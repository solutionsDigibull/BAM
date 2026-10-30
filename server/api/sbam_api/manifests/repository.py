"""Tenant- and project-scoped `sub_skill_manifests` / `gate_results` access (E9.1)."""

import uuid
from typing import Any

from sbam_api.db.repository import TenantScopedRepository
from sbam_api.manifests.models import GateResultRow, SubSkillManifest, WorkflowManifestRow


class SubSkillManifestRepository(TenantScopedRepository[SubSkillManifest]):
    model = SubSkillManifest

    def find(
        self, sub_skill_id: uuid.UUID, build_no: int, cycle: int, attempt: int
    ) -> SubSkillManifest | None:
        """The manifest for one build stage key, if the stage already ran."""
        query = self._scoped().where(
            SubSkillManifest.sub_skill_id == sub_skill_id,
            SubSkillManifest.build_no == build_no,
            SubSkillManifest.cycle == cycle,
            SubSkillManifest.attempt == attempt,
        )
        return self.session.scalars(query).one_or_none()

    def record(self, values: dict[str, Any]) -> SubSkillManifest:
        """Insert once per stage key; a retried activity gets the row it already wrote."""
        found = self.find(
            values["sub_skill_id"], values["build_no"], values["cycle"], values["attempt"]
        )
        if found is not None:
            return found
        return self.add(SubSkillManifest(**values))

    def history(self, sub_skill_id: uuid.UUID) -> list[SubSkillManifest]:
        """Every manifest of the Sub-Skill, newest stage key first."""
        query = (
            self._scoped()
            .where(SubSkillManifest.sub_skill_id == sub_skill_id)
            .order_by(
                SubSkillManifest.build_no.desc(),
                SubSkillManifest.cycle.desc(),
                SubSkillManifest.attempt.desc(),
            )
        )
        return list(self.session.scalars(query))

    def newest(self, sub_skill_ids: list[uuid.UUID]) -> dict[uuid.UUID, SubSkillManifest]:
        """Each Sub-Skill's newest manifest (highest build_no, cycle, attempt)."""
        query = self._scoped().where(SubSkillManifest.sub_skill_id.in_(sub_skill_ids))
        found: dict[uuid.UUID, SubSkillManifest] = {}
        for row in self.session.scalars(query):
            key = (row.build_no, row.cycle, row.attempt)
            have = found.get(row.sub_skill_id)
            if have is None or key > (have.build_no, have.cycle, have.attempt):
                found[row.sub_skill_id] = row
        return found


class WorkflowManifestRepository(TenantScopedRepository[WorkflowManifestRow]):
    model = WorkflowManifestRow

    def find(self, workflow_id: uuid.UUID, build_no: int) -> WorkflowManifestRow | None:
        query = self._scoped().where(
            WorkflowManifestRow.workflow_id == workflow_id,
            WorkflowManifestRow.build_no == build_no,
        )
        return self.session.scalars(query).one_or_none()

    def history(self, workflow_id: uuid.UUID) -> list[WorkflowManifestRow]:
        """Every build's manifest of the Workflow, newest first."""
        query = (
            self._scoped()
            .where(WorkflowManifestRow.workflow_id == workflow_id)
            .order_by(WorkflowManifestRow.build_no.desc())
        )
        return list(self.session.scalars(query))

    def record(self, values: dict[str, Any]) -> WorkflowManifestRow:
        """Insert once per (workflow_id, build_no); a retried activity gets the row it wrote."""
        found = self.find(values["workflow_id"], values["build_no"])
        if found is not None:
            return found
        return self.add(WorkflowManifestRow(**values))


class GateResultRepository(TenantScopedRepository[GateResultRow]):
    model = GateResultRow

    def for_stage(
        self, sub_skill_id: uuid.UUID, gate: str, build_no: int, cycle: int, attempt: int
    ) -> GateResultRow | None:
        """The result of `gate` for one build stage key, if the gate already ran (E9.3)."""
        query = self._scoped().where(
            GateResultRow.sub_skill_id == sub_skill_id,
            GateResultRow.gate == gate,
            GateResultRow.build_no == build_no,
            GateResultRow.cycle == cycle,
            GateResultRow.attempt == attempt,
        )
        return self.session.scalars(query).one_or_none()

    def stage_statuses(
        self, sub_skill_ids: list[uuid.UUID]
    ) -> list[tuple[uuid.UUID, int, int, int, str, str]]:
        """(sub_skill, build_no, cycle, attempt, gate, status) of every keyed result, without the
        findings: the Build & Test grid picks each Sub-Skill's newest stage from these."""
        query = (
            self._scoped()
            .where(
                GateResultRow.sub_skill_id.in_(sub_skill_ids), GateResultRow.build_no.is_not(None)
            )
            .with_only_columns(
                GateResultRow.sub_skill_id,
                GateResultRow.build_no,
                GateResultRow.cycle,
                GateResultRow.attempt,
                GateResultRow.gate,
                GateResultRow.status,
            )
        )
        return [
            (sub, build, cycle, attempt, gate, status)
            for sub, build, cycle, attempt, gate, status in self.session.execute(query)
            if sub is not None and cycle is not None and attempt is not None
        ]

    def of_stage(
        self, sub_skill_id: uuid.UUID, build_no: int, cycle: int, attempt: int
    ) -> list[GateResultRow]:
        """Every gate's result for one build stage key, by gate name."""
        query = (
            self._scoped()
            .where(
                GateResultRow.sub_skill_id == sub_skill_id,
                GateResultRow.build_no == build_no,
                GateResultRow.cycle == cycle,
                GateResultRow.attempt == attempt,
            )
            .order_by(GateResultRow.gate)
        )
        return list(self.session.scalars(query))

    def failed_for_pass(
        self, sub_skill_id: uuid.UUID, build_no: int, cycle: int, attempt: int
    ) -> list[GateResultRow]:
        """The gates that FAILED in one build pass, by gate name (the corrector's findings)."""
        query = (
            self._scoped()
            .where(
                GateResultRow.sub_skill_id == sub_skill_id,
                GateResultRow.build_no == build_no,
                GateResultRow.cycle == cycle,
                GateResultRow.attempt == attempt,
                GateResultRow.status == "FAILED",
            )
            .order_by(GateResultRow.gate)
        )
        return list(self.session.scalars(query))

    def record_stage(self, values: dict[str, Any]) -> GateResultRow:
        """Insert once per (sub_skill, gate, stage key); a retried activity gets the row it
        already wrote, with the same findings."""
        found = self.for_stage(
            values["sub_skill_id"],
            values["gate"],
            values["build_no"],
            values["cycle"],
            values["attempt"],
        )
        if found is not None:
            return found
        return self.add(GateResultRow(**values))
