"""E3.8b contract tests: the parsed master documents a snapshot pins, the parse code of a master
document and the Cognee item source for it."""

import uuid

import pytest
from pydantic import ValidationError
from sbam_contracts.api import ErrorCode
from sbam_contracts.knowledge import ItemSource, KnowledgeItem
from sbam_contracts.masters_api import (
    MasterDocumentOut,
    MastersSnapshot,
    SnapshotDocument,
)

SHA = "a" * 64


def pinned(**changes: object) -> dict[str, object]:
    return {
        "document_id": str(uuid.uuid4()),
        "title": "costing-standard.pdf",
        "master_type": "RULES",
        "intent_class": "Policy / rules",
        "sha256": SHA,
        "derived_md_key": f"t/p/derived/{SHA}",
        **changes,
    }


def snapshot(**changes: object) -> dict[str, object]:
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "entries": [],
        "sha256": "sha256:" + SHA,
        "created_at": "2026-10-27T09:00:00Z",
        **changes,
    }


def test_e3_8b_a_snapshot_taken_before_master_documents_validates_with_none() -> None:
    assert MastersSnapshot.model_validate(snapshot()).documents == []


def test_e3_8b_a_snapshot_lists_the_parsed_master_documents_it_pins() -> None:
    first, second = pinned(), pinned(title="agents.yaml", master_type="AGENTS")
    parsed = MastersSnapshot.model_validate(snapshot(documents=[first, second]))
    assert [d.title for d in parsed.documents] == ["costing-standard.pdf", "agents.yaml"]
    assert MastersSnapshot.model_validate(parsed.model_dump(mode="json")) == parsed


@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "short"},
        {"sha256": "A" * 64},  # lower-case hex only
        {"master_type": "NOT_A_MASTER"},
        {"document_id": "not-a-uuid"},
        {"derived_md_key": None},
    ],
)
def test_e3_8b_a_pinned_document_with_a_bad_field_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SnapshotDocument.model_validate(pinned(**changes))


def test_e3_8b_a_master_document_may_say_why_its_parse_failed() -> None:
    base = {
        "id": str(uuid.uuid4()),
        "title": "x.pdf",
        "filetype_row_label": "Filetype #21",
        "intent_class": "Source data",
        "master_type": "RULES",
        "route_source": "DEFAULT_BY_FILETYPE",
        "sha256": SHA,
        "size": 1,
        "parse_status": "PENDING",
        "created_at": "2026-10-27T09:00:00Z",
    }
    assert MasterDocumentOut.model_validate(base).parse_code is None
    failed = MasterDocumentOut.model_validate(
        {**base, "parse_status": "FAILED", "parse_code": "ENCRYPTED"}
    )
    assert failed.parse_code == ErrorCode.ENCRYPTED


def test_e3_8b_a_master_document_is_its_own_cognee_item_source() -> None:
    assert ItemSource("MASTER_DOCUMENT") is ItemSource.MASTER_DOCUMENT
    item = KnowledgeItem.model_validate(
        {
            "source": "MASTER_DOCUMENT",
            "source_id": str(uuid.uuid4()),
            "sha256": SHA,
            "storage_key": f"t/p/derived/{SHA}",
        }
    )
    assert item.source is ItemSource.MASTER_DOCUMENT
