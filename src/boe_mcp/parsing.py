"""Turn the BOE's XML responses into the models the tools return.

The BOE serves the same data in XML and JSON, but its JSON turns a node with one child into an
object and a node with several into a list, so XML is the shape that does not change.
"""

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from pydantic import TypeAdapter

from boe_mcp.models import (
    BlockVersion,
    LaterChange,
    LegislationResult,
    OutlineEntry,
    SectionCode,
    SectionCount,
    Status,
    SummaryItem,
)

MAX_BLOCK_CHARS = 20_000
IMAGE_OMITTED = "[Imagen omitida: consúltela en boe.es]"

_SECTION_CODE: TypeAdapter[SectionCode] = TypeAdapter(SectionCode)
_LAW_ID_ORDER = re.compile(r"-(\d{4})-(\d+)$")
_WHITESPACE = re.compile(r"\s+")


class ParseError(ValueError):
    """The BOE answered with XML that does not have the documented shape."""


@dataclass(frozen=True)
class Bulletin:
    """Every item of one day's bulletin, before any filtering."""

    date: dt.date
    issues: list[int]
    sections: list[SectionCount]
    items: list[SummaryItem]


@dataclass(frozen=True)
class Metadata:
    """The fields of a consolidated rule's metadata that the tools use."""

    id: str
    title: str
    rank: str
    scope: str
    department: str
    official_number: str | None
    enacted: dt.date | None
    published: dt.date
    in_force_from: dt.date | None
    repealed_flag: bool
    repeal_date: dt.date | None
    annulled: bool
    expired: bool
    consolidation_status: str
    last_updated: dt.datetime
    url: str
    eli_url: str | None


@dataclass(frozen=True)
class Analysis:
    """Subjects and later changes of a consolidated rule."""

    subjects: list[str]
    later_changes: list[LaterChange]


@dataclass(frozen=True)
class Block:
    """One block of a consolidated text, with every version."""

    kind: str
    title: str
    versions: list[tuple[BlockVersion, str]]


def parse_date(value: str) -> dt.date:
    """Parse a BOE date (AAAAMMDD)."""
    return dt.datetime.strptime(value, "%Y%m%d").date()


def parse_timestamp(value: str) -> dt.datetime:
    """Parse a BOE timestamp (AAAAMMDDTHHmmSSZ), which is in UTC."""
    return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)


def parse_summary(data: ET.Element) -> Bulletin:
    """Parse the `data` node of a daily summary."""
    summary = _child(data, "sumario")
    date = parse_date(_text(_child(summary, "metadatos"), "fecha_publicacion"))
    issues: list[int] = []
    counts: dict[SectionCode, SectionCount] = {}
    items: list[SummaryItem] = []
    for issue in summary.iter("diario"):
        issues.append(int(_attr(issue, "numero")))
        for section in issue.iter("seccion"):
            code = _SECTION_CODE.validate_python(_attr(section, "codigo"))
            section_items = [
                _summary_item(item, code, department, heading)
                for department, heading, item in _section_items(section)
            ]
            previous = counts.get(code)
            counts[code] = SectionCount(
                code=code,
                name=_attr(section, "nombre"),
                items=len(section_items) + (previous.items if previous else 0),
            )
            items.extend(section_items)
    return Bulletin(date=date, issues=issues, sections=list(counts.values()), items=items)


def _section_items(section: ET.Element) -> list[tuple[str, str | None, ET.Element]]:
    """Every item of a section with its department and, where there is one, its heading."""
    found: list[tuple[str, str | None, ET.Element]] = []
    for department in section.iter("departamento"):
        name = _attr(department, "nombre")
        for child in department:
            if child.tag == "item":
                found.append((name, None, child))
            elif child.tag == "epigrafe":
                found.extend((name, _attr(child, "nombre"), item) for item in child.iter("item"))
    return found


def _summary_item(
    item: ET.Element, section: SectionCode, department: str, heading: str | None
) -> SummaryItem:
    return SummaryItem.model_validate(
        {
            "id": _text(item, "identificador"),
            "title": _text(item, "titulo"),
            "section": section,
            "department": department,
            "heading": heading,
            "pdf_url": _text(item, "url_pdf"),
            "html_url": _optional_text(item, "url_html"),
        }
    )


def parse_search(data: ET.Element) -> list[LegislationResult]:
    """Parse the `data` node of a consolidated-legislation search."""
    return [
        LegislationResult.model_validate(
            {
                "id": _text(item, "identificador"),
                "title": _text(item, "titulo"),
                "rank": _text(item, "rango"),
                "scope": _text(item, "ambito"),
                "department": _text(item, "departamento"),
                "official_number": _optional_text(item, "numero_oficial"),
                "published": parse_date(_text(item, "fecha_publicacion")),
                "in_force": _text(item, "vigencia_agotada") == "N",
                "url": _text(item, "url_html_consolidada"),
            }
        )
        for item in data.iter("item")
    ]


