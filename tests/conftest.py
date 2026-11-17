"""Shared fixtures: a fake BOE that answers from responses recorded in November 2026."""

import datetime as dt
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from boe_mcp.boe_client import BASE_URL

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 10, 12)

# Path under the API base URL -> recorded response.
ROUTES = {
    "boe/sumario/20261002": "summary-20261002.xml",
    "boe/sumario/20261012": "summary-20261012.xml",
    "legislacion-consolidada": "search-proteccion-datos.xml",
    "legislacion-consolidada/id/BOE-A-2018-16673/metadatos": "metadata-BOE-A-2018-16673.xml",
    "legislacion-consolidada/id/BOE-A-2018-16673/analisis": "analysis-BOE-A-2018-16673.xml",
    "legislacion-consolidada/id/BOE-A-2018-16673/texto/indice": "outline-BOE-A-2018-16673.xml",
    "legislacion-consolidada/id/BOE-A-2018-16673/texto/bloque/a6": "block-BOE-A-2018-16673-a6.xml",
    "legislacion-consolidada/id/BOE-A-2018-16673/texto/bloque/a2-5": (
        "block-BOE-A-2018-16673-a2-5.xml"
    ),
}


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_data(name: str) -> ET.Element:
    """The `data` node of a recorded response."""
    data = ET.fromstring(fixture_text(name)).find("data")
    assert data is not None
    return data


class FakeBoe:
    """Answers like the BOE from recorded responses and remembers what it was asked."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(httpx.URL(BASE_URL).path)
        name = ROUTES.get(path)
        if name is None:
            return httpx.Response(404, text=fixture_text("block-not-found.xml"))
        return httpx.Response(200, text=fixture_text(name))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_boe() -> FakeBoe:
    return FakeBoe()


@pytest.fixture
def today() -> Callable[[], dt.date]:
    return lambda: TODAY
