"""The server over Streamable HTTP, protected with OAuth: what runs on AWS Lambda."""

import os
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

import httpx
import jwt
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from boe_mcp.auth import CognitoTokenVerifier
from boe_mcp.boe_client import new_http_client
from boe_mcp.server import create_server

ENV_PREFIX = "BOE_MCP_"
HEALTH_PATH = "/healthz"


class HttpSettings(BaseModel):
    """Configuration of the HTTP server, read from BOE_MCP_* environment variables."""

    resource_url: AnyHttpUrl = Field(
        description="Public URL of the MCP endpoint, exactly as clients use it. It is also the "
        "audience that tokens must be issued for."
    )
    issuer_url: AnyHttpUrl = Field(description="Cognito user pool issuer.")
    client_ids: frozenset[str] = Field(min_length=1, description="App clients allowed in.")
    required_scope: str = Field(min_length=1, description="Scope every token must carry.")

    @field_validator("resource_url")
    @classmethod
    def _endpoint_path(cls, url: AnyHttpUrl) -> AnyHttpUrl:
        # The URL is compared character by character with each token's audience, so it has one
        # spelling only: an endpoint path such as /mcp, without a trailing slash.
        if not url.path or url.path == "/" or url.path.endswith("/"):
            raise ValueError("must name the MCP endpoint path, such as /mcp, with no trailing /")
        return url

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> HttpSettings:
        """Read the settings from the environment; client ids are comma-separated."""

        def get(name: str) -> str:
            return environ.get(f"{ENV_PREFIX}{name}", "")

        return cls.model_validate(
            {
                "resource_url": get("RESOURCE_URL"),
                "issuer_url": get("ISSUER_URL"),
                "client_ids": frozenset(filter(None, get("CLIENT_IDS").split(","))),
                "required_scope": get("REQUIRED_SCOPE"),
            }
        )


def create_app(
    settings: HttpSettings,
    *,
    http_client: Callable[[], httpx.AsyncClient] = new_http_client,
    jwks: jwt.PyJWKClient | None = None,
) -> Starlette:
    """Build the ASGI app: the MCP endpoint, its OAuth metadata and a health check."""
    resource = str(settings.resource_url)
    issuer = str(settings.issuer_url).rstrip("/")
    server = create_server(
        http_client=http_client,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(resource),
            required_scopes=[settings.required_scope],
            validate_token_resource=True,
        ),
        token_verifier=CognitoTokenVerifier(
            issuer=issuer, resource=resource, client_ids=settings.client_ids, jwks=jwks
        ),
    )

    url = urlsplit(resource)
    app = server.streamable_http_app(
        streamable_http_path=url.path,
        # One JSON answer per request and no session state, so any Lambda instance can serve
        # any request.
        json_response=True,
        stateless_http=True,
        # Only requests addressed to our own host name, and from no other web origin than our
        # own: protection against DNS rebinding.
        transport_security=TransportSecuritySettings(
            allowed_hosts=[url.netloc], allowed_origins=[f"{url.scheme}://{url.netloc}"]
        ),
    )
    # Older clients may open a GET stream for server messages. A stateless server has none to
    # send, and an open stream would keep a Lambda invocation running until it times out, so
    # the endpoint answers GET with 405, which tells the client there is no stream.
    app.router.routes.insert(0, Route(url.path, _no_stream, methods=["GET"]))
    app.router.routes.append(Route(HEALTH_PATH, _health, methods=["GET"]))
    return app


async def _no_stream(_: Request) -> Response:
    """Refuse the optional GET stream of older clients."""
    return Response(status_code=405, headers={"Allow": "POST"})


async def _health(_: Request) -> PlainTextResponse:
    """Readiness check for the Lambda Web Adapter. It needs no token and reveals nothing."""
    return PlainTextResponse("ok")