def parse_metadata(data: ET.Element) -> Metadata:
    """Parse the `data` node of a rule's metadata."""
    node = _child(data, "metadatos")
    return Metadata(
        id=_text(node, "identificador"),
        title=_text(node, "titulo"),
        rank=_text(node, "rango"),
        scope=_text(node, "ambito"),
        department=_text(node, "departamento"),
        official_number=_optional_text(node, "numero_oficial"),
        enacted=_optional_date(node, "fecha_disposicion"),
        published=parse_date(_text(node, "fecha_publicacion")),
        in_force_from=_optional_date(node, "fecha_vigencia"),
        repealed_flag=_text(node, "estatus_derogacion") == "S",
        repeal_date=_optional_date(node, "fecha_derogacion"),
        annulled=_text(node, "estatus_anulacion") == "S",
        expired=_text(node, "vigencia_agotada") == "S",
        consolidation_status=_text(node, "estado_consolidacion"),
        last_updated=parse_timestamp(_text(node, "fecha_actualizacion")),
        url=_text(node, "url_html_consolidada"),
        eli_url=_optional_text(node, "url_eli"),
    )


def status_on(metadata: Metadata, today: dt.date) -> Status:
    """Whether the rule is in force on `today`.

    The BOE can record a repeal that takes effect later with the repeal flag still unset, so the
    status comes from the dates when there are any, not from the flags alone.
    """
    if metadata.annulled:
        return Status.ANNULLED
    if metadata.repeal_date is not None and metadata.repeal_date <= today:
        return Status.REPEALED
    if metadata.repeal_date is None and metadata.repealed_flag:
        return Status.REPEALED
    if metadata.expired:
        return Status.EXPIRED
    if metadata.in_force_from is not None and metadata.in_force_from > today:
        return Status.NOT_YET_IN_FORCE
    if metadata.repeal_date is not None:
        return Status.REPEAL_SCHEDULED
    return Status.IN_FORCE


def parse_analysis(data: ET.Element) -> Analysis:
    """Parse the `data` node of a rule's analysis: subjects and later changes, newest first."""
    node = _child(data, "analisis")
    subjects = [(subject.text or "").strip() for subject in node.iter("materia")]
    changes = [
        LaterChange(
            law_id=_text(ref, "id_norma"),
            relation=_text(ref, "relacion"),
            detail=_optional_text(ref, "texto"),
        )
        for ref in node.iterfind("referencias/posteriores/posterior")
    ]
    changes.sort(key=lambda change: _law_id_order(change.law_id), reverse=True)
    return Analysis(subjects=[subject for subject in subjects if subject], later_changes=changes)


def _law_id_order(law_id: str) -> tuple[int, int]:
    """Sort key for identifiers such as BOE-A-2025-26698: year, then number within the year."""
    match = _LAW_ID_ORDER.search(law_id)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def parse_outline(data: ET.Element) -> list[OutlineEntry]:
    """Parse the `data` node of a text index.

    Many rules leave the title of their preamble and signature empty, so a block may have none.
    """
    return [
        OutlineEntry(
            block_id=_text(block, "id"),
            title=_optional_text(block, "titulo") or "",
            updated=_optional_date(block, "fecha_actualizacion"),
        )
        for block in data.iter("bloque")
    ]


def parse_block(data: ET.Element) -> Block:
    """Parse the `data` node of a text block, oldest version first."""
    block = _child(data, "bloque")
    versions = [
        (
            BlockVersion(
                introduced_by=_attr(version, "id_norma"),
                published=parse_date(_attr(version, "fecha_publicacion")),
                in_force_from=_optional_date_attr(version, "fecha_vigencia"),
            ),
            version_text(version),
        )
        for version in block.iter("version")
    ]
    if not versions:
        raise ParseError("The block has no version.")
    return Block(kind=_attr(block, "tipo"), title=block.get("titulo") or "", versions=versions)


def version_text(version: ET.Element) -> str:
    """Render a version as plain text: paragraphs, table rows and notes."""
    parts: list[str] = []
    for child in version:
        if child.tag == "table":
            parts.extend(
                " | ".join(_flatten(cell) for cell in row if cell.tag in ("td", "th"))
                for row in child.iter("tr")
            )
        elif child.tag == "blockquote":
            parts.extend(f"Nota: {_flatten(note)}" for note in child.iter("p"))
        else:
            parts.append(_flatten(child))
    return "\n\n".join(part for part in parts if part)


def truncate(text: str, limit: int = MAX_BLOCK_CHARS) -> tuple[str, bool]:
    """Cut `text` at a paragraph boundary so it fits in `limit` characters."""
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n\n", 0, limit)
    return text[: cut if cut > 0 else limit], True


def _flatten(element: ET.Element) -> str:
    """Text of an element and its descendants, with a marker where an image was."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == "img":
            parts.append(f" {IMAGE_OMITTED} ")
        elif node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return _WHITESPACE.sub(" ", "".join(parts)).strip()


def _child(element: ET.Element, tag: str) -> ET.Element:
    child = element.find(tag)
    if child is None:
        raise ParseError(f"Missing <{tag}> in <{element.tag}>.")
    return child


def _text(element: ET.Element, tag: str) -> str:
    value = _optional_text(element, tag)
    if value is None:
        raise ParseError(f"Missing <{tag}> in <{element.tag}>.")
    return value


def _optional_text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None:
        return None
    value = _flatten(child)
    return value or None


def _optional_date(element: ET.Element, tag: str) -> dt.date | None:
    value = _optional_text(element, tag)
    return parse_date(value) if value else None


def _attr(element: ET.Element, name: str) -> str:
    value = element.get(name)
    if value is None:
        raise ParseError(f"Missing attribute {name} in <{element.tag}>.")
    return value


def _optional_date_attr(element: ET.Element, name: str) -> dt.date | None:
    value = element.get(name)
    return parse_date(value) if value else None
