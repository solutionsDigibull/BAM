"""E6.4 SOW export: the Markdown source, the renderers and `export_version` over a real generated
version (the E6.2 test world). PDF tests need WeasyPrint's Pango and skip where it is missing
(Windows); CI and the worker image have it."""

import io
import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import docx
import pytest
from knowledge_db import World
from llm_db import FakeBifrost, completion
from pypdf import PdfReader
from sbam_api.sow.models import SowExport, SowVersion
from sbam_contracts.sow import SowDocument
from sbam_contracts.sow_api import SECTION_TITLES, ExportFormat, SowExportInput
from sbam_workers.sow.export import render as render_module
from sbam_workers.sow.export.activity import ExportDeps, export_version
from sbam_workers.sow.export.markdown import (
    ExportMeta,
    build_markdown,
    citation_count,
    escape,
)
from sbam_workers.sow.export.render import render, to_html
from sqlalchemy import func, select, update
from temporalio.exceptions import ApplicationError
from test_sow_generate import Gen

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
DATA = SowDocument.model_validate_json(FIXTURE.read_text(encoding="utf-8")).model_dump(mode="json")
META = ExportMeta("Alpha", 3, "IN_REVIEW", "ab" * 32, datetime(2026, 10, 22, 9, 30))
HEADINGS = [f"{n}. {t}" for n, t in enumerate(SECTION_TITLES.values(), start=1)]


def weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


needs_weasyprint = pytest.mark.skipif(
    not weasyprint_available(), reason="WeasyPrint needs Pango (installed in the worker image)"
)


def hostile(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(data))
    out["overview"]["objective"] += (
        ' <img src="http://169.254.169.254/latest/meta-data/"> ![x](http://169.254.169.254/a.png)'
        " <script>alert(1)</script> [click](javascript:alert(1)) 日本語のテキスト العربية"
    )
    return out


# --- the Markdown source --------------------------------------------------------------------------


def test_e6_4_markdown_has_the_nine_headings_in_order_and_one_footnote_per_citation() -> None:
    md = build_markdown(DATA, META, {})
    assert re.findall(r"^## (.+)$", md, re.M) == HEADINGS
    refs = re.findall(r"\[\^(\d+)\](?!:)", md)
    notes = re.findall(r"^\[\^(\d+)\]:", md, re.M)
    assert len(refs) == len(notes) == citation_count(DATA) > 0
    assert "**SHA-256:** `" + "ab" * 32 + "`" in md


def test_e6_4_citations_name_the_document_and_its_location() -> None:
    doc = DATA["scope"]["citations"][0]["document_version_id"]
    from uuid import UUID

    md = build_markdown(DATA, META, {UUID(doc): "RFQ v2.pdf"})
    assert "RFQ v2.pdf, line 5" in md


def test_e6_4_text_is_escaped_so_markup_stays_literal() -> None:
    html = to_html(build_markdown(hostile(DATA), META, {}), superseded=False)
    for tag in ("<img", "<script", 'href="javascript', 'href="http'):
        assert tag not in html
    assert "日本語のテキスト" in html and "العربية" in html
    assert escape("1. two") == "1\\. two" and escape("- x") == "\\- x"


def test_e6_4_a_superseded_version_says_so() -> None:
    meta = ExportMeta("Alpha", 1, "SUPERSEDED", "cd" * 32, datetime(2026, 1, 1))
    md = build_markdown(DATA, meta, {})
    assert "**SUPERSEDED**" in md
    assert 'class="watermark">SUPERSEDED' in to_html(md, superseded=True)


# --- renderers ------------------------------------------------------------------------------------


def test_e6_4_t03_markdown_export_is_utf8_with_section_headings_and_citations() -> None:
    data = render(build_markdown(hostile(DATA), META, {}), ExportFormat.MD)
    text = data.decode("utf-8")
    assert re.findall(r"^## (.+)$", text, re.M) == HEADINGS
    assert text.rstrip().splitlines()[-1].startswith(f"[^{citation_count(DATA)}]:")


def test_e6_4_t02_docx_has_heading_1_sections_and_footnotes() -> None:
    data = render(build_markdown(DATA, META, {}), ExportFormat.DOCX)
    document = docx.Document(io.BytesIO(data))
    heading_1 = [p.text for p in document.paragraphs if p.style and p.style.name == "Heading 1"]
    assert heading_1 == HEADINGS
    body = document.element.xml
    assert body.count("<w:footnoteReference") == citation_count(DATA)


