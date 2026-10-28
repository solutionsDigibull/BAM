"""Test helpers for E8.2: a READY knowledge version (the E5.3 test world), an approved SOW written
from it, the Workflow SBAM created for it, and ISATVON pass 1 wired to a fake Bifrost."""

import asyncio
import copy
import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any

from knowledge_db import TENANT, World
from llm_db import FakeBifrost, make_client, scope
from sbam_api.core.ids import uuid7
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.knowledge.models import KnowledgeVersion
from sbam_api.llm.models import LlmCall
from sbam_api.masters.models import MasterEntry, MasterEntryVersion, MasterSnapshot
from sbam_api.notifications.models import Notification
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.sow.service import canonical_sha256
from sbam_api.workflows.models import Workflow
from sbam_contracts.pipeline import WorkflowRef
from sbam_workers.isatvon.macros import EXTRACT_ATTEMPTS, extract_macros
from sbam_workers.llm.store import LlmScope
from sbam_workers.sow.generate import SowDeps
from sqlalchemy import select

FIXTURES = Path(__file__).parent / "fixtures" / "sow"
FOUR_MODULES = FIXTURES / "sow_four_modules.json"
DOCS = (
    "Northwind Funds onboards investors online: registration, identity checks and KYC.",
    "Investors see their portfolio holdings and receive quarterly statements by email.",
    "Support answers investor questions within one day and escalates complaints.",
)


def vector(text: str, size: int = 64) -> list[float]:
    """The mock provider's embedding: hashed words, so shared words mean close vectors."""
    out = [0.0] * size
    for word in re.findall(r"[a-z0-9]+", text.lower()) or [""]:
        digest = hashlib.sha256(word.encode()).digest()
        out[int.from_bytes(digest[:4], "big") % size] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]


async def fake_embed(texts: list[str]) -> list[list[float]]:
    return [vector(t) for t in texts]


def sow_doc(path: Path = FOUR_MODULES, **change: Any) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    doc.update(copy.deepcopy(change))
    return doc


def macro(name: str, *refs: str, agent: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} for the investors.",
        "sow_refs": list(refs),
        "agent_ref": agent,
        **extra,
    }


def answer(*macros: dict[str, Any]) -> str:
    return json.dumps({"macros": list(macros)})


