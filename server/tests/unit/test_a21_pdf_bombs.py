"""A-21: a small hostile PDF must not exhaust the shared ingest worker (pypdf >= 6.21.2 limits)."""

import time
import zlib
from importlib.metadata import version

import pytest
from sbam_contracts.api import ErrorCode
from sbam_workers.ingest.loaders.base import ParseError
from sbam_workers.ingest.loaders.pdf import load_pdf

DEADLINE_SECONDS = 30  # measured 0.2 s and 4 s; the margin keeps a slow CI runner green


def _pdf(page_extra: str = "", content: bytes = b"") -> bytes:
    """One-page PDF; `page_extra` lands in the page dictionary, `content` is its content stream."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> " + page_extra.encode() + b" >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs),
        xref,
    )


def test_a21_t00_pypdf_is_at_or_above_the_fixed_release() -> None:
    assert tuple(int(p) for p in version("pypdf").split(".")[:3]) >= (6, 21, 2)


def test_a21_t01_array_bomb_rejected() -> None:
    data = _pdf("/Foo [" + "0 " * 7_000_000 + "]")  # 14 MB, a 7M-element array
    started = time.monotonic()
    with pytest.raises(ParseError) as err:
        load_pdf(data, lambda: None)
    assert err.value.code == ErrorCode.CORRUPT
    assert time.monotonic() - started < DEADLINE_SECONDS


def test_a21_t02_content_stream_bomb_rejected() -> None:
    data = _pdf(content=b"BT /F1 1 Tf " + b"0 0 Td (a) Tj " * 500_000 + b"ET")  # 7 MB of operators
    started = time.monotonic()
    parsed = load_pdf(data, lambda: None)  # the page is unreadable: a warning, never the worker
    assert parsed.report.warnings == ["page 1 could not be read"]
    assert parsed.report.ocr_needed_pages == [1]
    assert parsed.blocks == []
    assert time.monotonic() - started < DEADLINE_SECONDS


def test_a21_t03_a_normal_pdf_still_parses() -> None:
    parsed = load_pdf(
        _pdf(content=b"BT /F1 12 Tf 10 50 Td (hello world, this is text) Tj ET"), lambda: None
    )
    assert parsed.report.warnings == []
    assert "hello world" in parsed.markdown


def test_a21_t04_flate_decompression_bomb_rejected() -> None:
    packer = zlib.compressobj(9)
    chunk = b"BT /F1 1 Tf (a) Tj ET\n" * 100_000
    packed = b"".join(packer.compress(chunk) for _ in range(150)) + packer.flush()
    data = _pdf(content=packed).replace(
        b"/Length %d" % len(packed), b"/Length %d /Filter /FlateDecode" % len(packed)
    )
    assert len(data) < 5_000_000
    parsed = load_pdf(data, lambda: None)
    assert parsed.report.warnings == ["page 1 could not be read"]
