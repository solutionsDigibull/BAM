"""E14.1 `certify_sub_skill` on the Compose Postgres (#129).

The unit tests (`tests/unit/test_e14_1_certify.py`) run the same stage on SQLite, where the frozen
rows are guarded by a stand-in trigger. Here the real `CertifySubSkill` runs with the real
repositories against a scratch database migrated to head, so what blocks it, what it writes and what
can never change afterwards is the Postgres schema's answer: the shared `forbid_frozen_change`
trigger (SQLSTATE SB423) on `certifications` (0050) and `sub_skill_manifests` (0033).

The sandbox run is a `sandbox_runs` row inserted directly: no CubeSandbox (or local runner) is in
the `core` stack, and certification only reads the row. Every test seeds its own tenant and project;
the scratch database is dropped at the end of the module. Needs `make up`; not part of the unit run.
"""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sbam_api.certifications.models import CertificationRow
from sbam_api.manifests.models import SubSkillManifest
from sbam_contracts.build_dashboard import GateName
from sbam_contracts.manifest import SkillManifest
from sbam_workers.certify.subskill import BLOCKED, CertifySubSkill
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError

from .certify_seed import (
    ALL_GATES,
    BUNDLE,
    NOW,
    Build,
    add_test_run,
    current_suite,
    isatvon_of,
    scratch_db,
    seed_build,
)

pytestmark = pytest.mark.integration

FROZEN_SQLSTATE = "SB423"


@pytest.fixture(scope="module")
def sessions() -> Iterator[sessionmaker[Session]]:
    with scratch_db("sbam_e141") as (made, _):
        yield made


def certify(build: Build) -> uuid.UUID:
    stage = CertifySubSkill(build.sessions, isatvon_of, clock=lambda: NOW)
    return asyncio.run(stage.run(build.inp)).id


def blocked_with(build: Build) -> str:
    with pytest.raises(ApplicationError) as err:
        certify(build)
    assert err.value.type == BLOCKED and err.value.non_retryable, err.value
    return str(err.value)


def certificates(build: Build) -> list[CertificationRow]:
    with build.sessions() as s:
        query = select(CertificationRow).where(
            CertificationRow.tenant_id == build.ctx.tenant_id,
            CertificationRow.subject_id == build.inp.sub_skill_id,
        )
        return list(s.scalars(query))


def manifest_of(build: Build) -> SubSkillManifest:
    with build.sessions() as s:
        row = s.get(SubSkillManifest, build.manifest_id)
        assert row is not None
        return row


def assert_nothing_written(build: Build) -> None:
    assert certificates(build) == []
    row = manifest_of(build)
    assert not row.is_frozen
    assert row.manifest["skill_metadata"]["status"] == "DRAFT"
    assert row.manifest["governance"]["lifecycle"]["state"] == "DRAFT"
    assert "certification" not in row.manifest["sbam_build_record"]


def test_e14_1_t02_missing_evidence_blocks_db(sessions: sessionmaker[Session]) -> None:
    """A build whose MYPY gate result is absent is refused by name, and nothing is written."""
    build = seed_build(sessions, gates=[g for g in ALL_GATES if g is not GateName.MYPY])
    add_test_run(build)
    message = blocked_with(build)
    assert "MISSING_EVIDENCE:MYPY" in message, message
    assert_nothing_written(build)


def test_e14_1_t03_local_docker_cannot_certify_db(sessions: sessionmaker[Session]) -> None:
    """3/3 green in the local-docker runner is evidence that never certifies."""
    build = seed_build(sessions, sandbox_mode="LOCAL_DOCKER_NONCERT")
    # What the judge writes for a green run of a non-certifying sandbox (judge.judge_run).
    add_test_run(build, state="NON_CERTIFYING", non_certifying=True)
    message = blocked_with(build)
    assert "NON_CERTIFYING_RUN" in message, message
    assert_nothing_written(build)


def _snapshot(s: Session, table: str, row_id: uuid.UUID) -> Any:
    return s.execute(
        text(f"SELECT to_jsonb(t) FROM sbam.{table} t WHERE id = :i"),  # noqa: S608 - fixed names
        {"i": row_id},
    ).scalar_one()


def _refused(sessions: sessionmaker[Session], table: str, row_id: uuid.UUID, sql: str) -> None:
    """`sql` on the row is refused by the frozen trigger and changes nothing."""
    with sessions() as s:
        before = _snapshot(s, table, row_id)
    with sessions() as s:
        with pytest.raises(DBAPIError, match="frozen") as err:
            s.execute(text(sql), {"i": row_id})
            s.commit()
        assert getattr(err.value.orig, "sqlstate", None) == FROZEN_SQLSTATE, err.value
        s.rollback()
    with sessions() as s:
        assert _snapshot(s, table, row_id) == before, f"{sql} changed the row"


def test_e14_1_certificate_and_sealed_manifest_are_frozen_in_postgres(
    sessions: sessionmaker[Session],
) -> None:
    """Full evidence on a certifying run certifies; then Postgres itself refuses any UPDATE or
    DELETE of the certificate and of the sealed manifest (the proof behind the SQLite stand-in)."""
    build = seed_build(sessions, sandbox_mode="CUBESANDBOX")
    add_test_run(build)
    cert_id = certify(build)

    (cert,) = certificates(build)
    assert cert.id == cert_id and cert.is_frozen and cert.level == "SUB_SKILL"
    assert cert.content_sha256 == "sha256:" + BUNDLE
    assert cert.evidence["sandbox_run_id"] == str(build.sandbox_run_id)
    assert cert.evidence["suite_sha256"] == current_suite(build)
    sealed = manifest_of(build)
    assert sealed.is_frozen
    approved = SkillManifest.model_validate(sealed.manifest)
    assert approved.skill_metadata.status == "APPROVED"
    assert approved.governance.lifecycle.immutable_after_approval
    assert sealed.sha256 == approved.governance.deterministic_hash

    for sql in (
        "UPDATE sbam.certifications SET evidence = '{}'::jsonb WHERE id = :i",
        "UPDATE sbam.certifications SET content_sha256 = 'sha256:' || repeat('0', 64)"
        " WHERE id = :i",
        "UPDATE sbam.certifications SET certified_at = now() WHERE id = :i",
        "DELETE FROM sbam.certifications WHERE id = :i",
    ):
        _refused(sessions, "certifications", cert_id, sql)
    for sql in (
        "UPDATE sbam.sub_skill_manifests SET manifest = '{}'::jsonb WHERE id = :i",
        "UPDATE sbam.sub_skill_manifests SET is_frozen = false WHERE id = :i",  # un-seal it
        "UPDATE sbam.sub_skill_manifests SET sha256 = 'sha256:' || repeat('0', 64)"
        " WHERE id = :i",
        "DELETE FROM sbam.sub_skill_manifests WHERE id = :i",
    ):
        _refused(sessions, "sub_skill_manifests", build.manifest_id, sql)

    # still exactly one certificate, and a retried activity finds it instead of writing another
    assert certify(build) == cert_id
    assert [c.id for c in certificates(build)] == [cert_id]
