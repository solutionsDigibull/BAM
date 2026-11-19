"""E15.3-T06 frozen tamper (#137): a frozen Workflow version cannot be changed through the API,
through SQL, or by overwriting its stored files, and each attempt changes nothing.

Built on the E14.4 freeze harness (tests/api/test_e14_4_freeze_api.py): a CERTIFIED Workflow is
frozen by the real `OrderedFreezer`, with one real code bundle in `LocalDiskBackend`. Then:

* API: every write route on the frozen Workflow (PATCH rename, PATCH / DELETE Sub-Skill, PUT
  schema-sql source) answers 423 problem+json;
* SQL: an UPDATE or DELETE of a frozen row is refused by the frozen trigger (the SQLite stand-in
  here; tests/integration/test_e15_3_t06_frozen_tamper_db.py checks SQLSTATE SB423 on Postgres);
* storage: the frozen file is read-only on disk, and a forced overwrite is caught by the freeze's
  `read_back` as `HashMismatch`.

After each attempt a snapshot of every frozen row (and the stored bytes) is unchanged.
"""

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

import test_e14_4_freeze_api as base
import test_wg18_commit_api as wg18
from sbam_api.builds.models import CodeAttempt
from sbam_api.certifications.models import CertificationRow
from sbam_api.core.conventions import etag
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.manifests.models import SubSkillManifest, WorkflowManifestRow
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.versioning.freezer import FrozenFile, HashMismatch, read_back
from sbam_api.versioning.models import WorkflowVersionRow
from sbam_api.workflows.models import Workflow
from test_auth import TENANT_A, problem
from test_project_workspace import Harness
from test_wg16_connections_api import WORKFLOW

h = wg18.h  # the freeze harness's fixtures
hv = base.hv

# Every table a freeze writes or marks; the snapshot covers all of their rows.
FROZEN_TABLES: tuple[type[Any], ...] = (
    Workflow,
    SubSkill,
    MacroSkill,
    CodeAttempt,
    CertificationRow,
    WorkflowManifestRow,
    SubSkillManifest,
    WorkflowVersionRow,
)
W = "/projects/{project}/workflows/{workflow}"


@pytest.fixture
def frozen(hv: Harness) -> Iterator[tuple[Harness, str]]:
    """Version 1.0.0 frozen, with its code bundle stored for real. Yields (harness, bundle key)."""
    sha = base.prepared(hv)
    stored = wg18.storage(hv).put(
        b"frozen bundle bytes",
        StorageScope(TENANT_A, uuid.UUID(hv.project), WORKFLOW),
        Kind.CODE,
        "application/x-tar",
    )
    with hv.sessions() as s:
        s.scalars(select(CodeAttempt)).one().bundle_key = stored.storage_key
        s.commit()
    base.freeze(hv, sha)
    assert [v.version for v in base.versions(hv)] == ["1.0.0"]
    yield hv, stored.storage_key


def snapshot(h: Harness) -> dict[str, list[dict[str, Any]]]:
    """Every column of every row of the frozen tables, ordered by id."""
    out: dict[str, list[dict[str, Any]]] = {}
    with h.sessions() as s:
        for model in FROZEN_TABLES:
            columns = [c.key for c in model.__table__.columns]
            rows = s.scalars(select(model).order_by(model.id)).all()
            out[model.__tablename__] = [{c: getattr(r, c) for c in columns} for r in rows]
    return out


def sub_skill(h: Harness) -> SubSkill:
    with h.sessions() as s:
        return s.scalars(select(SubSkill)).one()


# --- API ----------------------------------------------------------------------------------------