class Pass1:
    """One project with an approved SOW and its Workflow (ISATVON_EXTRACTING)."""

    def __init__(
        self,
        w: World,
        sow: dict[str, Any] | None = None,
        agents: dict[str, str] | None = None,
        with_workflow: bool = True,
    ) -> None:
        """`with_workflow=False`: the pipeline's `create_workflow` makes the Workflow."""
        self.w = w
        engine = w.sessions.kw["bind"]
        for table in (LlmCall, Sow, SowVersion, Workflow, MacroSkill, SubSkill, Notification):
            table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        w.docs(*DOCS)
        self.inp = w.run_input()
        self.kv = w.build_all(self.inp)
        if agents:
            self.pin_agents(agents)
        self.sow = sow if sow is not None else sow_doc()
        self.version, self.sha = self.approved(self.sow)
        if with_workflow:
            self.workflow = self.add_workflow(self.version)
            self.ref = WorkflowRef(**self.inp.model_dump(), workflow_id=self.workflow)

    # -- the world ---------------------------------------------------------------------------
    def pin_agents(self, agents: dict[str, str]) -> None:
        """An Agents master entry per `AG-*` id, pinned in a snapshot the knowledge version uses."""
        entries: list[dict[str, object]] = []
        with self.w.sessions() as s:
            for stable_id, name in agents.items():
                entry = MasterEntry(
                    tenant_id=TENANT,
                    project_id=self.w.project,
                    master_type="AGENTS",
                    stable_id=stable_id,
                    row_label=stable_id,
                    status="APPROVED",
                    source_kind="ADMIN_ENTRY",
                )
                s.add(entry)
                s.flush()
                s.add(
                    MasterEntryVersion(
                        tenant_id=TENANT,
                        entry_id=entry.id,
                        version=1,
                        payload={"agent_name": name, "description": f"{stable_id}. {name}"},
                    )
                )
                entries.append(
                    {
                        "entry_id": str(entry.id),
                        "master_type": "AGENTS",
                        "stable_id": stable_id,
                        "version": 1,
                        "status": "APPROVED",
                        "restricted": False,
                    }
                )
            snapshot = MasterSnapshot(
                tenant_id=TENANT,
                project_id=self.w.project,
                entries=entries,
                sha256="sha256:" + "0" * 64,
            )
            s.add(snapshot)
            s.flush()
            kv = s.get(KnowledgeVersion, self.kv)
            assert kv is not None
            kv.master_snapshot_id = snapshot.id
            s.commit()

    def approved(self, structured: dict[str, Any]) -> tuple[uuid.UUID, str]:
        """The project's next SOW version, APPROVED and the SOW's approved one."""
        sha = canonical_sha256(structured)
        with self.w.sessions() as s:
            sow = s.scalars(select(Sow).where(Sow.project_id == self.w.project)).one_or_none()
            if sow is None:
                sow = Sow(tenant_id=TENANT, project_id=self.w.project)
                s.add(sow)
                s.flush()
            number = len(s.scalars(select(SowVersion).where(SowVersion.sow_id == sow.id)).all())
            version = SowVersion(
                tenant_id=TENANT,
                project_id=self.w.project,
                sow_id=sow.id,
                version=number + 1,
                source="GENERATED",
                run_id=uuid7(),
                knowledge_version_id=self.kv,
                structured=structured,
                sha256=sha,
            )
            version.state = "APPROVED"
            s.add(version)
            s.flush()
            if sow.approved_version_id is not None:
                previous = s.get(SowVersion, sow.approved_version_id)
                if previous is not None:
                    previous.state = "SUPERSEDED"
            sow.approved_version_id = sow.current_version_id = version.id
            s.commit()
            return version.id, sha

    def add_workflow(self, version: uuid.UUID, major: int = 1) -> uuid.UUID:
        with self.w.sessions() as s:
            row = Workflow(
                id=uuid7(),
                tenant_id=TENANT,
                project_id=self.w.project,
                sow_version_id=version,
                major=major,
                version=f"{major}.0.0",
                name=f"Northwind v{major}",
                created_by="SYSTEM",
            )
            row.state = "ISATVON_EXTRACTING"
            s.add(row)
            s.commit()
            return row.id

    # -- running pass 1 ----------------------------------------------------------------------
    def deps(self, bifrost: FakeBifrost, budget: int = 100_000) -> SowDeps:
        client, _ = make_client(bifrost, sessions=self.w.sessions)
        return SowDeps(
            self.w.sessions, self.w.storage, client, fake_embed, budget, attempts=EXTRACT_ATTEMPTS
        )

    def scope(self, attempt: int = 1) -> LlmScope:
        return scope(tenant_id=TENANT, project_id=self.w.project, attempt=attempt)

    def run(
        self, bifrost: FakeBifrost, *, attempt: int = 1, budget: int = 100_000
    ) -> list[uuid.UUID]:
        return asyncio.run(
            extract_macros(self.deps(bifrost, budget), self.ref, self.scope(attempt))
        )

    # -- reading back ------------------------------------------------------------------------
    def macros(self) -> list[MacroSkill]:
        with self.w.sessions() as s:
            rows = list(
                s.scalars(
                    select(MacroSkill)
                    .where(MacroSkill.workflow_id == self.workflow)
                    .order_by(MacroSkill.order)
                )
            )
            s.expunge_all()
            return rows

    def workflow_row(self) -> Workflow:
        with self.w.sessions() as s:
            row = s.get(Workflow, self.workflow)
            assert row is not None
            s.expunge(row)
            return row

    def module_ids(self) -> list[str]:
        return [m["id"] for m in self.sow["modules"]]