@needs_weasyprint
def test_e6_4_t01_pdf_has_the_headings_and_the_sha256_on_the_cover() -> None:
    data = render(build_markdown(DATA, META, {}), ExportFormat.PDF)
    reader = PdfReader(io.BytesIO(data))
    cover = reader.pages[0].extract_text()
    assert "ab" * 32 in cover.replace("\n", "")
    text = "\n".join(page.extract_text() for page in reader.pages)
    positions = [text.find(h) for h in HEADINGS]
    assert -1 not in positions and positions == sorted(positions)


@needs_weasyprint
def test_e6_4_t05_no_url_is_fetched_and_the_export_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    def spy(url: str, *args: Any, **kwargs: Any) -> Any:
        fetched.append(url)
        return render_module.refuse_urls(url)

    monkeypatch.setattr(render_module, "refuse_urls", spy)
    data = render(build_markdown(hostile(DATA), META, {}), ExportFormat.PDF)
    assert data.startswith(b"%PDF") and fetched == []


def test_e6_4_t05_the_pdf_url_fetcher_refuses_everything() -> None:
    with pytest.raises(ValueError):
        render_module.refuse_urls("http://169.254.169.254/latest/meta-data/")


# --- export_version ------------------------------------------------------------------------------


@pytest.fixture
def g(tmp_path: Path) -> Iterator[Gen]:
    gen = Gen(World(tmp_path))
    SowExport.__table__.create(gen.w.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    gen.run(FakeBifrost([completion(gen.answer())]))
    yield gen


def export_input(g: Gen, fmt: ExportFormat) -> SowExportInput:
    [version] = g.versions()
    return SowExportInput(
        **g.inp.model_dump(exclude={"run_id"}),
        run_id=uuid4(),
        sow_version_id=version.id,
        format=fmt,
    )


def test_e6_4_t07_the_same_version_is_rendered_once(
    g: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = render

    def counting(md: str, fmt: ExportFormat, **kw: Any) -> bytes:
        calls.append(fmt.value)
        return real(md, fmt, **kw)

    monkeypatch.setattr("sbam_workers.sow.export.activity.render", counting)
    deps = ExportDeps(g.w.sessions, g.w.storage)
    first = export_version(deps, export_input(g, ExportFormat.MD))
    second = export_version(deps, export_input(g, ExportFormat.MD))
    assert first == second and calls == ["md"]
    text = g.w.storage.get(first.storage_key, verify=True).decode("utf-8")
    assert re.findall(r"^## (.+)$", text, re.M) == HEADINGS
    with g.w.sessions() as s:
        assert s.scalar(select(func.count()).select_from(SowExport)) == 1


def test_e6_4_an_approval_renders_a_new_cover(g: Gen) -> None:
    deps = ExportDeps(g.w.sessions, g.w.storage)
    before = export_version(deps, export_input(g, ExportFormat.MD))
    with g.w.sessions() as s:
        s.execute(update(SowVersion).values(state="SUPERSEDED"))
        s.commit()
    after = export_version(deps, export_input(g, ExportFormat.MD))
    assert before.storage_key != after.storage_key
    assert b"**SUPERSEDED**" in g.w.storage.get(after.storage_key)


def test_e6_4_a_failed_generation_cannot_be_exported(g: Gen) -> None:
    with g.w.sessions() as s:
        s.execute(update(SowVersion).values(state="GENERATION_FAILED"))
        s.commit()
    with pytest.raises(ApplicationError) as caught:
        export_version(ExportDeps(g.w.sessions, g.w.storage), export_input(g, ExportFormat.MD))
    assert caught.value.type == "ExportNotAllowed" and caught.value.non_retryable


def test_e6_4_tampered_content_is_refused(g: Gen) -> None:
    with g.w.sessions() as s:
        s.execute(update(SowVersion).values(sha256="0" * 64))
        s.commit()
    with pytest.raises(ApplicationError) as caught:
        export_version(ExportDeps(g.w.sessions, g.w.storage), export_input(g, ExportFormat.DOCX))
    assert caught.value.type == "HASH_MISMATCH"
