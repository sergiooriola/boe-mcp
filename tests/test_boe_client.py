import datetime as dt
import json

import httpx
import pytest

from boe_mcp import parsing
from boe_mcp.boe_client import (
    BASE_URL,
    USER_AGENT,
    BoeClient,
    BoeError,
    BoeNotFoundError,
    BoeUnavailableError,
    build_search_query,
    new_http_client,
)

from .conftest import FakeBoe, fixture_text


def _query(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "title_words": None,
        "text_words": None,
        "official_number": None,
        "scope": None,
        "only_in_force": False,
        "published_from": None,
        "published_to": None,
    }
    arguments.update(overrides)
    return json.loads(build_search_query(**arguments))  # type: ignore[arg-type,no-any-return]


def test_query_joins_one_condition_per_word() -> None:
    query = _query(title_words="Protección de datos", official_number="3/2018", scope="state")

    assert query == {
        "query": {
            "query_string": {
                "query": "titulo:Protección and titulo:de and titulo:datos "
                "and numero_oficial:3/2018 and ambito@codigo:1"
            }
        },
        "sort": [{"fecha_publicacion": "desc"}],
    }


def test_query_filters_by_force_and_publication_dates() -> None:
    query = _query(
        text_words="becas",
        scope="regional",
        only_in_force=True,
        published_from=dt.date(2025, 1, 1),
        published_to=dt.date(2025, 12, 31),
    )

    assert query["query"] == {
        "query_string": {"query": "texto:becas and ambito@codigo:2 and vigencia_agotada:N"},
        "range": {"fecha_publicacion": {"gte": "20250101", "lte": "20251231"}},
    }


def test_query_input_cannot_add_operators_or_fields() -> None:
    query = _query(title_words='ley) or (rango@codigo:1300 NOT "x" _ -')

    assert query["query"] == {
        "query_string": {
            "query": "titulo:ley and titulo:rango and titulo:codigo and titulo:1300 and titulo:x"
        }
    }


def test_query_needs_a_search_word() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _query(title_words=" () ")


def test_http_client_sends_accept_and_user_agent() -> None:
    client = new_http_client()

    assert client.headers["Accept"] == "application/xml"
    assert client.headers["User-Agent"] == USER_AGENT
    assert str(client.base_url) == BASE_URL


@pytest.mark.anyio
async def test_legislation_requests_metadata_analysis_and_outline(fake_boe: FakeBoe) -> None:
    async with fake_boe.client() as http:
        metadata, analysis, outline = await BoeClient(http).legislation("BOE-A-2018-16673")

    assert metadata.id == "BOE-A-2018-16673"
    assert analysis.later_changes
    assert outline
    assert len(fake_boe.requests) == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("law_id", "block_id"),
    [("../../robots.txt", "a1"), ("BOE-A-2018-16673", "a1/../../x"), ("BOE-A-2018-16673?", "a1")],
)
async def test_identifiers_are_checked_before_any_request(
    fake_boe: FakeBoe, law_id: str, block_id: str
) -> None:
    async with fake_boe.client() as http:
        with pytest.raises(ValueError, match="Not a valid identifier"):
            await BoeClient(http).block(law_id, block_id)

    assert fake_boe.requests == []


def _client_answering(response: httpx.Response | Exception) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    return httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (httpx.Response(404, text=fixture_text("block-not-found.xml")), BoeNotFoundError, "existe"),
        (httpx.Response(404, text="not xml"), BoeNotFoundError, "Not found"),
        (httpx.Response(503), BoeUnavailableError, "HTTP 503"),
        (httpx.ReadTimeout("slow"), BoeUnavailableError, "in time"),
        (httpx.ConnectError("down"), BoeUnavailableError, "reach"),
        (
            httpx.Response(400, text="<response><status><text>Bad</text></status></response>"),
            BoeError,
            "Bad",
        ),
        (httpx.Response(403), BoeError, "HTTP 403"),
        (httpx.Response(200, text="<response"), parsing.ParseError, "malformed"),
        (httpx.Response(200, text="<response/>"), parsing.ParseError, "no <data>"),
    ],
)
async def test_failures_become_typed_errors(
    response: httpx.Response | Exception, error: type[Exception], message: str
) -> None:
    async with _client_answering(response) as http:
        with pytest.raises(error, match=message):
            await BoeClient(http).summary(dt.date(2026, 10, 12))
