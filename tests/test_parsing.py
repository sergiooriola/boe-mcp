import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from boe_mcp import parsing
from boe_mcp.models import Status

from .conftest import fixture_data


def test_summary_lists_every_item_with_its_section_and_department() -> None:
    bulletin = parsing.parse_summary(fixture_data("summary-20261012.xml"))

    assert bulletin.date == dt.date(2026, 10, 12)
    assert len(bulletin.items) == 86
    assert sum(section.items for section in bulletin.sections) == 86
    first = bulletin.items[0]
    assert first.id.startswith("BOE-")
    assert first.department
    assert str(first.pdf_url).endswith(f"{first.id}.pdf")


def test_summary_with_an_extraordinary_issue_lists_both_issues() -> None:
    bulletin = parsing.parse_summary(fixture_data("summary-20261002.xml"))

    assert len(bulletin.issues) == 2
    assert len({item.id for item in bulletin.items}) == len(bulletin.items)


def test_items_outside_a_heading_have_no_heading() -> None:
    data = ET.fromstring(
        """<data><sumario><metadatos><fecha_publicacion>20261012</fecha_publicacion></metadatos>
        <diario numero="1"><seccion codigo="4" nombre="IV. Administración de Justicia">
        <departamento codigo="1" nombre="JUZGADOS"><item><identificador>BOE-B-2026-1</identificador>
        <titulo>Edicto</titulo><url_pdf>https://www.boe.es/a.pdf</url_pdf></item></departamento>
        </seccion></diario></sumario></data>"""
    )

    [item] = parsing.parse_summary(data).items

    assert item.heading is None
    assert item.html_url is None


def test_search_results() -> None:
    results = parsing.parse_search(fixture_data("search-proteccion-datos.xml"))

    assert [result.id for result in results][:1] == ["BOE-A-2024-900"]
    assert results[0].scope == "Autonómico"
    assert results[0].in_force is True
    assert results[0].published == dt.date(2024, 1, 18)


@pytest.mark.parametrize(
    ("fixture", "today", "status"),
    [
        ("metadata-BOE-A-2018-16673.xml", dt.date(2026, 10, 12), Status.IN_FORCE),
        # The repeal flag is still N, but the repeal is dated 2027-01-02.
        ("metadata-BOE-A-2006-22080.xml", dt.date(2026, 10, 12), Status.REPEAL_SCHEDULED),
        ("metadata-BOE-A-2006-22080.xml", dt.date(2027, 1, 2), Status.REPEALED),
        ("metadata-BOE-A-1992-26318.xml", dt.date(2026, 10, 12), Status.REPEALED),
        # Published on 2018-12-06, in force from the next day.
        ("metadata-BOE-A-2018-16673.xml", dt.date(2018, 12, 6), Status.NOT_YET_IN_FORCE),
        ("metadata-BOE-A-2018-16673.xml", dt.date(2018, 12, 7), Status.IN_FORCE),
    ],
)
def test_status_comes_from_the_repeal_date(fixture: str, today: dt.date, status: Status) -> None:
    metadata = parsing.parse_metadata(fixture_data(fixture))

    assert parsing.status_on(metadata, today) is status


def _metadata(**changes: object) -> parsing.Metadata:
    from dataclasses import replace

    base = parsing.parse_metadata(fixture_data("metadata-BOE-A-2018-16673.xml"))
    return replace(base, **changes)  # type: ignore[arg-type]


def test_status_of_annulled_expired_and_flag_only_rules() -> None:
    today = dt.date(2026, 10, 12)

    assert parsing.status_on(_metadata(annulled=True), today) is Status.ANNULLED
    assert parsing.status_on(_metadata(expired=True), today) is Status.EXPIRED
    assert parsing.status_on(_metadata(repealed_flag=True), today) is Status.REPEALED


def test_metadata_fields() -> None:
    metadata = parsing.parse_metadata(fixture_data("metadata-BOE-A-2018-16673.xml"))

    assert metadata.official_number == "3/2018"
    assert metadata.rank == "Ley Orgánica"
    assert metadata.last_updated == dt.datetime(2026, 10, 8, 7, 39, 57, tzinfo=dt.UTC)
    assert metadata.eli_url == "https://www.boe.es/eli/es/lo/2018/12/05/3"


def test_analysis_lists_later_changes_newest_first() -> None:
    analysis = parsing.parse_analysis(fixture_data("analysis-BOE-A-2018-16673.xml"))

    assert "Protección de datos personales" in analysis.subjects
    assert len(analysis.later_changes) == 7
    assert analysis.later_changes[0].law_id == "BOE-A-2025-26698"
    assert analysis.later_changes[0].relation == "SE MODIFICA"
    years = [change.law_id.split("-")[2] for change in analysis.later_changes]
    assert years == sorted(years, reverse=True)


def test_outline() -> None:
    outline = parsing.parse_outline(fixture_data("outline-BOE-A-2018-16673.xml"))

    assert len(outline) == 169
    article_23 = next(entry for entry in outline if entry.title == "Artículo 23")
    assert article_23.block_id == "a2-5"
    assert article_23.updated == dt.date(2025, 12, 27)


def test_outline_keeps_blocks_without_a_title() -> None:
    # The Constitution, like many rules, leaves its preamble and signature untitled.
    outline = parsing.parse_outline(fixture_data("outline-BOE-A-1978-31229.xml"))

    assert len(outline) == 210
    untitled = [entry.block_id for entry in outline if not entry.title]
    assert untitled == ["preambulo", "firma"]
    assert next(entry for entry in outline if entry.block_id == "a1").title == "Artículo 1"


