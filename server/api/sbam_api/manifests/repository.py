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