def test_e15_3_t06_api_writes_on_a_frozen_version_are_423_and_change_nothing(
    frozen: tuple[Harness, str],
) -> None:
    h, key = frozen
    w = W.format(project=h.project, workflow=WORKFLOW)
    sub = sub_skill(h)
    with h.sessions() as s:
        workflow = s.get(Workflow, WORKFLOW)
        assert workflow is not None and workflow.state == "FROZEN"
        workflow_etag = etag(workflow.row_version)
    sub_etag = f'"{sub.content_sha256}"'
    attempts: list[tuple[str, str, dict[str, Any], dict[str, str], str]] = [
        (
            "PATCH",
            w,
            {"name": "Renamed after freeze"},
            {"If-Match": workflow_etag},
            "RESOURCE_LOCKED",
        ),
        (
            "PATCH",
            f"{w}/sub-skills/{sub.id}",
            {"changes": {"skill_metadata.skill_name": "tampered"}},
            {"If-Match": sub_etag},
            "WORKFLOW_FROZEN",
        ),
        ("DELETE", f"{w}/sub-skills/{sub.id}", {}, {"If-Match": sub_etag}, "WORKFLOW_FROZEN"),
        (
            "PUT",
            f"{w}/schema-sql/source",
            {"document_id": "00000000-0000-4000-8000-000000000001"},
            {},
            "WORKFLOW_FROZEN",
        ),
    ]
    before = snapshot(h)
    stored = wg18.storage(h).get(key, verify=True)
    for method, url, body, headers, code in attempts:
        kwargs: dict[str, Any] = {"headers": headers}
        if body:
            kwargs["json"] = body
        problem(h.call(method, url, h.fde(), **kwargs), 423, code)
        assert snapshot(h) == before, f"{method} {url} changed a frozen row"
    assert wg18.storage(h).get(key, verify=True) == stored
    assert [v.version for v in base.versions(h)] == ["1.0.0"]


# --- SQL ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        update(WorkflowVersionRow).values(change_reason="tampered"),
        update(WorkflowVersionRow).values(is_frozen=False),
        delete(WorkflowVersionRow),
        update(WorkflowManifestRow).values(manifest={}),
        update(WorkflowManifestRow).values(sha256="0" * 71),
        delete(WorkflowManifestRow),
        update(CertificationRow).values(content_sha256="sha256:" + "0" * 64),
        delete(CertificationRow),
    ],
    ids=lambda s: str(s).split("\n")[0][:60],
)
def test_e15_3_t06_sql_update_or_delete_of_a_frozen_row_is_refused(
    frozen: tuple[Harness, str], statement: Any
) -> None:
    h, _ = frozen
    before = snapshot(h)
    assert before[statement.table.name], "no frozen row to tamper with: the case would be vacuous"
    with h.sessions() as s:
        with pytest.raises(Exception, match="frozen"):
            s.execute(statement)
            s.commit()
        s.rollback()
    assert snapshot(h) == before


# --- storage ------------------------------------------------------------------------------------


def test_e15_3_t06_a_frozen_file_is_read_only_on_disk(frozen: tuple[Harness, str]) -> None:
    h, key = frozen
    path: Path = wg18.storage(h)._path(key)  # type: ignore[attr-defined]
    if getattr(os, "geteuid", lambda: 1)() == 0:
        pytest.skip("root ignores file modes")
    with pytest.raises(PermissionError):
        path.write_bytes(b"tampered")
    assert wg18.storage(h).get(key, verify=True) == b"frozen bundle bytes"


def test_e15_3_t06_a_forced_overwrite_of_a_frozen_file_is_a_hash_mismatch(
    frozen: tuple[Harness, str],
) -> None:
    h, key = frozen
    before = snapshot(h)
    path: Path = wg18.storage(h)._path(key)  # type: ignore[attr-defined]
    path.chmod(0o644)  # an attacker with the storage host's rights
    path.write_bytes(b"tampered")

    with pytest.raises(HashMismatch) as error:
        read_back(wg18.storage(h), [FrozenFile(key)])

    assert error.value.status == 409 and error.value.code.value == "HASH_MISMATCH"
    assert snapshot(h) == before  # detected, and the frozen rows still say what was certified
