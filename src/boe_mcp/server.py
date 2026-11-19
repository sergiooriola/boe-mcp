"""The MCP server: four read-only tools over the BOE open-data API."""

import asyncio
import datetime as dt
import logging
import re
import time
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import httpx
from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, HttpUrl

from boe_mcp import parsing
from boe_mcp.boe_client import (
    BoeClient,
    BoeError,
    BoeNotFoundError,
    BoeUnavailableError,
    build_search_query,
    new_http_client,
)
from boe_mcp.models import (
    BlockVersion,
    DailySummary,
    Legislation,
    LegislationBlock,
    SearchResults,
    SectionCode,
    Status,
)

logger = logging.getLogger(__name__)

MADRID = ZoneInfo("Europe/Madrid")
MAX_LATER_CHANGES = 30
# Claude Code moves a tool result over 50,000 characters out of the conversation into a file.
# With 200 outline blocks the largest answer seen, from the Ley Orgánica del Poder Judicial and
# its long headings, has about 33,000; the whole Código Civil, 2,444 blocks, would have 250,000.
OUTLINE_PAGE = 200
# A rule whose repeal is only scheduled is still in force today.
IN_FORCE_STATUSES = frozenset({Status.IN_FORCE, Status.REPEAL_SCHEDULED})
_HEADING_WORD = re.compile(r"\w+")

INSTRUCTIONS = """\
Read Spain's official gazette, the Boletín Oficial del Estado (BOE), from its open-data API.

- get_daily_summary: what the BOE published on a day (today by default).
- search_legislation: find a rule in the consolidated-legislation collection and get its id.
- get_legislation: whether a rule is in force, its outline and the later rules that changed it.
- read_legislation_block: the current wording of one article or other block of a rule.

To quote an article: search_legislation, then get_legislation with outline_contains set to the
article number alone (23 for article 23.1) to find its block_id, then read_legislation_block
with that block_id and find the section in its text. Answer with the BOE identifier and the
link, and say that consolidated texts are informative: only the signed PDF of the BOE is official.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def madrid_today() -> dt.date:
    """Today's date in Spain's peninsular time, the BOE's calendar."""
    return dt.datetime.now(MADRID).date()


@dataclass(frozen=True)
class AppContext:
    """What every tool call shares: the BOE client and the clock."""

    boe: BoeClient
    today: Callable[[], dt.date]


ToolContext = Context[AppContext]


