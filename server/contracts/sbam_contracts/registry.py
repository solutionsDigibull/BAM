"""Every exported root contract, in a fixed order (issue E1.2).

`python -m sbam_contracts.gen` writes one JSON Schema per entry (`schemas/<path>.schema.json`) and
one TypeScript file with every entry and the models they use (`client/src/contracts/index.ts`).
Adding a contract is one line here, then `make contracts`.
"""

from typing import Final

from pydantic import BaseModel

from sbam_contracts.api import ProblemDetail
from sbam_contracts.audit import AuditEntry
from sbam_contracts.documents import (
    CleanupResult,
    DocumentOut,
    PurgeRequest,
    PurgeResult,
    PurgeTarget,
    ScanOutcome,
    UploadRequest,
    UploadToken,
)
from sbam_contracts.identity import (
    Me,
    Tenant,
    TenantCreate,
    TenantPatch,
    TenantUser,
    UserInvite,
    UserPatch,
)
from sbam_contracts.isatvon14 import Agent, RuleViolation, SubSkill
from sbam_contracts.knowledge import (
    KnowledgeGraphPage,
    KnowledgeNodeDetail,
    KnowledgeVersionList,
)
from sbam_contracts.manifest import Isa14Manifest, LoopSpec, SkillManifest, WorkflowManifest
from sbam_contracts.masters import (
    AgentsPayload,
    FiletypePayload,
    OrganizationPayload,
    RagReference,
    ReadmeTab,
    RulesPayload,
    SkillsPayload,
    ToolingPayload,
)
from sbam_contracts.masters_api import (
    FiletypeInfo,
    ImportReport,
    InclusionReport,
    MasterEntry,
    MasterEntryCreate,
    MastersSnapshot,
    MasterVersionCreate,
)
from sbam_contracts.notifications import Notification
from sbam_contracts.okf import (
    BundleCoverage,
    BundleManifest,
    OkfChunk,
    OkfEntity,
    OkfRelation,
)
from sbam_contracts.projects import (
    PipelineTimeline,
    Project,
    ProjectCreate,
    ProjectMembers,
    ProjectMembersPut,
    ProjectPatch,
    WorkflowSummary,
)
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
    ("masters/tooling", ToolingPayload),
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
    ("identity/user_invite", UserInvite),
    ("identity/user_patch", UserPatch),
    ("identity/tenant_user", TenantUser),
    ("projects/project", Project),
    ("projects/project_create", ProjectCreate),
    ("projects/project_patch", ProjectPatch),
    ("projects/project_members_put", ProjectMembersPut),
    ("projects/project_members", ProjectMembers),
    ("projects/pipeline_timeline", PipelineTimeline),
    ("projects/workflow_summary", WorkflowSummary),
    ("documents/upload_request", UploadRequest),
    ("documents/upload_token", UploadToken),
    ("documents/scan_outcome", ScanOutcome),
    ("masters/master_entry_create", MasterEntryCreate),
    ("masters/master_version_create", MasterVersionCreate),
    ("masters/master_entry", MasterEntry),
    ("masters/filetype_info", FiletypeInfo),
    ("masters/masters_snapshot", MastersSnapshot),
    ("masters/inclusion_report", InclusionReport),
    ("masters/import_report", ImportReport),
    ("documents/document", DocumentOut),
    ("documents/purge_target", PurgeTarget),
    ("documents/purge_request", PurgeRequest),
    ("documents/purge_result", PurgeResult),
    ("documents/cleanup_result", CleanupResult),
    ("notifications/notification", Notification),
    ("audit/audit_entry", AuditEntry),
    ("okf/bundle", BundleManifest),
    ("okf/entity", OkfEntity),
    ("okf/relation", OkfRelation),
    ("okf/chunk", OkfChunk),
    ("okf/coverage", BundleCoverage),
    ("knowledge/version_list", KnowledgeVersionList),
    ("knowledge/graph_page", KnowledgeGraphPage),
    ("knowledge/node_detail", KnowledgeNodeDetail),
)
