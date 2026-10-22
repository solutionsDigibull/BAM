"""E3.8 contract: the models of `/admin/master-documents` (the routes are tested in tests/api)."""

import uuid

import pytest
from pydantic import ValidationError
from sbam_contracts.masters_api import (
    MasterDocumentOut,
    MasterDocumentPatch,
    MasterDocumentUpload,
    ParseStatus,
    RouteSource,
)

OUT = {
    "id": str(uuid.uuid4()),
    "title": "rates.xlsx",
    "filetype_row_label": "Filetype #23",
    "intent_class": "Source data (RFQ / BOM / quote)",
    "master_type": "SKILLS",
    "route_source": "DEFAULT_BY_FILETYPE",
    "sha256": "0" * 64,
    "size": 10,
    "parse_status": "PENDING",
    "created_at": "2026-10-22T10:00:00Z",
}


@pytest.mark.parametrize("master_type", ["ORGANIZATION", "RULES", "AGENTS", "SKILLS"])
def test_e3_8_a_document_routes_to_one_of_the_four_masters(master_type: str) -> None:
    assert MasterDocumentPatch(master_type=master_type).master_type == master_type  # type: ignore[arg-type]
    assert MasterDocumentUpload(master_type=master_type).master_type == master_type  # type: ignore[arg-type]


@pytest.mark.parametrize("master_type", ["TOOLING", "FILETYPE", "Skills", ""])
def test_e3_8_tooling_filetype_and_unknown_masters_are_not_routes(master_type: str) -> None:
    with pytest.raises(ValidationError):
        MasterDocumentPatch(master_type=master_type)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        MasterDocumentUpload(master_type=master_type)  # type: ignore[arg-type]


def test_e3_8_requests_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        MasterDocumentPatch.model_validate({"master_type": "RULES", "route_source": "x"})
    with pytest.raises(ValidationError):
        MasterDocumentUpload.model_validate({"sha256": "0" * 64})


def test_e3_8_an_entry_is_optional_on_the_patch() -> None:
    patch = MasterDocumentPatch(master_type="RULES")  # type: ignore[arg-type]
    assert patch.master_entry_id is None


def test_e3_8_a_document_that_is_still_scanning_has_no_scan_status() -> None:
    out = MasterDocumentOut.model_validate(OUT)
    assert out.scan_status is None and out.parse_status is ParseStatus.PENDING
    assert out.route_source is RouteSource.DEFAULT_BY_FILETYPE


def test_e3_8_the_route_sources_are_the_documented_ones() -> None:
    assert {s.value for s in RouteSource} == {
        "DEFAULT_BY_FILETYPE",
        "ADMIN_OVERRIDE",
        "CLASSIFIER",
    }
