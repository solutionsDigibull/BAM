"""E8.1 `create_workflow` step on SQLite with the real tables: the approval re-check, one Workflow
per approved SOW version, MAJOR numbering, and the pipeline's create transition finding the row."""

import copy
import logging
import uuid

import pytest
from sbam_api.sow.models import SowVersion
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import TransitionRequest
from sbam_contracts.state_machines import Actor, MachineName
from sbam_workers import mirror
from sbam_workers.pipeline_state import apply_pipeline_transition
from sbam_workers.workflows.create import HASH_MISMATCH, create_for_approval
from sqlalchemy.orm import Session, sessionmaker
from temporalio.exceptions import ApplicationError
from workflows_db import (
    STRUCTURED,
    add_project,
    create_input,
    real_models,
    sow_version,
    workflow_sessions,
    workflows,
)

TENANT = uuid.uuid4()


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    return workflow_sessions()


def mismatch(sessions: sessionmaker[Session], inp: object) -> ApplicationError:
    with pytest.raises(ApplicationError) as err:
        create_for_approval(sessions, inp)  # type: ignore[arg-type]
    assert err.value.type == HASH_MISMATCH and err.value.non_retryable
    return err.value


def test_e8_1_creates_1_0_0_by_system_named_after_the_project(
    sessions: sessionmaker[Session],
) -> None:
    project = add_project(sessions, TENANT, name="Cable quoting")
    version, sha = sow_version(sessions, TENANT, project)
    result = create_for_approval(sessions, create_input(TENANT, project, version, sha))
    (row,) = workflows(sessions, project)
    assert row.id == result.workflow_id
    assert (row.version, row.name, row.created_by, row.state) == (
        "1.0.0",
        "Cable quoting v1",
        "SYSTEM",
        "CREATED",
    )


def test_e8_1_t04_a_retry_or_second_signal_returns_the_same_workflow(
    sessions: sessionmaker[Session],
) -> None:
    project = add_project(sessions, TENANT)
    version, sha = sow_version(sessions, TENANT, project)
    first = create_for_approval(sessions, create_input(TENANT, project, version, sha))
    again = create_for_approval(sessions, create_input(TENANT, project, version, sha))
    assert first == again
    assert len(workflows(sessions, project)) == 1


def test_e8_1_t05_a_different_sha256_is_hash_mismatch_with_an_alert(
    sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    project = add_project(sessions, TENANT)
    version, _ = sow_version(sessions, TENANT, project)
    with caplog.at_level(logging.ERROR, logger="sbam_workers.workflows.create"):
        mismatch(sessions, create_input(TENANT, project, version, "cd" * 32))
    assert workflows(sessions, project) == []
    (alert,) = caplog.records
    assert alert.__dict__["alert"] == "SOW_HASH_MISMATCH"
    assert alert.__dict__["reason"] == "STORED_HASH"


def test_e8_1_t05_content_that_no_longer_hashes_to_the_approval_is_refused(
    sessions: sessionmaker[Session],
) -> None:
    project = add_project(sessions, TENANT)
    version, sha = sow_version(sessions, TENANT, project)
    tampered = copy.deepcopy(STRUCTURED)
    tampered["title"] = "Something else"
    with sessions() as s:
        row = s.get(SowVersion, version)
        assert row is not None
        row.structured = tampered  # sha256 column left as approved
        s.commit()
    mismatch(sessions, create_input(TENANT, project, version, sha))
    assert workflows(sessions, project) == []


def test_e8_1_a_version_not_approved_or_superseded_is_refused(
    sessions: sessionmaker[Session],
) -> None:
    project = add_project(sessions, TENANT)
    draft, draft_sha = sow_version(sessions, TENANT, project, approved=False)
    mismatch(sessions, create_input(TENANT, project, draft, draft_sha))
    v1, sha1 = sow_version(sessions, TENANT, project)
    doc = copy.deepcopy(STRUCTURED)
    doc["title"] = "Scope change"
    sow_version(sessions, TENANT, project, structured=doc)  # v3 approved: v1 SUPERSEDED
    mismatch(sessions, create_input(TENANT, project, v1, sha1))
    assert workflows(sessions, project) == []


def test_e8_1_another_tenant_or_project_never_sees_the_version(
    sessions: sessionmaker[Session],
) -> None:
    project = add_project(sessions, TENANT)
    version, sha = sow_version(sessions, TENANT, project)
    mismatch(sessions, create_input(uuid.uuid4(), project, version, sha))
    other = add_project(sessions, TENANT, name="Other")
    mismatch(sessions, create_input(TENANT, other, version, sha))
    assert workflows(sessions, project) == workflows(sessions, other) == []


def test_e8_1_t06_a_newly_approved_version_is_the_next_major(
    sessions: sessionmaker[Session],
) -> None:
    project = add_project(sessions, TENANT, name="EMS")
    v1, sha1 = sow_version(sessions, TENANT, project)
    first = create_for_approval(sessions, create_input(TENANT, project, v1, sha1))
    doc = copy.deepcopy(STRUCTURED)
    doc["title"] = "Scope change"
    v4, sha4 = sow_version(sessions, TENANT, project, structured=doc)
    second = create_for_approval(sessions, create_input(TENANT, project, v4, sha4))
    old, new = workflows(sessions, project)
    assert (old.id, old.version, old.sow_version_id, old.name) == (
        first.workflow_id,
        "1.0.0",
        v1,
        "EMS v1",
    )
    assert old.row_version == 1  # unchanged
    assert (new.id, new.version, new.sow_version_id, new.name) == (
        second.workflow_id,
        "2.0.0",
        v4,
        "EMS v2",
    )


def test_e8_1_the_pipelines_create_transition_finds_the_row_and_does_nothing(
    sessions: sessionmaker[Session],
) -> None:
    """A crash after the insert: the retry returns the row, and the pipeline's own `sow_approved`
    create (pipeline_state) is a no-op instead of a second insert."""
    project = add_project(sessions, TENANT)
    version, sha = sow_version(sessions, TENANT, project)
    result = create_for_approval(sessions, create_input(TENANT, project, version, sha))
    mirror.configure(sessions)
    with real_models():
        outcome = apply_pipeline_transition(
            TransitionRequest(
                tenant_id=TENANT,
                project_id=project,
                machine=MachineName.WORKFLOW,
                entity_id=result.workflow_id,
                event="sow_approved",
                actor=Actor.SYSTEM,
                actor_id="SYSTEM",
                correlation_id="cid-e8-1",
                idempotency_key="run:create_workflow",
                create=True,
            )
        )
    assert outcome.applied is False and outcome.to_state == "CREATED"
    with sessions() as s:
        assert s.query(Workflow).count() == 1
