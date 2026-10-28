"""Test helpers for E8.3: pass 1's world (`macros_db.Pass1`) with Macro Skills stored, and ISATVON
pass 2 wired to a fake Bifrost. Answers are built from the ISA14 MS01.1 sample Sub-Skill."""

import asyncio
import copy
import json
import uuid
from typing import Any

from fixtures import fixture_path
from knowledge_db import TENANT, World
from llm_db import FakeBifrost
from macros_db import FIXTURES, Pass1, sow_doc
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_contracts.pipeline import MacroRef
from sbam_workers.isatvon.macros import macro_category
from sbam_workers.isatvon.sub_skills import extract_for_macro
from sqlalchemy import select

MS01_SOW = FIXTURES / "ms01_1_sow.json"


def sample() -> dict[str, Any]:
    """The ISA14 MS01.1 Sub-Skill (VALID as it is)."""
    raw: dict[str, Any] = json.loads(
        fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8")
    )
    return raw


def proposal(name: str, *refs: str, **dotted: Any) -> dict[str, Any]:
    """One `SubSkillProposal`: the sample renamed, with dotted-path changes to its content."""
    skill = copy.deepcopy(sample())
    skill["skill_metadata"]["skill_name"] = name
    for path, value in dotted.items():
        *parents, leaf = path.split(".")
        node = skill
        for part in parents:
            node = node[part]
        node[leaf] = value
    return {"sow_refs": list(refs), "sub_skill": skill}


def answer(*proposals: dict[str, Any]) -> str:
    return json.dumps({"sub_skills": list(proposals)})


class Pass2(Pass1):
    """A Workflow with its Macro Skills stored (as pass 1 leaves them)."""

    def __init__(
        self,
        w: World,
        sow: dict[str, Any] | None = None,
        macros: list[tuple[str, list[str]]] | None = None,
    ) -> None:
        super().__init__(w, sow=sow if sow is not None else sow_doc(MS01_SOW))
        self.macro_ids = [
            self.add_macro(order, name, refs)
            for order, (name, refs) in enumerate(
                macros or [("Investor Registration Access", ["M-ACCESS"])], start=1
            )
        ]

    def add_macro(self, order: int, name: str, refs: list[str]) -> uuid.UUID:
        with self.w.sessions() as s:
            row = MacroSkill(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.w.project,
                workflow_id=self.workflow,
                sow_version_id=self.version,
                macro_category=macro_category(order, name),
                name=name,
                description=f"{name}.",
                order=order,
                sow_refs=refs,
                agent_ref=None,
                validation_errors=[],
                created_by="SYSTEM",
            )
            row.state = "PENDING"
            s.add(row)
            s.commit()
            return row.id

    def macro_ref(self, index: int = 0) -> MacroRef:
        return MacroRef(
            **self.inp.model_dump(), workflow_id=self.workflow, macro_skill_id=self.macro_ids[index]
        )

    def run_macro(
        self, bifrost: FakeBifrost, *, index: int = 0, attempt: int = 1
    ) -> list[uuid.UUID]:
        return asyncio.run(
            extract_for_macro(self.deps(bifrost), self.macro_ref(index), self.scope(attempt))
        )

    def sub_skills(self, index: int | None = None) -> list[SubSkill]:
        with self.w.sessions() as s:
            query = select(SubSkill).where(SubSkill.workflow_id == self.workflow)
            if index is not None:
                query = query.where(SubSkill.macro_skill_id == self.macro_ids[index])
            rows = list(s.scalars(query.order_by(SubSkill.macro_skill_id, SubSkill.order)))
            s.expunge_all()
            return rows
