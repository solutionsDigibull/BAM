"""Test helpers for E7.2: a project whose approved SOW v1 has a Workflow with Macro and Sub-Skills,
and a scope change (v2 approved over it, its impact stored by the preview's own function).

v1's Workflow: Macro MS01 (M-A) with A1 (F-A-1, CERTIFIED), A2 (F-A-2, CERTIFIED); Macro MS02 (M-B)
with B1 (F-B-1, VALID, never built), B2 (F-B-2, CERTIFIED).
"""

import copy
import uuid
from typing import Any

from sbam_api.audit.models import AuditLog
from sbam_api.core.ids import uuid7
from sbam_api.db.repository import TenantContext
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.sow.impact import record_impact
from sbam_api.sow.models import Sow, SowImpactRecord, SowVersion
from sbam_api.sow.service import canonical_sha256
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import CarryOverInput
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from workflows_db import add_project, workflow_sessions

TENANT = uuid.uuid4()
DOC: dict[str, Any] = {
    "modules": [{"id": "M-A", "name": "A"}, {"id": "M-B", "name": "B"}],
    "features": [
        {"id": "F-A-1", "module_id": "M-A", "name": "a1"},
        {"id": "F-A-2", "module_id": "M-A", "name": "a2"},
        {"id": "F-B-1", "module_id": "M-B", "name": "b1"},
        {"id": "F-B-2", "module_id": "M-B", "name": "b2"},
    ],
}
SUBS = {
    "A1": ("MS01", ["F-A-1"], "CERTIFIED"),
    "A2": ("MS01", ["F-A-2"], "CERTIFIED"),
    "B1": ("MS02", ["F-B-1"], "VALID"),
    "B2": ("MS02", ["F-B-2"], "CERTIFIED"),
}


class World:
    def __init__(self) -> None:
        self.sessions: sessionmaker[Session] = workflow_sessions()
        bind = self.sessions.kw["bind"]
        for table in (MacroSkill, SubSkill, SowImpactRecord):
            table.__table__.create(bind, checkfirst=True)  # type: ignore[attr-defined]
        self.project = add_project(self.sessions, TENANT)
        self.ctx = TenantContext(TENANT, self.project)
        self.sow_id = self._add(Sow(tenant_id=TENANT, project_id=self.project))
        self.v1 = self.version(1, DOC)
        self.point_sow_at(self.v1)
        self.w1 = self.workflow(self.v1, 1)
        self.macros = {
            "MS01": self.macro(self.w1, "MS01_A", 1, ["M-A"]),
            "MS02": self.macro(self.w1, "MS02_B", 2, ["M-B"]),
        }
        self.subs = {
            name: self.sub_skill(self.w1, self.macros[m], f"{m}.{i}_{name}", i, refs, state)
            for i, (name, (m, refs, state)) in enumerate(SUBS.items(), start=1)
        }

    def _add(self, row: Any, state: str | None = None) -> uuid.UUID:
        with self.sessions() as s:
            if state is not None:
                row.state = state
            s.add(row)
            s.commit()
            return uuid.UUID(str(row.id))

    def version(self, number: int, doc: dict[str, Any]) -> uuid.UUID:
        return self._add(
            SowVersion(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.project,
                sow_id=self.sow_id,
                version=number,
                source="EDITED",
                run_id=uuid7(),
                knowledge_version_id=uuid7(),
                structured=doc,
                sha256=canonical_sha256(doc),
            ),
            "APPROVED",
        )

    def workflow(self, version: uuid.UUID, major: int) -> uuid.UUID:
        return self._add(
            Workflow(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.project,
                sow_version_id=version,
                major=major,
                version=f"{major}.0.0",
                name=f"P v{major}",
            ),
            "CREATED" if major > 1 else "FROZEN",
        )

    def macro(self, wf: uuid.UUID, category: str, order: int, refs: list[str]) -> uuid.UUID:
        return self._add(
            MacroSkill(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.project,
                workflow_id=wf,
                sow_version_id=self.v1,
                macro_category=category,
                name=category,
                description=".",
                order=order,
                sow_refs=refs,
                agent_ref=None,
                validation_errors=[],
            ),
            "CERTIFIED",
        )

    def sub_skill(
        self,
        wf: uuid.UUID,
        macro: uuid.UUID,
        skill_id: str,
        order: int,
        refs: list[str],
        state: str,
    ) -> uuid.UUID:
        return self._add(
            SubSkill(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.project,
                macro_skill_id=macro,
                workflow_id=wf,
                skill_id=skill_id,
                skill_name=skill_id,
                order=order,
                isatvon={"skill_metadata": {"skill_id": skill_id}},
                isatvon_framework_version="1.4",
                sow_refs=refs,
                validation_errors=[],
                content_sha256=str(order) * 64,
            ),
            state,
        )

    def point_sow_at(self, version: uuid.UUID) -> None:
        with self.sessions() as s:
            sow = s.get(Sow, self.sow_id)
            assert sow is not None
            sow.current_version_id = sow.approved_version_id = version
            s.commit()

    def approve_v2(self, doc: dict[str, Any]) -> uuid.UUID:
        """v2 = `doc` approved over v1 as E7.1 + E7.2 leave it: v1 SUPERSEDED, impact stored."""
        self.v2 = self.version(2, doc)
        with self.sessions() as s:
            base, draft = s.get(SowVersion, self.v1), s.get(SowVersion, self.v2)
            assert base is not None and draft is not None
            record_impact(s, self.ctx, base, draft)
            base.state = "SUPERSEDED"
            s.commit()
        self.point_sow_at(self.v2)
        return self.v2

    def scope_change(self, doc: dict[str, Any]) -> CarryOverInput:
        """Approve v2 = `doc` over v1 (its impact stored), create Workflow 2.0.0."""
        v2 = self.approve_v2(doc)
        self.w2 = self.workflow(v2, 2)
        return CarryOverInput(
            tenant_id=TENANT,
            project_id=self.project,
            run_id=uuid.uuid4(),
            correlation_id="cid-e7-2",
            workflow_id=self.w2,
            sow_version_id=v2,
        )

    def rows(self, wf: uuid.UUID) -> dict[str, SubSkill]:
        with self.sessions() as s:
            found = {
                r.skill_id: r for r in s.scalars(select(SubSkill).where(SubSkill.workflow_id == wf))
            }
            s.expunge_all()
            return found

    def macro_rows(self, wf: uuid.UUID) -> list[MacroSkill]:
        with self.sessions() as s:
            found = list(
                s.scalars(
                    select(MacroSkill)
                    .where(MacroSkill.workflow_id == wf)
                    .order_by(MacroSkill.order)
                )
            )
            s.expunge_all()
            return found

    def trail(self, row_id: uuid.UUID) -> list[str | None]:
        with self.sessions() as s:
            rows = s.scalars(
                select(AuditLog).where(AuditLog.entity_id == str(row_id)).order_by(AuditLog.id)
            )
            return [(r.after or {}).get("state") for r in rows]


def changed(**names: str) -> dict[str, Any]:
    """DOC with features renamed (`F-A-1="x"`)."""
    doc = copy.deepcopy(DOC)
    for f in doc["features"]:
        if f["id"] in names:
            f["name"] = names[f["id"]]
    return doc
