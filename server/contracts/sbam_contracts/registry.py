"""Every exported root contract, in a fixed order (issue E1.2).

`python -m sbam_contracts.gen` writes one JSON Schema per entry (`schemas/<path>.schema.json`) and
one TypeScript file with every entry and the models they use (`client/src/contracts/index.ts`).
Adding a contract is one line here, then `make contracts`.
"""

from typing import Final

from pydantic import BaseModel

from sbam_contracts.api import ProblemDetail
from sbam_contracts.identity import Me, Tenant, TenantCreate, TenantPatch
from sbam_contracts.isatvon14 import Agent, RuleViolation, SubSkill
from sbam_contracts.manifest import Isa14Manifest, LoopSpec, SkillManifest, WorkflowManifest
from sbam_contracts.masters import (
    AgentsPayload,
    FiletypePayload,
    OrganizationPayload,
    RagReference,
    ReadmeTab,
    RulesPayload,
    SkillsPayload,
)
from sbam_contracts.projects import Project, ProjectCreate, ProjectPatch
from sbam_contracts.runs import Run, RunAccepted
from sbam_contracts.runtime_registry import RuntimeRegistry
from sbam_contracts.sow import SowDocument
from sbam_contracts.state_machines import StateMachineStates

ROOTS: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("problem_detail", ProblemDetail),
    ("isatvon/agent", Agent),
    ("isatvon/sub_skill", SubSkill),
    ("isatvon/rule_violation", RuleViolation),
    ("manifest/isa14_manifest", Isa14Manifest),
    ("manifest/skill_manifest", SkillManifest),
    ("manifest/workflow_manifest", WorkflowManifest),
    ("manifest/loop_spec", LoopSpec),
    ("masters/readme", ReadmeTab),
    ("masters/organization", OrganizationPayload),
    ("masters/rules", RulesPayload),
    ("masters/agents", AgentsPayload),
    ("masters/skills", SkillsPayload),
    ("masters/filetype", FiletypePayload),
    ("masters/rag_reference", RagReference),
    ("sow/sow_document", SowDocument),
    ("runs/run", Run),
    ("runs/run_accepted", RunAccepted),
    ("runtime_registry/runtime_registry", RuntimeRegistry),
    ("state_machines/states", StateMachineStates),
    ("identity/tenant", Tenant),
    ("identity/tenant_create", TenantCreate),
    ("identity/tenant_patch", TenantPatch),
    ("identity/me", Me),
    ("projects/project", Project),
    ("projects/project_create", ProjectCreate),
    ("projects/project_patch", ProjectPatch),
)
