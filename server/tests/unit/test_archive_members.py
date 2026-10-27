"""E4.7 archive members as child documents: `register_attachments` and `finish_attachments` on
SQLite and a temp-dir storage (the stack is covered in tests/integration/test_archive_stack.py)."""

from pathlib import Path
from typing import Any

import pytest
from sbam_api.ingest.models import Document, DocumentVersion
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import ParseReport
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest.attachments import (
    finish_attachments_for,
    register_attachments_for,
)
from sbam_workers.ingest.parsing import parse_version
from sqlalchemy import select
from test_attachments import children, deps, fill_project, set_child
from test_loaders_archive import make_zip, targz
from test_parsing import Harness, root  # noqa: F401 - root: the short-path fixture


@pytest.fixture
def h(root: Path) -> Harness:  # noqa: F811 - the imported fixture
    return Harness(root)


def parsed_archive(h: Harness, data: bytes, kind: str = "zip", **fields: Any) -> StageRef:
    ref = h.add(f"files.{kind}", data, detected=kind, **fields)
    assert parse_version(h.deps, ref).parsed
    return ref


def report(h: Harness, ref: StageRef) -> ParseReport:
    raw = h.version(ref).parse_report
    assert raw is not None
    return ParseReport.model_validate(raw)


def titles(h: Harness) -> dict[str, tuple[str | None, str]]:
    """Document title -> (archive_path, state of its version) for every child in the project."""
    with h.sessions() as s:
        rows = s.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.parent_document_id.is_not(None))
        ).all()
        return {d.title: (d.archive_path, v.state) for d, v in rows}


def test_e4_7_t01_every_member_becomes_a_child_with_its_path(h: Harness) -> None:
    members = {"a.md": b"# a", "reports/b.csv": b"x,y\n1,2\n", "reports/c.md": b"# c"}
    parent = parsed_archive(h, make_zip(members))
    ids = register_attachments_for(deps(h), parent).ids
    assert len(ids) == 3 and len(children(h, parent)) == 3
    assert titles(h) == {
        "a.md": ("a.md", "UPLOADED"),
        "b.csv": ("reports/b.csv", "UPLOADED"),
        "c.md": ("reports/c.md", "UPLOADED"),
    }
    archive = report(h, parent).archive
    assert archive is not None and archive.members_total == 3 and archive.accepted == 3
    assert archive.rejected == []
    assert h.version(parent).state == "PARSED" and report(h, parent).attachments == []
    for _, child in children(h, parent):
        assert h.storage.get(child.storage_key) in members.values()


def test_e4_7_a_tar_gz_is_registered_the_same_way(h: Harness) -> None:
    parent = parsed_archive(h, targz({"a.md": b"# a", "d/b.md": b"# b"}), "tar.gz")
    assert len(register_attachments_for(deps(h), parent).ids) == 2
    assert {path for path, _ in titles(h).values()} == {"a.md", "d/b.md"}


def test_e4_7_registering_twice_makes_the_children_once(h: Harness) -> None:
    parent = parsed_archive(h, make_zip({"a.md": b"# a", "b.md": b"# b"}))
    first = register_attachments_for(deps(h), parent).ids
    assert register_attachments_for(deps(h), parent).ids == first  # an activity retry
    assert len(children(h, parent)) == 2


def test_e4_7_the_same_content_twice_makes_two_children(h: Harness) -> None:
    parent = parsed_archive(h, make_zip({"one/a.md": b"# same", "two/a.md": b"# same"}))
    assert len(set(register_attachments_for(deps(h), parent).ids)) == 2
    with h.sessions() as s:
        paths = [d.archive_path for d in s.scalars(select(Document)) if d.parent_document_id]
    assert sorted(path or "" for path in paths) == ["one/a.md", "two/a.md"]