def test_block_keeps_every_version_oldest_first_with_notes() -> None:
    block = parsing.parse_block(fixture_data("block-BOE-A-2018-16673-a2-5.xml"))

    assert block.kind == "precepto"
    assert [version.introduced_by for version, _ in block.versions] == [
        "BOE-A-2018-16673",
        "BOE-A-2025-26698",
    ]
    _, current_text = block.versions[-1]
    assert current_text.startswith("Artículo 23. Sistemas de exclusión publicitaria.")
    assert "Nota: " in current_text


@pytest.mark.parametrize(
    "fixture",
    [
        "block-BOE-A-1882-6036-a387.xml",  # "(Derogado)." on a line of its own
        "block-BOE-A-1889-4763-art.1863bis.xml",  # "Artículo 1863 bis. (Derogado)"
        "block-BOE-A-1995-25444-txixbis.xml",  # a title, its name and "(Suprimido)"
    ],
)
def test_only_the_version_that_repeals_a_block_is_a_repeal_notice(fixture: str) -> None:
    block = parsing.parse_block(fixture_data(fixture))

    assert [version.repealed for version, _ in block.versions] == [False] * (
        len(block.versions) - 1
    ) + [True]


@pytest.mark.parametrize(
    "version",
    [
        # Article 1654 of the Código Civil, in force.
        '<version><p class="articulo">Artículo 1654.</p>'
        '<p class="parrafo">Queda suprimido para lo sucesivo el contrato de subenfiteusis.</p>'
        "</version>",
        # A section still in force before a repealed one.
        '<version><p class="articulo">Artículo 9.</p><p class="parrafo">1. Texto vigente.</p>'
        '<p class="parrafo">(Derogado)</p></version>',
        # Normative text that ends by naming a repealed rule.
        '<version><p class="parrafo">Se aplicará el artículo 5 de la Ley 30/1992 (derogada).</p>'
        "</version>",
        # A notice inside a note only.
        '<version><p class="articulo">Artículo 2.</p><blockquote><p class="nota_pie">(Derogado)'
        "</p></blockquote></version>",
    ],
)
def test_wording_that_mentions_a_repeal_is_not_a_repeal_notice(version: str) -> None:
    assert parsing.is_repeal_notice(ET.fromstring(version)) is False


@pytest.mark.parametrize(
    ("fixture", "lapsed_on"),
    [
        ("block-BOE-A-1885-6627-a573.xml", dt.date(2014, 7, 25)),
        ("block-BOE-A-2018-16673-a2-5.xml", None),
    ],
)
def test_block_lapse_date(fixture: str, lapsed_on: dt.date | None) -> None:
    assert parsing.parse_block(fixture_data(fixture)).lapsed_on == lapsed_on


def test_an_impossible_lapse_date_is_ignored() -> None:
    # The BOE has a few, such as 00000015 on article 309 of the Código Civil.
    data = ET.fromstring(
        '<data><bloque id="a1" tipo="precepto" titulo="Artículo 1" fecha_caducidad="00000015">'
        '<version id_norma="BOE-A-1889-4763" fecha_publicacion="18890725"><p>Artículo 1.</p>'
        "</version></bloque></data>"
    )

    assert parsing.parse_block(data).lapsed_on is None


def test_block_without_versions_is_rejected() -> None:
    data = ET.fromstring('<data><bloque id="a1" tipo="precepto" titulo="Artículo 1"/></data>')

    with pytest.raises(parsing.ParseError):
        parsing.parse_block(data)


def test_images_inside_paragraphs_and_cells_leave_a_marker() -> None:
    version = ET.fromstring(
        """<version><p>Fórmula: <img src="a.png"/> donde x es</p>
        <table><tr><td><img src="b.png"/></td><td>Valor</td></tr></table></version>"""
    )

    assert parsing.version_text(version) == (
        f"Fórmula: {parsing.IMAGE_OMITTED} donde x es\n\n{parsing.IMAGE_OMITTED} | Valor"
    )


def test_version_text_flattens_tables_and_omits_images() -> None:
    version = ET.fromstring(
        """<version id_norma="BOE-A-1" fecha_publicacion="20200101">
        <p class="parrafo">Tabla <b>de</b>   tarifas:</p>
        <table><tr><th>Concepto</th><th>Euros</th></tr><tr><td>Tasa</td><td>10</td></tr></table>
        <img src="data:image/png;base64,AAAA"/>
        </version>"""
    )

    assert parsing.version_text(version) == (
        "Tabla de tarifas:\n\nConcepto | Euros\n\nTasa | 10\n\n"
        "[Imagen omitida: consúltela en boe.es]"
    )


def test_truncate_cuts_at_a_paragraph() -> None:
    text = "a" * 10 + "\n\n" + "b" * 10

    assert parsing.truncate(text, limit=15) == ("a" * 10, True)
    assert parsing.truncate(text, limit=100) == (text, False)
    assert parsing.truncate("c" * 20, limit=5) == ("c" * 5, True)


def test_missing_nodes_raise_parse_errors() -> None:
    with pytest.raises(parsing.ParseError, match="sumario"):
        parsing.parse_summary(ET.fromstring("<data/>"))
    with pytest.raises(parsing.ParseError, match="identificador"):
        parsing.parse_search(ET.fromstring("<data><item/></data>"))
    with pytest.raises(parsing.ParseError, match="id_norma"):
        parsing.parse_block(
            ET.fromstring('<data><bloque tipo="precepto"><version/></bloque></data>')
        )
