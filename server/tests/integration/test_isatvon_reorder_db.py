"""A-29 (#455) on the Compose Postgres: reorders and deletes racing in one Macro Skill. Each one
renumbers every sibling, so two of them used to take row locks crosswise (`deadlock detected`, a
500). Real row locks: SQLite ignores `FOR UPDATE` and cannot deadlock. A scratch database migrated
to head; dropped at the end."""

import copy
import json
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from alembic import command
from fixtures import fixture_path
from sbam_api.core.conventions import ApiError
from sbam_api.core.ids import uuid7
from sbam_api.db.migrate import config
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.isatvon import edits
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.sow.models import SowVersion
from sbam_api.workflows.models import Workflow
from sbam_contracts.canonical import sha256_of
from sbam_contracts.isatvon_workbench import SubSkillPatch
from sqlalchemy import pool, select, text
from sqlalchemy.orm import Session, sessionmaker

from .test_db import url
from .test_sow_approve_db import FDE, TENANT, a_sow

pytestmark = pytest.mark.integration

DB = f"sbam_a29_{uuid.uuid4().hex[:10]}"
WAVES, WIDTH = 6, 3  # 18 requests, three at the same moment (the audit's reproduction)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as mp:
        yield mp


@pytest.fixture(scope="module")
def sessions(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    admin = make_engine(url("postgres", "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{DB}" OWNER sbam'))
    engine = make_engine(url(database=DB), poolclass=pool.NullPool)
    try:
        monkeypatch_module.setenv("DATABASE_URL", url(database=DB))
        command.upgrade(config(), "head")
        yield make_sessionmaker(engine)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)'))
        admin.dispose()


class Macro:
    """One Workflow in ISATVON_REVIEW with one Macro Skill holding `count` Sub-Skills."""

    def __init__(self, sessions: sessionmaker[Session], count: int = 5) -> None:
        self.sessions = sessions
        self.project_id, _ = a_sow(sessions)
        raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text("utf-8"))
        with sessions() as s:
            version = s.scalars(select(SowVersion).where(SowVersion.project_id == self.project_id))
            self.wf, self.macro = uuid7(), uuid7()
            workflow = Workflow(
                id=self.wf,
                tenant_id=TENANT,
                project_id=self.project_id,
                sow_version_id=version.one().id,
                major=1,
                version="1.0.0",
                name="A-29",
                created_by="SYSTEM",
            )
            workflow.state = "ISATVON_REVIEW"
            s.add(workflow)
            s.flush()
            macro = MacroSkill(
                id=self.macro,
                tenant_id=TENANT,
                project_id=self.project_id,
                workflow_id=self.wf,
                sow_version_id=workflow.sow_version_id,
                macro_category="MS01_Investor_Registration_Access",
                name="Access",
                description="Access.",
                order=1,
                sow_refs=["M-ACCESS"],
                agent_ref=None,
                validation_errors=[],
            )
            macro.state = "PENDING"
            s.add(macro)
            s.flush()
            self.subs: list[uuid.UUID] = []
            self.etags: dict[uuid.UUID, str] = {}
            for n in range(1, count + 1):
                body = copy.deepcopy(raw)
                body["skill_metadata"]["skill_id"] = f"MS01.{n}_Skill_{n}"
                body["skill_metadata"]["skill_name"] = f"Skill {n}"
                row = SubSkill(
                    id=uuid7(),
                    tenant_id=TENANT,
                    project_id=self.project_id,
                    macro_skill_id=self.macro,
                    workflow_id=self.wf,
                    skill_id=body["skill_metadata"]["skill_id"],
                    skill_name=body["skill_metadata"]["skill_name"],
                    order=n,
                    isatvon=body,
                    isatvon_framework_version="1.4",
                    sow_refs=["F-ACCESS-1"],
                    validation_errors=[],
                    content_sha256=sha256_of(body).removeprefix("sha256:"),
                )
                row.state = "VALID"
                s.add(row)
                self.subs.append(row.id)
                self.etags[row.id] = f'"{row.content_sha256}"'
            s.commit()

    def reorder(self, sub_id: uuid.UUID, order: int) -> None:
        with self.sessions() as s:
            edits.edit_sub_skill(
                s,
                FDE,
                self.project_id,
                self.wf,
                sub_id,
                SubSkillPatch(order=order),
                self.etags[sub_id],
            )
            s.commit()

    def delete(self, sub_id: uuid.UUID) -> None:
        with self.sessions() as s:
            edits.delete_sub_skill(s, FDE, self.project_id, self.wf, sub_id, None)
            s.commit()

    def orders(self) -> list[int]:
        with self.sessions() as s:
            rows = s.scalars(
                select(SubSkill)
                .where(SubSkill.macro_skill_id == self.macro, SubSkill.deleted_at.is_(None))
                .order_by(SubSkill.order)
            ).all()
            return [r.order for r in rows]


def race(jobs: list[Any]) -> list[BaseException]:
    """The jobs run in waves of WIDTH that start together; whatever each one raised, any order."""
    barrier = threading.Barrier(WIDTH)

    def run(job: Any) -> BaseException | None:
        barrier.wait(timeout=30)
        try:
            job()
        except BaseException as exc:  # the test inspects every kind
            return exc
        return None

    with ThreadPoolExecutor(max_workers=WIDTH) as pool_:
        outcomes = list(pool_.map(run, jobs))
    return [o for o in outcomes if o is not None]


def refused(errors: list[BaseException]) -> list[BaseException]:
    """What is left once the answers the API is meant to give (4xx) are taken out: a 5xx, or any
    database error that was not turned into an answer."""
    return [e for e in errors if not (isinstance(e, ApiError) and e.status < 500)]


def test_a29_t01_parallel_reorders_in_one_macro_never_fail(
    sessions: sessionmaker[Session],
) -> None:
    world = Macro(sessions)
    jobs = [
        (lambda sid=world.subs[i % 5], to=(i * 2) % 5 + 1: world.reorder(sid, to))
        for i in range(WAVES * WIDTH)
    ]
    assert refused(race(jobs)) == []
    assert world.orders() == [1, 2, 3, 4, 5]  # still contiguous


def test_a29_t02_racing_deletes_and_reorders_in_one_macro_never_fail(
    sessions: sessionmaker[Session],
) -> None:
    world = Macro(sessions)
    a, b, c, d, e = world.subs
    jobs = [
        lambda: world.delete(a),
        lambda: world.reorder(b, 1),
        lambda: world.reorder(c, 5),
        lambda: world.reorder(d, 2),
        lambda: world.delete(e),
        lambda: world.reorder(b, 3),
    ] * 3
    assert refused(race(jobs)) == []
    left = world.orders()
    assert left == list(range(1, len(left) + 1)) and len(left) == 3  # a and e are gone
