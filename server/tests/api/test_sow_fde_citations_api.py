"""E6.3b: FDE-authored citations in an edited SOW (no `okf_ref_id`, signed by the API)."""

import copy
from datetime import UTC, datetime
from typing import Any

import pytest
from sbam_api.sow.tiptap import to_tiptap
from test_auth import problem
from test_sow_api import Harness


@pytest.fixture
def h(tmp_path: Any) -> Any:
    yield Harness(tmp_path)


def _scope_citations(tiptap: dict[str, Any]) -> dict[str, Any]:
    """The `sowCitations` block of the `scope` section (blocks are nested under sections)."""
    section = next(
        n
        for n in tiptap["content"]
        if n.get("type") == "sowSection" and n["attrs"]["section_id"] == "scope"
    )
    return next(n for n in section["content"] if n.get("type") == "sowCitations")


FORGED = {"kind": "fde", "author": "The CEO", "date": "1999-01-01", "note": "per the call"}


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _with(h: Harness, cite: dict[str, Any]) -> dict[str, Any]:
    doc = copy.deepcopy(h.structured)
    doc["scope"]["citations"].append(cite)
    return to_tiptap(doc)


def _fde(structured: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [c for c in (structured or {})["scope"]["citations"] if c.get("kind") == "fde"]


def test_e6_3b_t01_save_accepts_an_fde_authored_citation(h: Harness) -> None:
    res = h.save(_with(h, FORGED))
    assert res.status_code == 201
    [fde] = _fde(h.rows()[1].structured)
    assert set(fde) == {"kind", "author", "date", "note"}  # no okf_ref_id
    assert fde["note"] == "per the call"


def test_e6_3b_t02_an_unknown_okf_ref_id_is_still_422(h: Harness) -> None:
    doc = h.cited(copy.deepcopy(h.structured))
    doc["features"][0]["citations"][0]["okf_ref_id"] = "c-999"
    body = problem(h.save(to_tiptap(doc)), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["features.0.citations"]
    assert len(h.rows()) == 1


def test_e6_3b_t03_author_and_date_are_stamped_by_the_server_not_the_client(h: Harness) -> None:
    assert h.save(_with(h, FORGED)).status_code == 201
    [fde] = _fde(h.rows()[1].structured)
    assert fde["author"] != "The CEO" and fde["author"]
    assert fde["date"] == _today()


def test_e6_3b_t04_an_unchanged_fde_citation_keeps_its_stamp_and_saves_as_a_no_op(
    h: Harness,
) -> None:
    assert h.save(_with(h, FORGED)).status_code == 201
    v2 = h.rows()[1]
    [stamped] = _fde(v2.structured)
    again = h.save(to_tiptap(copy.deepcopy(v2.structured or {})), base=2)
    assert again.status_code == 200 and len(h.rows()) == 2
    assert _fde(h.rows()[1].structured) == [stamped]


def test_e6_3b_t05_rewriting_a_stamp_on_an_existing_citation_is_overwritten(h: Harness) -> None:
    assert h.save(_with(h, FORGED)).status_code == 201
    doc = copy.deepcopy(h.rows()[1].structured or {})
    doc["scope"]["citations"][-1].update(author="Someone Else", date="2001-02-03")
    assert h.save(to_tiptap(doc), base=2).status_code in (200, 201)
    for row in h.rows():
        for c in _fde(row.structured):
            assert c["author"] != "Someone Else" and c["date"] != "2001-02-03"


def test_e6_3b_t06_the_detail_lists_the_fde_citation_as_such(h: Harness) -> None:
    assert h.save(_with(h, FORGED)).status_code == 201
    refs = h.get("/versions/2").json()["citations"]
    fde = [r for r in refs if r["kind"] == "fde"]
    assert [r["path"] for r in fde] == ["scope"]
    assert fde[0]["okf_ref_id"] is None and fde[0]["note"] == "per the call"
    assert all(r["okf_ref_id"] for r in refs if r["kind"] == "okf")


@pytest.mark.parametrize(
    "cite",
    [
        {"kind": "fde", "date": "2026-01-01"},  # no author
        {"kind": "fde", "author": "A", "date": "not-a-date"},
    ],
)
def test_e6_3b_t07a_missing_or_bad_author_and_date_are_replaced_by_the_stamp(
    h: Harness, cite: dict[str, Any]
) -> None:
    tiptap = _with(h, FORGED)
    attrs = _scope_citations(tiptap)["content"][-1]["attrs"]
    attrs.clear()
    attrs.update(cite)
    assert h.save(tiptap).status_code == 201
    [fde] = _fde(h.rows()[1].structured)
    assert fde["author"] and fde["date"] == _today()


@pytest.mark.parametrize("cite", [{"kind": "fde", "note": "x" * 10_000}])
def test_e6_3b_t07_a_malformed_fde_citation_is_422(h: Harness, cite: dict[str, Any]) -> None:
    tiptap = to_tiptap(copy.deepcopy(h.structured))
    block = _scope_citations(tiptap)
    block["content"].append({"type": "citation", "attrs": dict(cite)})
    problem(h.save(tiptap), 422, "VALIDATION_ERROR")
    assert len(h.rows()) == 1


def test_e6_3b_t08_a_fake_okf_ref_id_on_an_fde_citation_is_not_kept(h: Harness) -> None:
    tiptap = _with(h, FORGED)
    block = _scope_citations(tiptap)
    block["content"][-1]["attrs"]["okf_ref_id"] = "c-999"
    assert h.save(tiptap).status_code == 201
    assert "okf_ref_id" not in _fde(h.rows()[1].structured)[0]


# --- a stamp is bound to the statement it was made on (review note on #353) -----------------------


def _save_as(h: Harness, monkeypatch: pytest.MonkeyPatch, who: str, tiptap: Any, base: int) -> Any:
    monkeypatch.setattr("sbam_api.sow.versions._signer", lambda user: who)
    res = h.save(tiptap, base=base)
    assert res.status_code == 201, res.text
    return h.rows()[-1].structured


def _alice_cites_scope(h: Harness, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    structured = _save_as(h, monkeypatch, "Alice", _with(h, FORGED), base=1)
    assert [c["author"] for c in _fde(structured)] == ["Alice"]
    return copy.deepcopy(structured or {})


def test_e6_3b_t09_a_colleagues_stamp_copied_onto_another_statement_is_replaced(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _alice_cites_scope(h, monkeypatch)
    alice = next(c for c in doc["scope"]["citations"] if c.get("kind") == "fde")
    doc["features"][0]["citations"].append(dict(alice))  # Alice's exact (author, date, note)
    structured = _save_as(h, monkeypatch, "Bob", to_tiptap(doc), base=2)
    assert [c["author"] for c in _fde(structured)] == ["Alice"]  # scope: untouched
    copied = [c for c in structured["features"][0]["citations"] if c.get("kind") == "fde"]
    assert [c["author"] for c in copied] == ["Bob"]  # the copy is Bob's own claim, not Alice's


def test_e6_3b_t10_editing_the_statement_a_stamp_stands_on_re_signs_it(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _alice_cites_scope(h, monkeypatch)
    doc["scope"]["in_scope"] = [*doc["scope"]["in_scope"], "Returns handling"]
    structured = _save_as(h, monkeypatch, "Bob", to_tiptap(doc), base=2)
    assert [c["author"] for c in _fde(structured)] == ["Bob"]


def test_e6_3b_t11_an_edit_elsewhere_keeps_the_stamp(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _alice_cites_scope(h, monkeypatch)
    doc["overview"]["objective"] = doc["overview"]["objective"] + " (revised)"
    structured = _save_as(h, monkeypatch, "Bob", to_tiptap(doc), base=2)
    assert [c["author"] for c in _fde(structured)] == ["Alice"]


def test_e6_3b_t12_reordering_items_keeps_the_stamp_with_its_item(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = copy.deepcopy(h.structured)
    doc["features"][1]["citations"].append(dict(FORGED))
    structured = _save_as(h, monkeypatch, "Alice", to_tiptap(doc), base=1)
    moved = copy.deepcopy(structured or {})
    moved["features"].reverse()
    structured = _save_as(h, monkeypatch, "Bob", to_tiptap(moved), base=2)
    stamped = [
        (f["id"], c["author"])
        for f in structured["features"]
        for c in f["citations"]
        if c.get("kind") == "fde"
    ]
    assert stamped == [(h.structured["features"][1]["id"], "Alice")]


def test_e6_3b_t13_a_stamp_survives_formatting_only_changes_in_the_editor(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _alice_cites_scope(h, monkeypatch)
    tiptap = to_tiptap(doc)
    section = next(n for n in tiptap["content"] if n["attrs"]["section_id"] == "scope")
    for field in section["content"]:  # bold marks and a stray blank paragraph, same words
        for block in field.get("content", []):
            for run in block.get("content", []):
                if run.get("type") == "text":
                    run["marks"] = [{"type": "bold"}]
        if field.get("type") == "sowField":
            field["content"].append({"type": "paragraph"})
    res = h.save(tiptap, base=2)
    assert res.status_code == 200 and len(h.rows()) == 2  # no new version, so no re-sign
