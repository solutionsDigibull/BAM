"""GET /admin/master-types (TD §8.3, E3.10): the frozen layout the masters screen is built from."""

from pathlib import Path
from typing import Any

import pytest
import test_projects
from sbam_contracts.masters import MasterType
from sbam_contracts.masters_api import MasterTypes
from sbam_contracts.masters_v03 import ID_KEY
from sbam_contracts.masters_workbook import COLUMNS, ID_HEADER, TABS
from test_auth import FDE_A1, problem
from test_masters_api import MastersHarness, engine_with_masters


@pytest.fixture
def h(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MastersHarness:
    monkeypatch.setattr(test_projects, "sqlite_engine", engine_with_masters)
    return MastersHarness(tmp_path)


def layout(h: MastersHarness) -> dict[str, Any]:
    res = h.call("GET", "/admin/master-types", h.admin_a())
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    MasterTypes.model_validate(body)
    return body


def test_e3_10_every_master_in_workbook_order(h: MastersHarness) -> None:
    types = {t["master_type"]: t for t in layout(h)["types"]}
    assert list(types) == [t.value for t in TABS]
    for master_type, tab in TABS.items():
        info = types[master_type.value]
        assert info["tab"] == tab
        columns = [(c["header"], c["field"]) for c in info["columns"]]
        if master_type is MasterType.FILETYPE:
            assert columns == list(COLUMNS[master_type])
        else:  # Masters 0.3: the id column (column A) comes first
            assert columns == [(ID_HEADER[master_type], ID_KEY[master_type]), *COLUMNS[master_type]]
        assert {f for _, f in columns} <= set(info["payload_schema"]["properties"])
    assert types["FILETYPE"]["editable"] is False and types["RULES"]["editable"] is True
    assert types["RULES"]["row_label_prefix"] == "RULE-CUSTOM"


def test_e3_10_t03_row_3_descriptions_are_the_tooltips(h: MastersHarness) -> None:
    types = {t["master_type"]: t for t in layout(h)["types"]}
    rules = {c["header"]: c["description"] for c in types["RULES"]["columns"]}
    assert (
        rules["Owner / version"] == "Accountable owner + rule version; confirmed before activation."
    )
    org = {c["header"]: c["description"] for c in types["ORGANIZATION"]["columns"]}
    assert org["Org ID"] == "Stable unit or resource ID"
    assert org["Brand Properties"] == "Website / GBP / Social Media"
    tooling = types[MasterType.TOOLING.value]["columns"]
    assert all(c["description"] for c in tooling)  # the approved workbook's row 3


def test_e3_10_readme_tab(h: MastersHarness) -> None:
    readme = layout(h)["readme"]
    assert readme["title"].startswith("BuLLM - Admin Masters (BAM)")
    assert any(row and row[0] == "Tab" for row in readme["rows"])  # the column guide
    assert all(len(row) <= 4 for row in readme["rows"])


def test_e3_10_fde_cannot_read_the_layout(h: MastersHarness) -> None:
    problem(h.call("GET", "/admin/master-types", h.realm.token(FDE_A1)), 403, "WRONG_PORTAL")