def test_e4_7_t05_members_beyond_the_20_file_budget_are_listed_not_added(h: Harness) -> None:
    fill_project(h, 17)  # 17 others and the archive itself make 18 active documents
    members = {f"m{n}.md": f"member {n}".encode() for n in range(4)}
    parent = parsed_archive(h, make_zip(members))
    assert len(register_attachments_for(deps(h), parent).ids) == 2
    archive = report(h, parent).archive
    assert archive is not None and (archive.members_total, archive.accepted) == (4, 2)
    assert [(m.path, m.code) for m in archive.rejected] == [
        ("m2.md", ErrorCode.FILE_LIMIT_REACHED),
        ("m3.md", ErrorCode.FILE_LIMIT_REACHED),
    ]
    assert h.version(parent).state == "PARSED" and len(children(h, parent)) == 2


def test_e4_7_the_loaders_refusals_stay_on_the_archives_report(h: Harness) -> None:
    parent = parsed_archive(
        h, make_zip({"../x.md": b"a", "inner.zip": b"z", "setup.exe": b"e", "ok.md": b"# ok"})
    )
    assert len(register_attachments_for(deps(h), parent).ids) == 1
    archive = report(h, parent).archive
    assert archive is not None and (archive.members_total, archive.accepted) == (4, 1)
    assert [(m.path, m.code) for m in archive.rejected] == [
        ("../x.md", ErrorCode.PATH_UNSAFE),
        ("inner.zip", ErrorCode.NESTED_ARCHIVE),
        ("setup.exe", ErrorCode.UNSUPPORTED_TYPE),
    ]


def test_e4_7_an_archive_that_is_itself_a_child_has_all_its_members_refused(h: Harness) -> None:
    mail = h.add("rfq.eml", b"unused", detected="eml")
    with h.sessions() as s:  # the archive is an attachment of an e-mail: its own depth is 1
        parent_doc = s.get(DocumentVersion, mail.ref_id)
        assert parent_doc is not None
        outer = parent_doc.document_id
    inner = parsed_archive(h, make_zip({"a.md": b"# a", "b.md": b"# b"}))
    with h.sessions() as s:
        version = s.get(DocumentVersion, inner.ref_id)
        assert version is not None
        document = s.get(Document, version.document_id)
        assert document is not None
        document.parent_document_id = outer
        s.commit()
    assert register_attachments_for(deps(h), inner).ids == []
    archive = report(h, inner).archive
    assert archive is not None and archive.accepted == 0
    assert [m.code for m in archive.rejected] == [ErrorCode.NESTED_ARCHIVE] * 2
    assert children(h, inner) == []


def test_e4_7_t06_a_member_that_fails_its_scan_warns_on_the_archive_which_stays_parsed(
    h: Harness,
) -> None:
    parent = parsed_archive(h, make_zip({"ok.md": b"# ok", "bad/eicar.txt": b"x" * 10}))
    ok, bad = register_attachments_for(deps(h), parent).ids
    set_child(h, ok, state="PARSED")
    set_child(h, bad, state="REJECTED", reject_code="MALWARE")
    assert finish_attachments_for(deps(h), parent).ids == [ok, bad]
    assert h.version(parent).state == "PARSED"
    assert report(h, parent).warnings == ["Member 'bad/eicar.txt' was rejected: MALWARE"]


def test_e4_7_finishing_notes_refused_members_unreadable_members_and_is_repeatable(
    h: Harness,
) -> None:
    parent = parsed_archive(h, make_zip({"../x.md": b"a", "a.md": b"# a", "b.csv": b"c"}))
    a, b = register_attachments_for(deps(h), parent).ids
    set_child(h, a, state="PARSE_FAILED", parse_code="CORRUPT")
    set_child(h, b, state="PARSED")
    for _ in range(2):  # an activity retry writes the same warnings, not twice
        finish_attachments_for(deps(h), parent)
    assert report(h, parent).warnings == [
        "Member '../x.md' was not taken: PATH_UNSAFE",
        "Member 'a.md' could not be read: CORRUPT",
    ]


def test_e4_7_a_version_that_is_not_an_archive_is_left_alone(h: Harness) -> None:
    note = h.add("note.md", b"# hi")
    assert parse_version(h.deps, note).parsed
    assert register_attachments_for(deps(h), note).ids == []
    assert report(h, note).archive is None
