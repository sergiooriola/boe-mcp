"""The four tools against the live BOE API, to notice when the API changes under the fixtures.

The other tests answer from recorded responses, so they keep passing if the BOE changes its
format. These ask the real API about published facts that do not change, through the same MCP
calls a client makes. They are skipped by default; `pytest -m live` runs them, and so does a
weekly workflow.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import Client
from mcp.types import TextContent

from boe_mcp.server import create_server

pytestmark = [pytest.mark.live, pytest.mark.anyio]

DATA_PROTECTION_ACT = "BOE-A-2018-16673"  # Ley Orgánica 3/2018
AMENDING_ACT = "BOE-A-2025-26698"  # Ley 10/2025, which rewrote its article 23
REPEALED_ACT = "BOE-A-1992-26318"  # Ley 30/1992, repealed in 2021
CIVIL_CODE = "BOE-A-1889-4763"  # 1889, with about 2,400 blocks and an untitled preamble
CRIMINAL_PROCEDURE_ACT = "BOE-A-1882-6036"  # Ley de Enjuiciamiento Criminal


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    async with Client(create_server(), raise_exceptions=True) as connected:
        yield connected


async def _call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, _text(result.content)
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(content: list[Any]) -> str:
    return " ".join(block.text for block in content if isinstance(block, TextContent))


async def test_a_past_bulletin_is_read_whole(client: Client) -> None:
    summary = await _call(client, "get_daily_summary", date="2026-10-12", limit=100)

    assert summary["total_matching"] == 86
    assert sum(section["items"] for section in summary["sections"]) == 86
    assert all(item["id"].startswith("BOE-") for item in summary["items"])
    assert all(item["pdf_url"].startswith("https://www.boe.es/") for item in summary["items"])


async def test_a_sunday_has_no_bulletin(client: Client) -> None:
    result = await client.call_tool("get_daily_summary", {"date": "2026-10-11"})

    assert result.is_error
    assert "There is no BOE for 2026-10-11" in _text(result.content)


async def test_a_rule_is_found_by_its_official_number(client: Client) -> None:
    found = await _call(client, "search_legislation", official_number="3/2018", only_in_force=False)

    assert DATA_PROTECTION_ACT in {result["id"] for result in found["results"]}


async def test_search_with_only_an_end_date_filters_by_it(client: Client) -> None:
    found = await _call(
        client, "search_legislation", title_words="Código Civil", published_to="1890-12-31"
    )

    assert [result["id"] for result in found["results"]] == ["BOE-A-1889-4763"]


async def test_a_rule_has_status_outline_and_changes(client: Client) -> None:
    law = await _call(client, "get_legislation", law_id=DATA_PROTECTION_ACT)

    assert law["official_number"] == "3/2018"
    assert law["status"] == "in_force"
    assert any(entry["block_id"] == "a2-5" for entry in law["outline"])
    assert AMENDING_ACT in {change["law_id"] for change in law["later_changes"]}


async def test_an_old_code_with_untitled_blocks_has_its_outline(client: Client) -> None:
    law = await _call(client, "get_legislation", law_id=CIVIL_CODE)
    titles = {entry["block_id"]: entry["title"] for entry in law["outline"]}

    assert law["status"] == "in_force"
    assert titles["preambulo"] == ""
    assert titles["art1172"] == "Art 1172"


async def test_an_article_has_its_wording_and_history(client: Client) -> None:
    block = await _call(
        client, "read_legislation_block", law_id=DATA_PROTECTION_ACT, block_id="a2-5"
    )
    versions = [block["current_version"], *block["previous_versions"]]

    assert block["title"] == "Artículo 23"
    assert block["in_force"] is True
    assert block["text"].startswith("Artículo 23.")
    assert {AMENDING_ACT, DATA_PROTECTION_ACT} <= {v["introduced_by"] for v in versions}


async def test_a_block_id_with_punctuation_is_read(client: Client) -> None:
    block = await _call(
        client, "read_legislation_block", law_id=CRIMINAL_PROCEDURE_ACT, block_id="a846bisa)"
    )

    assert block["title"] == "Artículo 846 bis a)"
    assert block["text"].startswith("Artículo 846 bis a)")


async def test_a_repealed_rule_is_not_in_force(client: Client) -> None:
    block = await _call(client, "read_legislation_block", law_id=REPEALED_ACT, block_id="a1")

    assert block["law_status"] == "repealed"
    assert block["in_force"] is False
