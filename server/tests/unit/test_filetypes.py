"""E3.7 Filetype configuration: validation, the frozen rows and the upload allowlist."""

import copy
import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest
import yaml
from sbam_api.filetypes import config as ft
from sbam_api.ingest.rules import ALLOWED_EXTENSIONS, is_allowed
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_workbook import parse_workbook

REPO = Path(__file__).resolve().parents[3]
WORKBOOK = REPO / "docs/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"
# TD §5.2 MVP set, as listed in E3.7-T04.
MVP = {
    "md", "txt", "eml", "msg", "json", "xml", "yaml", "yml", "py", "sql", "js", "go", "cpp",
    "csv", "xlsx", "docx", "pdf", "zip", "tar.gz", "png", "jpg", "jpeg", "tiff",
}  # fmt: skip


def raw_config() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(ft.CONFIG_FILE.read_text(encoding="utf-8"))["filetypes"]
    return copy.deepcopy(data)


def frozen() -> dict[str, Any]:
    return ft._read_frozen(ft.FROZEN_FILE)


# --- T04 / T05: the allowlist -----------------------------------------------------------------


def test_e3_7_t04_allowlist_is_the_mvp_set() -> None:
    assert ft.allowlist() == MVP
    assert ALLOWED_EXTENSIONS == MVP  # the upload rules use the derived set


def test_e3_7_t05_future_type_is_not_uploadable() -> None:
    assert "dwg" not in ft.allowlist()
    assert not is_allowed("plant-layout.dwg")
    assert is_allowed("BOM.tar.gz") and is_allowed("Quote.PDF")


def test_e3_7_shipped_config_has_43_rows_in_order_and_future_types_disabled() -> None:
    rows = ft.load()
    assert [f.row_label for f in rows] == [f"Filetype #{n}" for n in range(1, 44)]
    assert all(not f.config.mvp_enabled for f in rows if f.row_label in ft.FUTURE)
    assert all(f.config.loader in ft.LOADERS for f in rows if f.config.mvp_enabled)


def test_e3_7_pdf_text_and_scanned_share_the_extension_with_their_own_loaders() -> None:
    by_label = {f.row_label: f for f in ft.load()}
    text, scanned = by_label["Filetype #21"], by_label["Filetype #34"]
    assert text.config.extensions == scanned.config.extensions == ["pdf"]
    assert (text.config.loader, scanned.config.loader) == ("pdf-text", "ocr")


def test_e3_7_api_specs_are_recognised_by_content_not_extension() -> None:
    by_label = {f.row_label: f for f in ft.load()}
    for label in ("Filetype #13", "Filetype #15", "Filetype #16"):  # FastMCP, OpenAPI, Swagger
        assert by_label[label].config.extensions == [] and by_label[label].config.mvp_enabled


def test_e3_7_frozen_rows_equal_the_bam_0_2_workbook() -> None:
    book = openpyxl.load_workbook(WORKBOOK, read_only=True)
    try:
        sheets = {
            ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in book.worksheets
        }
    finally:
        book.close()
    rows = [r for r in parse_workbook(sheets).rows if r.master_type == MasterType.FILETYPE]
    expected = [
        {"row_label": r.row_label.strip(), **r.payload.model_dump(mode="json")} for r in rows
    ]
    assert json.loads(ft.FROZEN_FILE.read_text(encoding="utf-8")) == expected


# --- T06: a bad configuration stops the API -------------------------------------------------------


def test_e3_7_t06_unknown_row_label_raises() -> None:
    raw = raw_config()
    raw["Filetype #44"] = raw.pop("Filetype #43")
    with pytest.raises(ft.ConfigError, match="Filetype #44"):
        ft.validate(raw, frozen())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.pop("Filetype #1"), "expected 43 filetypes"),
        (lambda r: r["Filetype #6"].update(loader="markdown-magic"), "unknown loader"),
        (lambda r: r["Filetype #6"].update(loader=None), "enabled without a loader"),
        (lambda r: r["Filetype #27"].update(mvp_enabled=True, loader="ocr"), "Future type"),
        (lambda r: r["Filetype #27"].update(mvp_enabled=True), "enabled without a loader"),
        (lambda r: r["Filetype #6"].update(default_route="Tooling"), "Filetype #6"),
        (lambda r: r["Filetype #6"].update(priority=1), "Filetype #6"),
    ],
    ids=["count", "loader", "no-loader", "future", "future-no-loader", "route", "extra-key"],
)
def test_e3_7_t06_bad_config_raises(change: Any, message: str) -> None:
    raw = raw_config()
    change(raw)
    with pytest.raises(ft.ConfigError, match=message):
        ft.validate(raw, frozen())


def test_e3_7_t06_unreadable_config_raises(tmp_path: Path) -> None:
    bad = tmp_path / "filetypes.yaml"
    bad.write_text("filetypes: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(ft.ConfigError):
        ft.load.__wrapped__(bad, ft.FROZEN_FILE)


def test_e3_7_config_never_carries_frozen_columns() -> None:
    frozen_columns = {"file_format", "tier", "purpose", "rag_ingestion", "example"}
    assert all(frozen_columns.isdisjoint(entry) for entry in raw_config().values())
