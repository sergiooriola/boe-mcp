import datetime as dt
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from boe_mcp.boe_client import BASE_URL
from boe_mcp.server import create_server, madrid_today

from .conftest import TODAY, FakeBoe

TOOLS = {"get_daily_summary", "search_legislation", "get_legislation", "read_legislation_block"}


@pytest.fixture
async def client(fake_boe: FakeBoe, today: Callable[[], dt.date]) -> AsyncIterator[Client]:
    server = create_server(http_client=fake_boe.client, today=today)
    async with Client(server, raise_exceptions=True) as connected:
        yield connected


async def _call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, _text(result)
    content = result.structured_content
    assert isinstance(content, dict)
    return content


async def _error(client: Client, tool: str, **arguments: Any) -> str:
    result = await client.call_tool(tool, arguments)
    assert result.is_error
    return _text(result)


def _text(result: CallToolResult) -> str:
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))


@pytest.mark.anyio
async def test_tools_are_read_only_and_describe_their_output(client: Client) -> None:
    tools = (await client.list_tools()).tools

    assert {tool.name for tool in tools} == TOOLS
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
        assert tool.description


@pytest.mark.anyio
async def test_daily_summary_defaults_to_today_and_counts_every_section(client: Client) -> None:
    summary = await _call(client, "get_daily_summary", limit=5)

    assert summary["date"] == TODAY.isoformat()
    assert summary["total_matching"] == 86
    assert len(summary["items"]) == 5
    assert summary["next_offset"] == 5
    assert sum(section["items"] for section in summary["sections"]) == 86
    assert "Agencia Estatal Boletín Oficial del Estado" in summary["notice"]


@pytest.mark.anyio
async def test_daily_summary_filters_by_section_and_title_ignoring_accents(
    client: Client,
) -> None:
    everything = await _call(client, "get_daily_summary", sections=["1", "3"], limit=100)
    word = next(
        item["title"].split()[0] for item in everything["items"] if item["title"].split()[0]
    )

    found = await _call(
        client, "get_daily_summary", sections=["1", "3"], title_contains=word.upper(), limit=100
    )

    assert found["items"]
    assert all(item["section"] in {"1", "3"} for item in found["items"])
    assert found["next_offset"] is None


@pytest.mark.anyio
async def test_daily_summary_of_a_day_without_bulletin(client: Client) -> None:
    message = await _error(client, "get_daily_summary", date="2026-10-11")

    assert "There is no BOE for 2026-10-11" in message


@pytest.mark.anyio
async def test_search_pages_with_one_extra_result(client: Client, fake_boe: FakeBoe) -> None:
    results = await _call(client, "search_legislation", title_words="protección datos", limit=2)

    assert len(results["results"]) == 2
    assert results["next_offset"] == 2
    params = fake_boe.requests[-1].url.params
    assert params["limit"] == "3"
    query = json.loads(params["query"])["query"]["query_string"]["query"]
    assert query.startswith("titulo:protección and titulo:datos")


@pytest.mark.anyio
async def test_search_without_words_explains_what_to_give(client: Client) -> None:
    message = await _error(client, "search_legislation", scope="state")

    assert "title_words" in message


@pytest.mark.anyio
async def test_get_legislation(client: Client) -> None:
    law = await _call(client, "get_legislation", law_id="BOE-A-2018-16673")

    assert law["status"] == "in_force"
    assert law["official_number"] == "3/2018"
    assert len(law["outline"]) == 169
    assert law["outline_total"] == 169
    assert law["outline_next_offset"] is None
    assert law["later_changes_total"] == 7
    assert law["later_changes"][0]["law_id"] == "BOE-A-2025-26698"
    assert "meramente informativo" in law["notice"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("contains", "block_ids"),
    [
        ("2", ["a2", "s2", "s2-2"]),  # not Artículo 12, 20 or 23
        ("ARTICULO 2", ["a2"]),
        ("artículo 23", ["a2-5"]),
        ("artículo 999", []),
    ],
)
async def test_outline_is_filtered_by_whole_words_of_the_heading(
    client: Client, contains: str, block_ids: list[str]
) -> None:
    law = await _call(
        client, "get_legislation", law_id="BOE-A-2018-16673", outline_contains=contains
    )

    assert [entry["block_id"] for entry in law["outline"]] == block_ids
    assert law["outline_total"] == len(block_ids)


