"""Client for the BOE open-data API.

Endpoints and formats: https://www.boe.es/datosabiertos/api/api.php. Every request names this
project in its User-Agent, as the BOE's reuse conditions ask of reusers, and only uses paths
that the BOE's robots.txt allows.
"""

import asyncio
import datetime as dt
import json
import logging
import re
import xml.etree.ElementTree as ET
from importlib.metadata import version

import httpx
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

from boe_mcp import parsing
from boe_mcp.models import LegislationResult, OutlineEntry

BASE_URL = "https://www.boe.es/datosabiertos/api/"
USER_AGENT = f"boe-mcp/{version('boe-mcp')} (+https://github.com/sergiooriola/boe-mcp)"
TIMEOUT_SECONDS = 15.0

# Identifiers such as BOE-A-2018-16673 or BOCL-h-2018-90415, and block ids such as a2-5. They
# go into URL paths, so anything else is rejected before a request is made.
LAW_ID = re.compile(r"^[A-Z]{2,6}-[A-Za-z]-\d{4}-\d{1,6}$")
BLOCK_ID = re.compile(r"^[A-Za-z0-9]{1,20}(-[A-Za-z0-9]{1,20}){0,3}$")

# httpx logs every request URL at INFO, and search URLs carry the words the user searched for,
# which can name people. Its warnings and errors still get through.
logging.getLogger("httpx").setLevel(logging.WARNING)

_WORD = re.compile(r"[\w/.-]+")
_OPERATORS = frozenset({"and", "or", "not"})
MAX_QUERY_WORDS = 10


class BoeError(Exception):
    """The BOE could not answer the request."""


class BoeNotFoundError(BoeError):
    """The BOE has nothing at that address: no bulletin that day, or no such rule or block."""


class BoeUnavailableError(BoeError):
    """The BOE did not answer in time or failed on its side; trying later may work."""


def new_http_client() -> httpx.AsyncClient:
    """Create an HTTP client configured for the BOE API."""
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Accept": "application/xml", "User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )


def build_search_query(
    *,
    title_words: str | None,
    text_words: str | None,
    official_number: str | None,
    scope: str | None,
    only_in_force: bool,
    published_from: dt.date | None,
    published_to: dt.date | None,
) -> str:
    """Build the JSON `query` parameter of the consolidated-legislation search.

    The BOE's search language joins `field:value` pairs with and, or and not. User input is cut
    into words and each word becomes its own `field:word` condition, so input can never add an
    operator, a parenthesis or another field to the query.
    """
    conditions = [
        *(f"titulo:{word}" for word in _words(title_words)),
        *(f"texto:{word}" for word in _words(text_words)),
        *(f"numero_oficial:{word}" for word in _words(official_number)),
    ]
    if not conditions:
        raise ValueError("Give at least one of title_words, text_words or official_number.")
    if scope is not None:
        conditions.append(f"ambito@codigo:{1 if scope == 'state' else 2}")
    if only_in_force:
        conditions.append("vigencia_agotada:N")
    query: dict[str, object] = {"query_string": {"query": " and ".join(conditions)}}
    if published_from is not None or published_to is not None:
        # The BOE's documentation says either end of a range can be left out, but the API
        # ignores a range with one end only, so an open end is sent as the first or last date.
        start = published_from or dt.date.min
        end = published_to or dt.date.max
        query["range"] = {"fecha_publicacion": {"gte": f"{start:%Y%m%d}", "lte": f"{end:%Y%m%d}"}}
    return json.dumps({"query": query, "sort": [{"fecha_publicacion": "desc"}]})


def _words(value: str | None) -> list[str]:
    if not value:
        return []
    words = [
        word.strip("/.-")
        for word in _WORD.findall(value.replace("_", " "))
        if word.lower() not in _OPERATORS
    ]
    return [word for word in words if word][:MAX_QUERY_WORDS]


class BoeClient:
    """Typed access to the parts of the BOE API that the tools use."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def summary(self, date: dt.date) -> parsing.Bulletin:
        """Every item the BOE published on `date`."""
        return parsing.parse_summary(await self._get(f"boe/sumario/{date:%Y%m%d}"))

    async def search(self, query: str, *, offset: int, limit: int) -> list[LegislationResult]:
        """Rules in the consolidated collection that match `query`."""
        params: dict[str, str | int] = {"query": query, "offset": offset, "limit": limit}
        return parsing.parse_search(await self._get("legislacion-consolidada", params=params))

    async def metadata(self, law_id: str) -> parsing.Metadata:
        """Metadata of a rule: what it is and the dates that decide whether it is in force."""
        path = f"legislacion-consolidada/id/{_checked(LAW_ID, law_id)}/metadatos"
        return parsing.parse_metadata(await self._get(path))

    async def legislation(
        self, law_id: str
    ) -> tuple[parsing.Metadata, parsing.Analysis, list[OutlineEntry]]:
        """Metadata, analysis and outline of a rule, requested together."""
        base = f"legislacion-consolidada/id/{_checked(LAW_ID, law_id)}"
        metadata, analysis, outline = await asyncio.gather(
            self._get(f"{base}/metadatos"),
            self._get(f"{base}/analisis"),
            self._get(f"{base}/texto/indice"),
        )
        return (
            parsing.parse_metadata(metadata),
            parsing.parse_analysis(analysis),
            parsing.parse_outline(outline),
        )

    async def block(self, law_id: str, block_id: str) -> parsing.Block:
        """One block of a rule's consolidated text, with every version."""
        path = (
            f"legislacion-consolidada/id/{_checked(LAW_ID, law_id)}"
            f"/texto/bloque/{_checked(BLOCK_ID, block_id)}"
        )
        return parsing.parse_block(await self._get(path))

    async def _get(self, path: str, params: dict[str, str | int] | None = None) -> ET.Element:
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as error:
            raise BoeUnavailableError("The BOE did not answer in time.") from error
        except httpx.TransportError as error:
            raise BoeUnavailableError("Could not reach the BOE.") from error
        if response.status_code == httpx.codes.NOT_FOUND:
            raise BoeNotFoundError(_status_text(response) or "Not found.")
        if response.is_server_error:
            raise BoeUnavailableError(f"The BOE failed with HTTP {response.status_code}.")
        if response.is_error:
            reason = _status_text(response) or f"HTTP {response.status_code}"
            raise BoeError(f"The BOE rejected the request: {reason}.")
        try:
            root = SafeET.fromstring(response.content)
        except (ET.ParseError, DefusedXmlException) as error:
            raise parsing.ParseError("The BOE answered with malformed or unsafe XML.") from error
        data = root.find("data")
        if data is None:
            raise parsing.ParseError("The BOE answer has no <data> node.")
        return data


def _checked(pattern: re.Pattern[str], value: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"Not a valid identifier: {value!r}.")
    return value


def _status_text(response: httpx.Response) -> str | None:
    try:
        return SafeET.fromstring(response.content).findtext("status/text")
    except ET.ParseError, DefusedXmlException:
        return None
