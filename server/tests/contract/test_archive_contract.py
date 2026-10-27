"""E4.7 contract tests: the archive report and the path of a member inside its archive."""

import uuid

import pytest
from pydantic import ValidationError
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_RATIO,
    MAX_ARCHIVE_UNPACKED_BYTES,
    ArchiveReport,
    DocumentOut,
    ParseReport,
    RejectedMember,
)


def test_e4_7_the_limits_are_the_documented_numbers() -> None:
    assert MAX_ARCHIVE_MEMBERS == 20
    assert MAX_ARCHIVE_UNPACKED_BYTES == 100 * 1024 * 1024
    assert MAX_ARCHIVE_RATIO == 100


def test_e4_7_the_codes_an_archive_can_give_are_error_codes() -> None:
    for code in ("PATH_UNSAFE", "NESTED_ARCHIVE", "ARCHIVE_LIMIT", "ENCRYPTED", "UNSUPPORTED_TYPE"):
        assert ErrorCode(code).value == code


def test_e4_7_a_report_without_an_archive_still_validates() -> None:
    assert ParseReport.model_validate({"pages": 1}).archive is None


def test_e4_7_an_archive_report_lists_each_rejected_member_with_its_code() -> None:
    report = ParseReport.model_validate(
        {
            "archive": {
                "members_total": 4,
                "accepted": 2,
                "rejected": [
                    {"path": "../../x.md", "code": "PATH_UNSAFE"},
                    {"path": "inner.zip", "code": "NESTED_ARCHIVE"},
                ],
            }
        }
    )
    assert report.archive is not None
    assert [(m.path, m.code) for m in report.archive.rejected] == [
        ("../../x.md", ErrorCode.PATH_UNSAFE),
        ("inner.zip", ErrorCode.NESTED_ARCHIVE),
    ]
    assert ParseReport.model_validate(report.model_dump(mode="json")) == report


def test_e4_7_an_empty_archive_report_defaults_to_nothing() -> None:
    assert ArchiveReport().model_dump() == {"members_total": 0, "accepted": 0, "rejected": []}


@pytest.mark.parametrize(
    "member",
    [
        {"path": "", "code": "PATH_UNSAFE"},
        {"path": "x" * 1025, "code": "PATH_UNSAFE"},
        {"path": "a.md", "code": "NOT_A_CODE"},
        {"path": "a.md", "code": "PATH_UNSAFE", "extra": 1},
    ],
)
def test_e4_7_a_rejected_member_with_a_bad_field_is_refused(member: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RejectedMember.model_validate(member)


@pytest.mark.parametrize("report", [{"members_total": -1}, {"accepted": -1}])
def test_e4_7_counts_cannot_be_negative(report: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        ArchiveReport.model_validate(report)


def test_e4_7_a_document_may_say_where_it_sits_in_its_archive() -> None:
    base = {
        "id": str(uuid.uuid4()),
        "title": "q3.csv",
        "state": "PARSED",
        "size": 1,
        "mime": "text/csv",
        "sha256": "0" * 64,
        "created_at": "2026-10-27T09:00:00Z",
    }
    assert DocumentOut.model_validate(base).archive_path is None
    member = DocumentOut.model_validate(
        {**base, "parent_document_id": str(uuid.uuid4()), "archive_path": "reports/q3.csv"}
    )
    assert member.archive_path == "reports/q3.csv"
