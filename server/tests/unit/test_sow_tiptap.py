"""E6.3 SowDocument <-> TipTap (ADR 0019): round trip, template lock, paths, formatting."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from sbam_api.sow.tiptap import TipTapInvalid, citations_of, from_tiptap, to_tiptap
from sbam_contracts.sow import SECTIONS, SowDocument
from sbam_contracts.sow_api import SECTION_TITLES

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
DOC = SowDocument.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
DATA = DOC.model_dump(mode="json")


def editor() -> dict[str, Any]:
    return json.loads(json.dumps(to_tiptap(DATA)))


def section(doc: dict[str, Any], section_id: str) -> dict[str, Any]:
    [found] = [s for s in doc["content"] if s["attrs"]["section_id"] == section_id]
    return found


def field(node: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [n for n in node["content"] if n.get("attrs", {}).get("field") == name]
    return found


def paths(doc: Any) -> list[str]:
    with pytest.raises(TipTapInvalid) as caught:
        from_tiptap(doc)
    return [e.path for e in caught.value.errors]


def test_e6_3_round_trip_is_exact() -> None:
    doc = editor()
    assert [s["attrs"]["section_id"] for s in doc["content"]] == list(SECTIONS)
    assert from_tiptap(doc) == DOC
    headings = [s["content"][0]["content"][0]["text"] for s in doc["content"]]
    assert headings == list(SECTION_TITLES.values())


def test_e6_3_multi_line_text_and_optional_fields_survive() -> None:
    data = copy.deepcopy(DATA)
    data["overview"]["background"] = "Line one\nLine two"
    data["overview"]["history"] = None
    doc = to_tiptap(data)
    back = from_tiptap(json.loads(json.dumps(doc)))
    assert back.overview.background == "Line one\nLine two" and back.overview.history is None


def test_e6_3_formatting_is_dropped_from_structured() -> None:
    doc = editor()
    objective = field(section(doc, "overview"), "objective")
    text = objective["content"][0]["content"][0]["text"]
    objective["content"][0]["content"] = [
        {"type": "text", "text": text[:5], "marks": [{"type": "bold"}]},
        {"type": "text", "text": text[5:], "marks": [{"type": "italic"}]},
    ]
    doc["content"].append({"type": "paragraph"})  # TipTap's trailing empty paragraph
    assert from_tiptap(doc) == DOC


def test_e6_3_a_hard_break_is_a_line_break() -> None:
    doc = editor()
    field(section(doc, "overview"), "topic")["content"] = [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "A"},
                {"type": "hardBreak"},
                {"type": "text", "text": "B"},
            ],
        }
    ]
    assert from_tiptap(doc).overview.topic == "A\nB"


def test_e6_3_t04_a_removed_section_is_reported_by_its_path() -> None:
    doc = editor()
    doc["content"] = [s for s in doc["content"] if s["attrs"]["section_id"] != "scope"]
    assert paths(doc) == ["sections.scope"]


def test_e6_3_a_renamed_heading_reordered_or_duplicated_section_is_refused() -> None:
    renamed = editor()
    section(renamed, "rules_constraints")["content"][0]["content"][0]["text"] = "Rules"
    assert paths(renamed) == ["sections.rules_constraints.heading"]
    reordered = editor()
    reordered["content"][0], reordered["content"][1] = (
        reordered["content"][1],
        reordered["content"][0],
    )
    assert paths(reordered) == ["sections"]
    duplicated = editor()
    duplicated["content"].append(copy.deepcopy(duplicated["content"][2]))
    assert paths(duplicated) == ["sections.scope"]


def test_e6_3_text_outside_the_sections_and_unknown_fields_are_refused() -> None:
    stray = editor()
    stray["content"].insert(0, {"type": "paragraph", "content": [{"type": "text", "text": "Hi"}]})
    assert paths(stray) == ["sections"]
    unknown = editor()
    section(unknown, "scope")["content"].insert(
        1, {"type": "sowField", "attrs": {"field": "status"}, "content": []}
    )
    assert paths(unknown) == ["scope.status"]
    assert paths({"type": "paragraph"}) == ["content_tiptap"]


def test_e6_3_schema_errors_keep_their_field_path() -> None:
    doc = editor()
    feature = section(doc, "features")["content"][1]
    attempts = field(field(feature, "bound"), "max_attempts")
    attempts["content"][0]["content"][0]["text"] = "lots"
    assert paths(doc) == ["features.0.bound.max_attempts"]
    attempts["content"][0]["content"][0]["text"] = "11"
    assert paths(doc) == ["features.0.bound.max_attempts"]


def test_e6_3_a_section_without_citations_is_refused() -> None:
    doc = editor()
    scope = section(doc, "scope")
    scope["content"] = [n for n in scope["content"] if n["type"] != "sowCitations"]
    assert paths(doc) == ["scope.citations"]


def test_e6_3_ids_come_from_attributes_and_lists_from_bullets() -> None:
    doc = editor()
    features = section(doc, "features")["content"]
    assert features[1]["attrs"] == {"id": "F-ACCESS-1", "module_id": "M-ACCESS"}
    in_scope = field(section(doc, "scope"), "in_scope")
    in_scope["content"][0]["content"].append(
        {
            "type": "listItem",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "New item"}]}],
        }
    )
    in_scope["content"][0]["content"].append(
        {"type": "listItem", "content": [{"type": "paragraph"}]}
    )
    assert from_tiptap(doc).scope.in_scope[-1] == "New item"


def test_e6_3_citations_of_lists_every_citation_with_its_place() -> None:
    found = citations_of(DOC)
    places = {path for path, _ in found}
    assert {"overview", "scope", "modules.0", "features.0", "features.1"} <= places
    assert len(found) == sum(
        (
            len(getattr(DOC, s).citations)
            if not isinstance(getattr(DOC, s), list)
            else sum(len(i.citations) for i in getattr(DOC, s))
        )
        for s in SECTIONS
    )
