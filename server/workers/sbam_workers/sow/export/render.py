"""SOW export renderers (E6.4): the Markdown source to md, docx or pdf bytes.

- md: the source itself, UTF-8.
- docx: Pandoc (`pypandoc-binary`, the version pinned by the lockfile). `## ` sections become Word's
  Heading 1 (`--shift-heading-level-by=-1`, the cover's `# ` becomes the title); citations become
  real footnotes.
- pdf: Pandoc's HTML of the source, in SBAM's page template, printed by WeasyPrint. WeasyPrint gets
  a `url_fetcher` that refuses every URL, so no image, stylesheet or font is ever fetched (SSRF),
  and the fonts are the container's Noto families (Latin, CJK, Arabic), embedded in the PDF. Wide
  tables wrap. A SUPERSEDED version carries a watermark on every page.

WeasyPrint needs Pango from the system (the worker image installs it); it is imported only when a
PDF is rendered, so the other formats work wherever Pandoc does.
"""

import html
import tempfile
from pathlib import Path
from typing import Any, Final, NoReturn

import pypandoc
from sbam_contracts.sow_api import ExportFormat

from sbam_workers.sow.export.markdown import MD_FORMAT

CONTENT_TYPES: Final = {
    ExportFormat.MD: "text/markdown; charset=utf-8",
    ExportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ExportFormat.PDF: "application/pdf",
}
PAGE_CSS: Final = """
@page { size: A4; margin: 20mm 18mm; @bottom-right { content: counter(page) " / " counter(pages);
  font-size: 8pt; color: #666; } }
html { font-family: "Noto Sans", "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans Arabic",
  "Noto Sans Hebrew", "DejaVu Sans", sans-serif; font-size: 10pt; line-height: 1.4; }
h1 { font-size: 20pt; margin: 0 0 12pt; }
h2 { font-size: 14pt; margin-top: 18pt; border-bottom: 1px solid #ccc; break-after: avoid; }
h3 { font-size: 11.5pt; margin-top: 12pt; break-after: avoid; }
p, li { overflow-wrap: anywhere; }
table { width: 100%; table-layout: fixed; border-collapse: collapse; }
td, th { border: 1px solid #ccc; padding: 2pt 4pt; overflow-wrap: anywhere; vertical-align: top; }
code { font-family: "Noto Sans Mono", "DejaVu Sans Mono", monospace; overflow-wrap: anywhere; }
section.footnotes { font-size: 8.5pt; border-top: 1px solid #ccc; margin-top: 18pt; }
.cover { break-after: page; }
.watermark { position: fixed; top: 45%; left: 0; width: 100%; text-align: center;
  transform: rotate(-30deg); font-size: 60pt; color: rgba(200, 0, 0, 0.12); z-index: -1; }
"""


def refuse_urls(url: str, *_: Any, **__: Any) -> NoReturn:
    """WeasyPrint's `url_fetcher`: nothing is fetched, ever (no remote images, CSS or fonts)."""
    raise ValueError("SOW exports fetch no URLs")


def to_html(markdown: str, *, superseded: bool) -> str:
    body = pypandoc.convert_text(markdown, "html5", format=MD_FORMAT)
    head, sep, rest = body.partition("<h2")
    cover = f'<div class="cover">{head}</div>' if sep else head
    mark = '<div class="watermark">SUPERSEDED</div>' if superseded else ""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape('Statement of Work')}</title></head>"
        f"<body>{mark}{cover}{sep}{rest}</body></html>"
    )


def render_pdf(markdown: str, *, superseded: bool) -> bytes:
    from weasyprint import CSS, HTML  # needs Pango: the worker image installs it

    document = HTML(string=to_html(markdown, superseded=superseded), url_fetcher=refuse_urls)
    css = CSS(string=PAGE_CSS, url_fetcher=refuse_urls)
    pdf: bytes = document.write_pdf(stylesheets=[css])
    return pdf


def render_docx(markdown: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="sbam-sow-export-") as tmp:
        out = Path(tmp) / "sow.docx"
        pypandoc.convert_text(
            markdown,
            "docx",
            format=MD_FORMAT,
            outputfile=str(out),
            extra_args=["--shift-heading-level-by=-1"],
        )
        return out.read_bytes()


def render(markdown: str, fmt: ExportFormat, *, superseded: bool = False) -> bytes:
    if fmt is ExportFormat.MD:
        return markdown.encode("utf-8")
    if fmt is ExportFormat.DOCX:
        return render_docx(markdown)
    return render_pdf(markdown, superseded=superseded)