@pytest.mark.anyio
async def test_outline_comes_a_page_at_a_time(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("boe_mcp.server.OUTLINE_PAGE", 100)

    first = await _call(client, "get_legislation", law_id="BOE-A-2018-16673")
    last = await _call(client, "get_legislation", law_id="BOE-A-2018-16673", outline_offset=100)

    assert (len(first["outline"]), first["outline_total"], first["outline_next_offset"]) == (
        100,
        169,
        100,
    )
    assert (len(last["outline"]), last["outline_next_offset"]) == (69, None)
    assert first["outline"][-1] != last["outline"][0]


@pytest.mark.anyio
async def test_get_legislation_outside_the_collection(client: Client) -> None:
    message = await _error(client, "get_legislation", law_id="BOE-A-2099-1")

    assert "search_legislation" in message


@pytest.mark.anyio
async def test_invalid_identifier_is_explained(client: Client, fake_boe: FakeBoe) -> None:
    message = await _error(client, "get_legislation", law_id="../../etc/passwd")

    assert "BOE-A-2018-16673" in message
    assert fake_boe.requests == []


@pytest.mark.anyio
async def test_read_block_returns_the_current_wording_and_its_history(client: Client) -> None:
    block = await _call(
        client, "read_legislation_block", law_id="BOE-A-2018-16673", block_id="a2-5"
    )

    assert block["title"] == "Artículo 23"
    assert block["law_status"] == "in_force"
    assert block["in_force"] is True
    assert block["current_version"]["introduced_by"] == "BOE-A-2025-26698"
    assert [version["introduced_by"] for version in block["previous_versions"]] == [
        "BOE-A-2018-16673"
    ]
    assert block["text"].startswith("Artículo 23.")
    assert block["upcoming_versions"] == []
    assert block["upcoming_text"] is None
    assert block["truncated"] is False
    assert block["url"] == "https://www.boe.es/buscar/act.php?id=BOE-A-2018-16673"


@pytest.mark.anyio
async def test_a_repealed_rule_keeps_its_wording_but_it_is_not_in_force(client: Client) -> None:
    # Ley 30/1992 was repealed on 2021-04-02.
    block = await _call(client, "read_legislation_block", law_id="BOE-A-1992-26318", block_id="a1")

    assert block["law_status"] == "repealed"
    assert block["in_force"] is False
    assert block["current_version"]["introduced_by"] == "BOE-A-1992-26318"
    assert block["text"]


@pytest.mark.anyio
async def test_a_repealed_article_of_a_rule_in_force_is_not_in_force(client: Client) -> None:
    # Ley Orgánica 13/2015 repealed article 387 of the Ley de Enjuiciamiento Criminal.
    block = await _call(client, "read_legislation_block", law_id="BOE-A-1882-6036", block_id="a387")

    assert block["law_status"] == "in_force"
    assert block["in_force"] is False
    assert block["current_version"]["repealed"] is True
    assert block["previous_versions"][0]["repealed"] is False
    assert "(Derogado)" in block["text"]


@pytest.mark.anyio
async def test_a_lapsed_article_is_not_in_force_although_it_keeps_its_wording(
    client: Client,
) -> None:
    # Ley 14/2014 repealed the maritime articles of the Código de Comercio; the BOE only marks
    # the blocks as lapsed, without a version that says so.
    block = await _call(client, "read_legislation_block", law_id="BOE-A-1885-6627", block_id="a573")

    assert block["in_force"] is False
    assert block["lapsed_on"] == "2014-07-25"
    assert block["current_version"]["repealed"] is False
    assert block["text"].startswith("Art. 573.")


@pytest.mark.anyio
@pytest.mark.parametrize(("today", "in_force"), [(dt.date(2021, 7, 1), True), (TODAY, False)])
async def test_an_article_holds_until_its_repeal_takes_effect(
    fake_boe: FakeBoe, today: dt.date, in_force: bool
) -> None:
    # Ley 8/2021, published on 2021-06-03, repealed article 301 of the Código Civil from
    # 2021-09-03. The BOE marks the block as lapsed from the publication date.
    server = create_server(http_client=fake_boe.client, today=lambda: today)
    async with Client(server, raise_exceptions=True) as client:
        block = await _call(
            client, "read_legislation_block", law_id="BOE-A-1889-4763", block_id="art301"
        )

    assert block["lapsed_on"] == "2021-06-03"
    assert block["in_force"] is in_force
    repeal = block["current_version"] if not in_force else block["upcoming_versions"][0]
    assert repeal == {
        "introduced_by": "BOE-A-2021-9233",
        "published": "2021-06-03",
        "in_force_from": "2021-09-03",
        "repealed": True,
    }


@pytest.mark.anyio
async def test_search_words_never_reach_the_logs(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    await _call(client, "search_legislation", title_words="Fulanita Menganez")

    assert "Fulanita" not in caplog.text
    assert "tool=search_legislation outcome=ok" in caplog.text


@pytest.mark.anyio
async def test_read_block_before_an_amendment_takes_effect(fake_boe: FakeBoe) -> None:
    # Ley 10/2025 rewrote article 23 with effect from 2025-12-28.
    server = create_server(http_client=fake_boe.client, today=lambda: dt.date(2025, 12, 27))
    async with Client(server, raise_exceptions=True) as client:
        block = await _call(
            client, "read_legislation_block", law_id="BOE-A-2018-16673", block_id="a2-5"
        )

    assert block["current_version"]["introduced_by"] == "BOE-A-2018-16673"
    assert block["previous_versions"] == []
    assert [version["introduced_by"] for version in block["upcoming_versions"]] == [
        "BOE-A-2025-26698"
    ]
    assert block["upcoming_text"].startswith("Artículo 23.")
    assert block["text"] != block["upcoming_text"]


@pytest.mark.anyio
async def test_read_block_that_does_not_exist_yet(fake_boe: FakeBoe) -> None:
    server = create_server(http_client=fake_boe.client, today=lambda: dt.date(2018, 12, 6))
    async with Client(server, raise_exceptions=True) as client:
        block = await _call(
            client, "read_legislation_block", law_id="BOE-A-2018-16673", block_id="a6"
        )

    assert block["current_version"] is None
    assert block["in_force"] is False
    assert block["text"] == ""
    assert block["upcoming_versions"][0]["in_force_from"] == "2018-12-07"


@pytest.mark.anyio
async def test_read_missing_block_points_to_the_outline(client: Client) -> None:
    message = await _error(
        client, "read_legislation_block", law_id="BOE-A-2018-16673", block_id="zz"
    )

    assert "get_legislation" in message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (503, "", "Try again in a few minutes"),
        (200, "<response/>", "unexpected format"),
        (403, "", "rejected the request"),
    ],
)
async def test_boe_failures_reach_the_model_as_tool_errors(
    today: Callable[[], dt.date], status: int, body: str, expected: str
) -> None:
    def failing() -> httpx.AsyncClient:
        transport = httpx.MockTransport(lambda _: httpx.Response(status, text=body))
        return httpx.AsyncClient(base_url=BASE_URL, transport=transport)

    async with Client(create_server(http_client=failing, today=today)) as client:
        message = await _error(client, "get_daily_summary")

    assert expected in message


def test_madrid_today_is_a_date() -> None:
    assert isinstance(madrid_today(), dt.date)
