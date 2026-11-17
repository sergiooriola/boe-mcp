import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from starlette.applications import Starlette

from boe_mcp.http_app import HttpSettings, create_app

from .conftest import FakeBoe

ISSUER = "https://cognito-idp.eu-south-2.amazonaws.com/eu-south-2_TEST"
RESOURCE = "http://testserver/mcp"
SCOPE = f"{RESOURCE}/read"
CLIENT_ID = "claude-code-client"
KEY_ID = "test-key"

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticJwks(jwt.PyJWKClient):
    """A JWKS client that serves our test key instead of fetching Cognito's."""

    def __init__(self) -> None:
        super().__init__(f"{ISSUER}/.well-known/jwks.json")

    def fetch_data(self) -> Any:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": KEY_ID, "alg": "RS256", "use": "sig"}]}


def token(key: Any = PRIVATE_KEY, algorithm: str = "RS256", **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "user-1",
        "client_id": CLIENT_ID,
        "token_use": "access",
        "scope": SCOPE,
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": KEY_ID})


SETTINGS = HttpSettings.model_validate(
    {
        "resource_url": RESOURCE,
        "issuer_url": ISSUER,
        "client_ids": frozenset({CLIENT_ID}),
        "required_scope": SCOPE,
    }
)


@pytest.fixture
async def app(fake_boe: FakeBoe) -> AsyncIterator[Starlette]:
    application = create_app(SETTINGS, http_client=fake_boe.client, jwks=StaticJwks())
    async with application.router.lifespan_context(application):
        yield application


def http(app: Starlette, bearer: str | None = None, host: str = "testserver") -> Any:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=f"http://{host}", headers=headers
    )


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


async def post(
    app: Starlette, bearer: str | None, host: str = "testserver", origin: str | None = None
) -> Any:
    headers = {**MCP_HEADERS, **({"Origin": origin} if origin else {})}
    async with http(app, bearer, host) as client:
        return await client.post("/mcp", json=INITIALIZE, headers=headers)


@pytest.mark.anyio
async def test_a_real_client_lists_and_calls_tools_with_a_valid_token(app: Starlette) -> None:
    async with (
        http(app, token()) as client,
        Client(streamable_http_client(RESOURCE, http_client=client)) as mcp,
    ):
        tools = await mcp.list_tools()
        result = await mcp.call_tool("get_legislation", {"law_id": "BOE-A-2018-16673"})

    assert len(tools.tools) == 4
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["official_number"] == "3/2018"


@pytest.mark.anyio
async def test_without_a_token_the_answer_points_to_the_resource_metadata(app: Starlette) -> None:
    response = await post(app, bearer=None)

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer")
    assert 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"' in (
        challenge
    )


@pytest.mark.anyio
async def test_resource_metadata_names_cognito_and_the_scope(app: Starlette) -> None:
    async with http(app) as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    metadata = response.json()
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [ISSUER]
    assert metadata["scopes_supported"] == [SCOPE]


@pytest.mark.parametrize(
    "bad_token",
    [
        pytest.param(lambda: token(aud="https://another-server.example/mcp"), id="other audience"),
        pytest.param(lambda: token(aud=None), id="no audience"),
        pytest.param(lambda: token(iss="https://evil.example"), id="other issuer"),
        pytest.param(lambda: token(exp=int(time.time()) - 60), id="expired"),
        pytest.param(lambda: token(token_use="id"), id="id token"),
        pytest.param(lambda: token(client_id="someone-else"), id="unknown client"),
        pytest.param(lambda: token(key=OTHER_KEY), id="wrong signature"),
        pytest.param(lambda: token(key="shared-secret-" * 4, algorithm="HS256"), id="hs256"),
        pytest.param(lambda: "not-a-jwt", id="garbage"),
    ],
)
@pytest.mark.anyio
async def test_invalid_tokens_are_refused(app: Starlette, bad_token: Callable[[], str]) -> None:
    response = await post(app, bad_token())

    assert response.status_code == 401


@pytest.mark.anyio
async def test_a_token_without_the_scope_is_forbidden(app: Starlette) -> None:
    response = await post(app, token(scope="openid email"))

    assert response.status_code == 403
    assert "insufficient_scope" in response.headers["www-authenticate"]


@pytest.mark.anyio
async def test_a_valid_token_for_a_legacy_client_is_served(app: Starlette) -> None:
    response = await post(app, token())

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "boe-mcp"


@pytest.mark.anyio
async def test_requests_to_another_host_name_are_refused(app: Starlette) -> None:
    response = await post(app, token(), host="attacker.example")

    assert response.status_code == 421


@pytest.mark.anyio
async def test_only_our_own_web_origin_is_accepted(app: Starlette) -> None:
    own = await post(app, token(), origin="http://testserver")
    other = await post(app, token(), origin="https://attacker.example")

    assert own.status_code == 200
    assert other.status_code == 403


@pytest.mark.anyio
async def test_there_is_no_get_stream(app: Starlette) -> None:
    async with http(app, token()) as client:
        response = await client.get(
            "/mcp",
            headers={"Accept": "text/event-stream", "MCP-Protocol-Version": "2025-11-25"},
        )

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


@pytest.mark.anyio
async def test_there_are_no_listen_streams(app: Starlette) -> None:
    async with (
        http(app, token()) as client,
        Client(streamable_http_client(RESOURCE, http_client=client)) as mcp,
    ):
        tools = mcp.server_capabilities.tools
        with anyio.fail_after(5), pytest.raises(MCPError):
            async with mcp.listen(tools_list_changed=True):
                pass

    assert tools is not None
    assert not tools.list_changed


@pytest.mark.anyio
async def test_health_check_needs_no_token(app: Starlette) -> None:
    async with http(app) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.text == "ok"


def test_settings_come_from_the_environment() -> None:
    settings = HttpSettings.from_env(
        {
            "BOE_MCP_RESOURCE_URL": "https://abc.execute-api.eu-south-2.amazonaws.com/mcp",
            "BOE_MCP_ISSUER_URL": ISSUER,
            "BOE_MCP_CLIENT_IDS": "one,two,",
            "BOE_MCP_REQUIRED_SCOPE": "scope",
        }
    )

    assert settings.client_ids == {"one", "two"}
    assert str(settings.resource_url) == "https://abc.execute-api.eu-south-2.amazonaws.com/mcp"


def test_missing_settings_stop_the_server_from_starting() -> None:
    with pytest.raises(ValidationError):
        HttpSettings.from_env({})


@pytest.mark.parametrize(
    "resource_url", ["https://example.com", "https://example.com/", "https://example.com/mcp/"]
)
def test_the_resource_url_must_be_an_endpoint_without_trailing_slash(resource_url: str) -> None:
    with pytest.raises(ValidationError, match="endpoint path"):
        HttpSettings.model_validate({**SETTINGS.model_dump(), "resource_url": resource_url})