def create_server(
    *,
    http_client: Callable[[], httpx.AsyncClient] = new_http_client,
    today: Callable[[], dt.date] = madrid_today,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
) -> MCPServer[AppContext]:
    """Build the server. Without `auth` it is meant for local use over stdio only."""

    @asynccontextmanager
    async def lifespan(_: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        async with http_client() as http:
            yield AppContext(boe=BoeClient(http), today=today)

    server: MCPServer[AppContext] = MCPServer(
        name="boe-mcp",
        title="BOE",
        description="Read-only access to Spain's Boletín Oficial del Estado.",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/sergiooriola/boe-mcp",
        version=version("boe-mcp"),
        lifespan=lifespan,
        auth=auth,
        token_verifier=token_verifier,
        # The tools never change while a client is connected, so there is nothing to listen for.
        # A subscriptions/listen stream stays open until the client leaves, and on Lambda it would
        # hold the function until its timeout. Not serving it also stops advertising listChanged.
        subscriptions=False,
    )

    @server.tool(title="BOE of the day", annotations=READ_ONLY)
    async def get_daily_summary(
        ctx: ToolContext,
        date: Annotated[
            dt.date | None,
            Field(description="Publication date. Defaults to today in Spain."),
        ] = None,
        sections: Annotated[
            list[SectionCode] | None,
            Field(
                description=(
                    "Only these sections. 1: general provisions (new laws and regulations); "
                    "2A: appointments; 2B: public-sector jobs; 3: other provisions (grants, "
                    "agreements); 4: courts; 5A: public procurement; 5B: other official notices; "
                    "5C: private notices; T: Constitutional Court. Defaults to all."
                )
            ),
        ] = None,
        title_contains: Annotated[
            str | None,
            Field(
                description="Only items whose title has every one of these words. "
                "Case and accents are ignored.",
                max_length=200,
            ),
        ] = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=100)] = 30,
    ) -> DailySummary:
        """List what the BOE published on one day, with links to the official PDFs.

        The answer always counts the items in every section, so a filtered or paged answer
        still shows what else that day holds. Sundays have no bulletin.
        """
        app = ctx.request_context.lifespan_context
        day = date or app.today()
        bulletin = await _run(
            "get_daily_summary",
            app.boe.summary(day),
            not_found=f"There is no BOE for {day:%Y-%m-%d}: the BOE is not published on "
            "Sundays, and a day's bulletin appears early that morning.",
        )
        wanted = _words(title_contains)
        matching = [
            item
            for item in bulletin.items
            if (not sections or item.section in sections)
            and all(word in _normalise(item.title) for word in wanted)
        ]
        end = offset + limit
        return DailySummary(
            date=bulletin.date,
            issues=bulletin.issues,
            sections=bulletin.sections,
            total_matching=len(matching),
            items=matching[offset:end],
            next_offset=end if end < len(matching) else None,
        )

    @server.tool(title="Search legislation", annotations=READ_ONLY)
    async def search_legislation(
        ctx: ToolContext,
        title_words: Annotated[
            str | None,
            Field(description="Words that must all appear in the title.", max_length=200),
        ] = None,
        text_words: Annotated[
            str | None,
            Field(description="Words that must all appear in the full text.", max_length=200),
        ] = None,
        official_number: Annotated[
            str | None,
            Field(description="Official number, such as 3/2018 or 39/2015.", max_length=40),
        ] = None,
        scope: Annotated[
            Literal["state", "regional"] | None,
            Field(description="State rules or regional (autonómicas) rules. Defaults to both."),
        ] = None,
        only_in_force: Annotated[
            bool, Field(description="Leave out rules the BOE marks as no longer in force.")
        ] = True,
        published_from: dt.date | None = None,
        published_to: dt.date | None = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=25)] = 10,
    ) -> SearchResults:
        """Search Spain's consolidated legislation: laws, royal decrees, orders and more.

        Give at least one of title_words, text_words or official_number. Results come newest
        publication first. The collection holds the most relevant state and regional rules,
        not every item ever published in the BOE.
        """
        app = ctx.request_context.lifespan_context
        try:
            query = build_search_query(
                title_words=title_words,
                text_words=text_words,
                official_number=official_number,
                scope=scope,
                only_in_force=only_in_force,
                published_from=published_from,
                published_to=published_to,
            )
        except ValueError as error:
            raise ToolError(str(error)) from error
        # One result more than asked for says whether there is another page.
        found = await _run(
            "search_legislation", app.boe.search(query, offset=offset, limit=limit + 1)
        )
        return SearchResults(
            results=found[:limit],
            next_offset=offset + limit if len(found) > limit else None,
        )

    @server.tool(title="Rule status, outline and changes", annotations=READ_ONLY)
    async def get_legislation(
        ctx: ToolContext,
        law_id: Annotated[
            str, Field(description="BOE identifier, such as BOE-A-2018-16673.", max_length=40)
        ],
        outline_contains: Annotated[
            str | None,
            Field(
                description="Only outline blocks whose heading has every one of these whole "
                "words. Case and accents are ignored. Headings differ between rules (Artículo 23, "
                "Art 1172) and do not number the sections of an article, so give the article "
                "number alone: 23 for article 23.1, or 1863 bis.",
                max_length=200,
            ),
        ] = None,
        outline_offset: Annotated[int, Field(ge=0)] = 0,
    ) -> Legislation:
        """Describe one consolidated rule: its status today, its outline and its later changes.

        Large rules have thousands of blocks, so the outline comes a page at a time and
        outline_contains narrows it to the blocks you need. Later changes come most recent
        first. Use the outline's block_id with read_legislation_block to read an article.
        """
        app = ctx.request_context.lifespan_context
        metadata, analysis, outline = await _run(
            "get_legislation",
            app.boe.legislation(law_id),
            not_found=f"{law_id} is not in the BOE's consolidated-legislation collection. "
            "Use search_legislation to find the identifier of the rule.",
        )
        status = parsing.status_on(metadata, app.today())
        wanted = _heading_words(outline_contains)
        matching = [entry for entry in outline if wanted <= _heading_words(entry.title)]
        end = outline_offset + OUTLINE_PAGE
        return Legislation(
            id=metadata.id,
            title=metadata.title,
            rank=metadata.rank,
            scope=metadata.scope,
            department=metadata.department,
            official_number=metadata.official_number,
            enacted=metadata.enacted,
            published=metadata.published,
            in_force_from=metadata.in_force_from,
            status=status,
            repeal_date=metadata.repeal_date,
            consolidation_status=metadata.consolidation_status,
            last_updated=metadata.last_updated,
            url=HttpUrl(metadata.url),
            eli_url=HttpUrl(metadata.eli_url) if metadata.eli_url else None,
            subjects=analysis.subjects,
            outline=matching[outline_offset:end],
            outline_total=len(matching),
            outline_next_offset=end if end < len(matching) else None,
            later_changes_total=len(analysis.later_changes),
            later_changes=analysis.later_changes[:MAX_LATER_CHANGES],
        )

    @server.tool(title="Read an article", annotations=READ_ONLY)
    async def read_legislation_block(
        ctx: ToolContext,
        law_id: Annotated[
            str, Field(description="BOE identifier, such as BOE-A-2018-16673.", max_length=40)
        ],
        block_id: Annotated[
            str,
            Field(description="block_id from the outline of get_legislation.", max_length=80),
        ],
    ) -> LegislationBlock:
        """Read the latest wording of one block of a consolidated rule, and whether it is in force.

        A block is an article, the preamble, an annex or a provision. The answer also gives any
        approved wording that takes effect later, and lists the earlier versions of the block
        with the rules that introduced them. Check in_force before presenting the text as law.
        """
        app = ctx.request_context.lifespan_context
        metadata, block = await _run(
            "read_legislation_block",
            asyncio.gather(app.boe.metadata(law_id), app.boe.block(law_id, block_id)),
            not_found=f"{law_id} has no block {block_id!r}. Call get_legislation for the "
            "outline, with outline_contains set to the article number, and use one of its "
            "block_id values.",
        )
        today = app.today()
        law_status = parsing.status_on(metadata, today)
        started = [entry for entry in block.versions if _in_force_by(entry[0], today)]
        upcoming = sorted(
            (entry for entry in block.versions if not _in_force_by(entry[0], today)),
            key=lambda entry: entry[0].in_force_from or entry[0].published,
        )
        current, wording = started[-1] if started else (None, "")
        text, text_cut = parsing.truncate(wording)
        upcoming_text, upcoming_cut = (
            parsing.truncate(upcoming[0][1]) if upcoming else (None, False)
        )
        return LegislationBlock(
            law_id=law_id,
            law_status=law_status,
            block_id=block_id,
            kind=block.kind,
            title=block.title,
            in_force=current is not None and law_status in IN_FORCE_STATUSES,
            current_version=current,
            text=text,
            upcoming_versions=[version for version, _ in upcoming],
            upcoming_text=upcoming_text,
            truncated=text_cut or upcoming_cut,
            previous_versions=[version for version, _ in started[:-1]],
            url=HttpUrl(f"https://www.boe.es/buscar/act.php?id={law_id}"),
        )

    return server


