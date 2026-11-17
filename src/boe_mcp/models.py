"""Data returned by the tools.

Each model is the structured output of a tool, so its field descriptions end up in the tool's
output schema and are read by the model that calls the tool.
"""

import datetime as dt
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

SOURCE_NOTICE = (
    "Basado en datos de la Agencia Estatal Boletín Oficial del Estado (https://www.boe.es). "
    "Solo la versión electrónica firmada del BOE, en PDF, es oficial y auténtica."
)
CONSOLIDATED_NOTICE = (
    "Basado en datos de la Agencia Estatal Boletín Oficial del Estado (https://www.boe.es). "
    "Texto consolidado de carácter meramente informativo: solo la versión electrónica firmada "
    "del BOE, en PDF, es oficial y auténtica."
)

SectionCode = Literal["1", "2A", "2B", "3", "4", "5A", "5B", "5C", "T"]


class SummaryItem(BaseModel):
    """One provision or notice in the daily summary."""

    id: str = Field(description="BOE identifier, such as BOE-A-2026-24003.")
    title: str
    section: SectionCode
    department: str = Field(description="Issuing body, as printed in the summary.")
    heading: str | None = Field(
        default=None, description="Subject heading the item is grouped under, if any."
    )
    pdf_url: HttpUrl = Field(description="Signed PDF: the only official version.")
    html_url: HttpUrl | None = None


class SectionCount(BaseModel):
    """How many items a section of the day's bulletin holds."""

    code: SectionCode
    name: str
    items: int


class DailySummary(BaseModel):
    """What the BOE published on one day."""

    date: dt.date
    issues: list[int] = Field(
        description="Issue numbers published that day. More than one means an extraordinary issue."
    )
    sections: list[SectionCount] = Field(description="Every section of the day, with its size.")
    total_matching: int = Field(description="Items that match the filters, before paging.")
    items: list[SummaryItem]
    next_offset: int | None = Field(
        default=None, description="Pass it as offset to get the next page; null on the last page."
    )
    notice: str = SOURCE_NOTICE


class LegislationResult(BaseModel):
    """One rule found in the consolidated-legislation collection."""

    id: str = Field(description="Identifier to pass to get_legislation.")
    title: str
    rank: str = Field(description="Kind of rule: Ley, Ley Orgánica, Real Decreto, Orden…")
    scope: str = Field(description="Estatal or Autonómico.")
    department: str
    official_number: str | None = Field(default=None, description="Such as 3/2018.")
    published: dt.date
    in_force: bool = Field(description="False when the BOE marks it as no longer in force.")
    url: HttpUrl = Field(description="Consolidated text on boe.es.")


class SearchResults(BaseModel):
    """A page of search results, newest publication first."""

    results: list[LegislationResult]
    next_offset: int | None = Field(
        default=None, description="Pass it as offset to get the next page; null on the last page."
    )
    notice: str = CONSOLIDATED_NOTICE


class Status(StrEnum):
    """Whether a rule is in force on the day it is asked about."""

    IN_FORCE = "in_force"
    NOT_YET_IN_FORCE = "not_yet_in_force"
    REPEAL_SCHEDULED = "repeal_scheduled"
    REPEALED = "repealed"
    ANNULLED = "annulled"
    EXPIRED = "expired"


class OutlineEntry(BaseModel):
    """One block of a rule's text: an article, the preamble, an annex or a provision."""

    block_id: str = Field(description="Identifier to pass to read_legislation_block.")
    title: str
    updated: dt.date | None = Field(default=None, description="Last time this block changed.")


class LaterChange(BaseModel):
    """A later rule that changed this one, as recorded by the BOE."""

    law_id: str = Field(description="Identifier of the rule that made the change.")
    relation: str = Field(description="Kind of change, such as SE MODIFICA or SE DEROGA.")
    detail: str | None = Field(
        default=None,
        description="What was changed, as written by the BOE. Show it, don't parse it.",
    )


class Legislation(BaseModel):
    """A consolidated rule: what it is, whether it is in force, its outline and its changes."""

    id: str
    title: str
    rank: str
    scope: str
    department: str
    official_number: str | None = None
    enacted: dt.date | None = Field(default=None, description="Date of the rule itself.")
    published: dt.date
    in_force_from: dt.date | None = None
    status: Status
    repeal_date: dt.date | None = Field(
        default=None, description="Set when the rule is repealed or will be on that date."
    )
    consolidation_status: str = Field(description="Whether the BOE has finished consolidating it.")
    last_updated: dt.datetime = Field(description="Last update of the consolidated text (UTC).")
    url: HttpUrl
    eli_url: HttpUrl | None = Field(default=None, description="European Legislation Identifier.")
    subjects: list[str] = Field(description="Subject terms assigned by the BOE.")
    outline: list[OutlineEntry]
    later_changes_total: int
    later_changes: list[LaterChange] = Field(
        description="Most recent changes first, capped; later_changes_total says how many exist."
    )
    notice: str = CONSOLIDATED_NOTICE


class BlockVersion(BaseModel):
    """One version of a block: the original text or the text after an amendment."""

    introduced_by: str = Field(
        description="Rule that gave the block this wording; the rule itself for the original."
    )
    published: dt.date
    in_force_from: dt.date | None = None


class LegislationBlock(BaseModel):
    """The latest wording of one block of a consolidated rule, and whether it is in force."""

    law_id: str
    law_status: Status = Field(description="Status of the whole rule today.")
    block_id: str
    kind: str = Field(description="precepto, preambulo, parte_final, firma, nota_inicial…")
    title: str
    in_force: bool = Field(
        description="True only when the rule is in force and `text` has taken effect. A "
        "repealed rule keeps its last wording, which is no longer in force."
    )
    current_version: BlockVersion | None = Field(
        description="Latest version that has taken effect; null when the block only has "
        "wording that takes effect later."
    )
    text: str = Field(
        description="Wording of current_version as plain text; empty when there is none. "
        "Notes start with 'Nota:'."
    )
    upcoming_versions: list[BlockVersion] = Field(
        description="Approved wordings that take effect after today, soonest first."
    )
    upcoming_text: str | None = Field(
        default=None, description="Wording of the soonest upcoming version, if there is one."
    )
    truncated: bool = Field(description="True when a text was cut to keep the answer short.")
    previous_versions: list[BlockVersion] = Field(
        description="Versions no longer in force, oldest first."
    )
    url: HttpUrl
    notice: str = CONSOLIDATED_NOTICE