async def _run[T](tool: str, operation: Awaitable[T], *, not_found: str | None = None) -> T:
    """Await a BOE call, log how it went and turn failures into errors the model can act on.

    Only the tool name, the outcome and the duration are logged: never the arguments, which
    can name people, nor the BOE's answer.
    """
    started = time.perf_counter()
    outcome = "error"
    try:
        result = await operation
        outcome = "ok"
        return result
    except BoeNotFoundError as error:
        outcome = "not_found"
        raise ToolError(not_found or "The BOE has nothing at that address.") from error
    except BoeUnavailableError as error:
        outcome = "boe_unavailable"
        raise ToolError(f"{error} Try again in a few minutes.") from error
    except ValueError as error:
        outcome = "invalid_argument"
        if isinstance(error, parsing.ParseError):
            outcome = "unexpected_answer"
            logger.warning("BOE answer did not parse: %s", error)
            raise ToolError("The BOE answered in an unexpected format.") from error
        raise ToolError(f"{error} BOE identifiers look like BOE-A-2018-16673.") from error
    except BoeError as error:
        raise ToolError(str(error)) from error
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        logger.info("tool=%s outcome=%s duration_ms=%d", tool, outcome, elapsed_ms)


def _in_force_by(version: BlockVersion, today: dt.date) -> bool:
    return version.in_force_from is None or version.in_force_from <= today


def _words(value: str | None) -> list[str]:
    return _normalise(value).split() if value else []


def _heading_words(value: str | None) -> set[str]:
    """Whole words of a heading, so that 2 finds Artículo 2 but not Artículo 23 or 12."""
    return set(_HEADING_WORD.findall(_normalise(value))) if value else set()


def _normalise(value: str) -> str:
    """Lower case without accents, so that "educacion" finds "Educación"."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
