// GENERATED — DO NOT EDIT. Source: server/contracts (Pydantic).
// Regenerate with `make contracts`; CI fails if this file differs from a fresh generation.
// source-sha256:e2a0b028c66b024b2fdd62eed809411c3b328735e77843a0f7719fb83a814a45

/**
 * Stable machine-readable error codes. Add new codes here; never rename or reuse one.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ErrorCode".
 */
export type ErrorCode =
  | "VALIDATION_ERROR"
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "WRONG_PORTAL"
  | "NOT_FOUND"
  | "CONFLICT"
  | "PRECONDITION_FAILED"
  | "PRECONDITION_REQUIRED"
  | "PAYLOAD_TOO_LARGE"
  | "UNSUPPORTED_MEDIA_TYPE"
  | "RESOURCE_LOCKED"
  | "RATE_LIMITED"
  | "SERVICE_UNAVAILABLE"
  | "INTERNAL_ERROR"
  | "IDEMPOTENCY_KEY_REUSED"
  | "INVALID_CURSOR"
  | "VERSION_CONFLICT"
  | "INVALID_TRANSITION"
  | "READ_ONLY"
  | "GONE"
  | "TENANT_MISMATCH"
  | "TENANT_SUSPENDED"
  | "IDP_UNAVAILABLE"
  | "LAST_ADMIN"
  | "USER_EXISTS"
  | "DUPLICATE_NAME"
  | "PROJECT_ARCHIVED"
  | "DRAFT_EXISTS"
  | "STRUCTURE_MISMATCH"
  | "FILETYPE_FROZEN"
  | "SNAPSHOT_PROJECT_MISMATCH"
  | "SECRET_VALUE_NOT_ALLOWED"
  | "FILE_TOO_LARGE"
  | "FILE_LIMIT_REACHED"
  | "UNSUPPORTED_TYPE"
  | "INVALID_ENCODING"
  | "NESTED_ARCHIVE"
  | "ARCHIVE_LIMIT"
  | "HASH_MISMATCH"
  | "NO_DOCUMENTS"
  | "PARSE_ERROR"
  | "SCAN_UNAVAILABLE"
  | "OCR_UNAVAILABLE"
  | "MALWARE"
  | "KNOWLEDGE_BUILDING"
  | "KNOWLEDGE_NOT_READY"
  | "COGNEE_ACL_DISABLED"
  | "SOW_STALE"
  | "SOW_LOCKED"
  | "SOW_APPROVED"
  | "SOW_NOT_APPROVED"
  | "SOW_NOT_LATEST"
  | "SOW_VERSION_CONFLICT"
  | "NO_APPROVED_SOW"
  | "NOT_IN_REVIEW"
  | "PIPELINE_RUNNING"
  | "PIPELINE_NOT_WAITING"
  | "TEMPORAL_UNAVAILABLE"
  | "ORCHESTRATOR_UNAVAILABLE"
  | "ISATVON_CONTRACT"
  | "SKILL_ID_PREFIX_MISMATCH"
  | "MACRO_REQUIRES_SUB_SKILL"
  | "GATE_PARSE_ERROR"
  | "UNRESOLVED_IDENTIFIER"
  | "TYPE_MISMATCH"
  | "SCHEMA_PARSE_ERROR"
  | "LIBRARY_NOT_IN_REGISTRY"
  | "TOOLS_NOT_DECOUPLED"
  | "WORKFLOW_BUILDING"
  | "MIN_TEST_CASES"
  | "MOCK_UNMATCHED"
  | "WORKFLOW_FROZEN"
  | "NOT_FROZEN";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "DiffKind".
 */
export type DiffKind = "renamed" | "added" | "removed" | "reordered";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ExternalKind".
 */
export type ExternalKind = "FILE" | "API" | "URL";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue = string | number | boolean | null | JsonValue[] | {[key: string]: JsonValue};
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ManifestStatus".
 */
export type ManifestStatus = "DRAFT" | "VALIDATING" | "APPROVED" | "SUPERSEDED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "CertificationState".
 */
export type CertificationState = "CERTIFIED" | "FROZEN";
/**
 * What a run works on; `subject_id` is that entity's id.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SubjectType".
 */
export type SubjectType = "project" | "document_version" | "sub_skill";
/**
 * Execution state as mirrored in `temporal_runs` (Temporal's workflow execution status).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RunState".
 */
export type RunState = "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | "TERMINATED" | "TIMED_OUT";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Actor".
 */
export type Actor = "FDE" | "ADMIN" | "SYSTEM";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MachineName".
 */
export type MachineName =
  "project_pipeline" | "workflow" | "document_version" | "sow_version" | "sub_skill" | "agent" | "knowledge_version";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProjectPipelineState".
 */
export type ProjectPipelineState =
  | "IDLE"
  | "INGESTING"
  | "KNOWLEDGE_BUILDING"
  | "SOW_GENERATING"
  | "SOW_REVIEW"
  | "SOW_APPROVED"
  | "WORKFLOW_CREATED"
  | "INGESTION_FAILED"
  | "KNOWLEDGE_FAILED"
  | "SOW_GENERATION_FAILED"
  | "CANCELLED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowState".
 */
export type WorkflowState =
  | "CREATED"
  | "ISATVON_EXTRACTING"
  | "ISATVON_REVIEW"
  | "BUILDING"
  | "CERTIFYING"
  | "CERTIFIED"
  | "FROZEN"
  | "EXTRACTION_FAILED"
  | "NEEDS_FDE_REVIEW"
  | "WORKFLOW_VALIDATION_FAILED"
  | "CANCELLED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "DocumentVersionState".
 */
export type DocumentVersionState =
  "UPLOADED" | "SCANNING" | "REJECTED" | "SCAN_FAILED" | "PARSING" | "PARSED" | "PARSE_FAILED" | "DELETED" | "PURGED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SowVersionState".
 */
export type SowVersionState = "GENERATED" | "IN_REVIEW" | "APPROVED" | "SUPERSEDED" | "GENERATION_FAILED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SubSkillState".
 */
export type SubSkillState =
  | "DRAFT"
  | "VALID"
  | "INVALID"
  | "BUILDING"
  | "CORRECTING"
  | "TESTING"
  | "TESTING_BLOCKED"
  | "NEEDS_FDE_REVIEW"
  | "DEPENDENCY_FAILED"
  | "BUILD_ERROR"
  | "CERTIFIED"
  | "FROZEN"
  | "INVALIDATED"
  | "SUPERSEDED";
/**
 * Agent = TD Macro Skill (§14.5).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "AgentState".
 */
export type AgentState = "PENDING" | "BUILDING" | "CERTIFIED" | "FROZEN";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeVersionState".
 */
export type KnowledgeVersionState = "BUILDING" | "READY" | "FAILED" | "OUTDATED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "TenantStatus".
 */
export type TenantStatus = "ACTIVE" | "SUSPENDED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UserStatus".
 */
export type UserStatus = "INVITED" | "ACTIVE" | "DEACTIVATED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProjectStatus".
 */
export type ProjectStatus = "ACTIVE" | "ARCHIVED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PipelineStepStatus".
 */
export type PipelineStepStatus = "pending" | "running" | "done" | "failed" | "cancelled";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ScanStatus".
 */
export type ScanStatus = "CLEAN" | "INFECTED" | "SCAN_FAILED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MasterType".
 */
export type MasterType = "ORGANIZATION" | "RULES" | "AGENTS" | "SKILLS" | "TOOLING" | "FILETYPE";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MasterStatus".
 */
export type MasterStatus = "PROPOSED" | "ILLUSTRATIVE" | "APPROVED" | "RETIRED";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SourceKind".
 */
export type SourceKind = "BAM_0_2_SAMPLE" | "TEMPLATE_ROW" | "ADMIN_ENTRY";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ImportMode".
 */
export type ImportMode = "validate-only" | "upsert";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "NotificationType".
 */
export type NotificationType =
  | "SOW_READY"
  | "BREAKDOWN_READY"
  | "SUBSKILL_NEEDS_REVIEW"
  | "WORKFLOW_CERTIFIED"
  | "WORKFLOW_FROZEN"
  | "EXPORT_READY"
  | "DOCUMENT_REJECTED"
  | "GENERATION_FAILED"
  | "STAGE_COMPLETED"
  | "REMINDER";
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "CoverageStatus".
 */
export type CoverageStatus = "COVERED" | "CHUNKS_ONLY" | "FAILED";
/**
 * A document's fate in one knowledge build (E5.2); the OKF bundle (E5.3) refines ADDED.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SourceStatus".
 */
export type SourceStatus = "ADDED" | "FAILED";

/**
 * Every SBAM contract root model (sbam_contracts/registry.py).
 */
export interface SbamContracts {
  ProblemDetail?: ProblemDetail;
  Agent?: Agent;
  SubSkill?: SubSkill;
  RuleViolation?: RuleViolation;
  Isa14Manifest?: Isa14Manifest;
  SkillManifest?: SkillManifest;
  WorkflowManifest?: WorkflowManifest;
  LoopSpec?: LoopSpec;
  ReadmeTab?: ReadmeTab;
  OrganizationPayload?: OrganizationPayload;
  RulesPayload?: RulesPayload;
  AgentsPayload?: AgentsPayload;
  SkillsPayload?: SkillsPayload;
  ToolingPayload?: ToolingPayload;
  FiletypePayload?: FiletypePayload;
  RagReference?: RagReference;
  SowDocument?: SowDocument;
  Run?: Run;
  RunAccepted?: RunAccepted;
  RuntimeRegistry?: RuntimeRegistry;
  StateMachineStates?: StateMachineStates;
  Tenant?: Tenant;
  TenantCreate?: TenantCreate;
  TenantPatch?: TenantPatch;
  Me?: Me;
  UserInvite?: UserInvite;
  UserPatch?: UserPatch;
  TenantUser?: TenantUser;
  Project?: Project;
  ProjectCreate?: ProjectCreate;
  ProjectPatch?: ProjectPatch;
  ProjectMembersPut?: ProjectMembersPut;
  ProjectMembers?: ProjectMembers;
  PipelineTimeline?: PipelineTimeline;
  WorkflowSummary?: WorkflowSummary;
  UploadRequest?: UploadRequest;
  UploadToken?: UploadToken;
  ScanOutcome?: ScanOutcome;
  MasterEntryCreate?: MasterEntryCreate;
  MasterVersionCreate?: MasterVersionCreate;
  MasterEntry?: MasterEntry;
  FiletypeInfo?: FiletypeInfo;
  MastersSnapshot?: MastersSnapshot;
  InclusionReport?: InclusionReport;
  ImportReport?: ImportReport;
  DocumentOut?: DocumentOut;
  PurgeTarget?: PurgeTarget;
  PurgeRequest?: PurgeRequest;
  PurgeResult?: PurgeResult;
  CleanupResult?: CleanupResult;
  Notification?: Notification;
  AuditEntry?: AuditEntry;
  BundleManifest?: BundleManifest;
  OkfEntity?: OkfEntity;
  OkfRelation?: OkfRelation;
  OkfChunk?: OkfChunk;
  BundleCoverage?: BundleCoverage;
  KnowledgeVersionList?: KnowledgeVersionList;
  KnowledgeGraphPage?: KnowledgeGraphPage;
  KnowledgeNodeDetail?: KnowledgeNodeDetail;
}
/**
 * `application/problem+json` body returned for every 4xx/5xx response.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProblemDetail".
 */
export interface ProblemDetail {
  type?: string;
  title: string;
  status: number;
  detail?: string | null;
  code: ErrorCode;
  errors?: FieldError[];
  correlation_id: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FieldError".
 */
export interface FieldError {
  /**
   * Dotted path of the offending field, e.g. 'limit'
   */
  path: string;
  message: string;
  diff?: StructureDiff | null;
}
/**
 * How an uploaded layout differs from the frozen one (workbook import, E3.3).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "StructureDiff".
 */
export interface StructureDiff {
  tab: string;
  expected: string | null;
  actual: string | null;
  kind: DiffKind;
}
/**
 * An Agent (TD: Macro Skill): a major module of the solution, made of ordered Sub-Skills.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Agent".
 */
export interface Agent {
  macro_category: string;
  name: string;
  description: string;
  order: number;
  sow_refs: string[];
  agent_ref?: string | null;
  /**
   * @minItems 1
   */
  sub_skills: [SubSkill, ...SubSkill[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SubSkill".
 */
export interface SubSkill {
  skill_metadata: SkillMetadata;
  isatvon_definition: IsatvonDefinition;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SkillMetadata".
 */
export interface SkillMetadata {
  macro_category: string;
  skill_id: string;
  skill_name: string;
  skill_ref?: string | null;
  rule_refs?: string[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "IsatvonDefinition".
 */
export interface IsatvonDefinition {
  i_instructions: IInstructions;
  s_source: SSource;
  a_automation: AAutomation;
  t_tech_stack: TTechStack;
  v_variables: VVariables;
  o_outcome: OOutcome;
  n_notification: NNotification;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "IInstructions".
 */
export interface IInstructions {
  statement: string;
  trigger: string;
  action: string;
  check: string;
  bound: string;
  user_role: UserRole;
  task: Task;
  context: Context;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UserRole".
 */
export interface UserRole {
  level: string;
  persona: string;
  authority: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Task".
 */
export interface Task {
  goal: string;
  method: string;
  /**
   * @minItems 1
   */
  steps: [string, ...string[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Context".
 */
export interface Context {
  background: string;
  topic: string;
  history?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SSource".
 */
export interface SSource {
  /**
   * @minItems 1
   */
  required_inputs: [string, ...string[]];
  fresh_state_parameters: string[];
  external_data?: ExternalData[] | null;
  ml_data?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ExternalData".
 */
export interface ExternalData {
  name: string;
  kind: ExternalKind;
  ref: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "AAutomation".
 */
export interface AAutomation {
  orchestration_layer: string;
  trigger_mode: string;
  action_step: string;
  acceptance_check: string;
  srim_metadata: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "TTechStack".
 */
export interface TTechStack {
  execution_layer: string;
  action_tool: string;
  fixed_check_tool: string;
  secondary_tools?: string[] | null;
  gateway_routing?: string | null;
  /**
   * @minItems 1
   */
  libraries: [Library, ...Library[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Library".
 */
export interface Library {
  package: string;
  version_constraint?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "VVariables".
 */
export interface VVariables {
  max_iterations: number;
  success_gate: string;
  no_op_condition: string;
  escalation_condition: string;
  budget_limit: string;
  tables: string[];
  parameters?: Parameter[] | null;
  secrets?: string[] | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Parameter".
 */
export interface Parameter {
  name: string;
  type: string;
  value?: JsonValue;
  default?: JsonValue;
  constraints?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "OOutcome".
 */
export interface OOutcome {
  json_schema: {
    [k: string]: JsonValue | undefined;
  };
  state_handoff_file: StateHandoffFile;
  success_criteria: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "StateHandoffFile".
 */
export interface StateHandoffFile {
  table: string;
  mode: string;
  required_fields: string[];
  description: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "NNotification".
 */
export interface NNotification {
  ui_updates: string[];
  database_updates: string[];
  alerts: string[];
  next: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RuleViolation".
 */
export interface RuleViolation {
  rule: number;
  path: string;
  message: string;
}
/**
 * The ISA14 Skill Manifest: exactly these five blocks.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Isa14Manifest".
 */
export interface Isa14Manifest {
  skill_metadata: ManifestSkillMetadata;
  isatvon_definition: Isa14Definition;
  srim_runtime_definition: SrimRuntimeDefinition;
  contracts: ManifestContracts;
  governance: Governance;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ManifestSkillMetadata".
 */
export interface ManifestSkillMetadata {
  macro_category: string;
  skill_id: string;
  skill_name: string;
  skill_type: string;
  reusable: boolean;
  version: string;
  status: string;
  owner: string;
  created_at: string;
  updated_at: string;
}
/**
 * `isatvon_definition` as exported: ISA14's flat shape (`i_instructions` is one string).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Isa14Definition".
 */
export interface Isa14Definition {
  i_instructions: string;
  s_source: FlatSource;
  a_automation: FlatAutomation;
  t_tech_stack: FlatTechStack;
  v_variables: FlatVariables;
  o_outcome: FlatOutcome;
  n_notification: FlatNotification;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FlatSource".
 */
export interface FlatSource {
  /**
   * @minItems 1
   */
  required_inputs: [string, ...string[]];
  fresh_state_parameters: string[];
  external_data?: ExternalData[] | null;
  ml_data?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FlatAutomation".
 */
export interface FlatAutomation {
  orchestration_layer: string;
  action_step: string;
  acceptance_check: string;
  trigger_mode?: string | null;
  srim_metadata?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FlatTechStack".
 */
export interface FlatTechStack {
  execution_layer: string;
  action_tool: string;
  fixed_check_tool: string;
  secondary_tools?: string[] | null;
  gateway_routing?: string | null;
  libraries?: Library[] | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FlatVariables".
 */
export interface FlatVariables {
  max_iterations: number;
  success_gate: string;
  no_op_condition: string;
  escalation_condition: string;
  budget_limit: string;
  tables?: string[] | null;
  parameters?: Parameter[] | null;
  secrets?: string[] | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FlatOutcome".
 */
export interface FlatOutcome {
  json_schema: {
    [k: string]: JsonValue | undefined;
  };
  state_handoff_file: StateHandoffFile | string;
  success_criteria?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FlatNotification".
 */
export interface FlatNotification {
  ui_updates: string[];
  database_updates: string[];
  alerts: string[];
  next?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SrimRuntimeDefinition".
 */
export interface SrimRuntimeDefinition {
  orchestration: Orchestration;
  execution: Execution;
  gateway: Gateway;
  persistence: Persistence;
  audit: Audit;
  ui: RuntimeUi;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Orchestration".
 */
export interface Orchestration {
  router: string;
  trigger: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Execution".
 */
export interface Execution {
  engine: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Gateway".
 */
export interface Gateway {
  route_via: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Persistence".
 */
export interface Persistence {
  database: string;
  table: string;
  action: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Audit".
 */
export interface Audit {
  telemetry: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RuntimeUi".
 */
export interface RuntimeUi {
  target: string;
  method: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ManifestContracts".
 */
export interface ManifestContracts {
  input_schema: JsonSchemaObject;
  output_schema: JsonSchemaObject;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "JsonSchemaObject".
 */
export interface JsonSchemaObject {
  type: "object";
  properties: {
    [k: string]:
      | {
          [k: string]: JsonValue | undefined;
        }
      | undefined;
  };
  required?: string[] | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Governance".
 */
export interface Governance {
  deterministic_hash: string;
  lifecycle: Lifecycle;
  versioning: Versioning;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Lifecycle".
 */
export interface Lifecycle {
  state: string;
  approval_required_for_change: boolean;
  immutable_after_approval: boolean;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Versioning".
 */
export interface Versioning {
  major: number;
  minor: number;
  patch: number;
}
/**
 * The Sub-Skill manifest SBAM writes: ISA14 blocks + `sbam_build_record` (TD §11.1).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SkillManifest".
 */
export interface SkillManifest {
  skill_metadata: SbamSkillMetadata;
  isatvon_definition: Isa14Definition;
  srim_runtime_definition: SrimRuntimeDefinition;
  contracts: ManifestContracts;
  governance: Governance;
  sbam_build_record: SbamBuildRecord;
}
/**
 * `skill_metadata` as SBAM writes it; ids, version and enums are visible in the JSON Schema.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SbamSkillMetadata".
 */
export interface SbamSkillMetadata {
  /**
   * Pattern: `^MS(\d{2})_[A-Za-z0-9_]+$`
   */
  macro_category: string;
  /**
   * Pattern: `^MS(\d{2})\.(\d+)_[A-Za-z0-9_]+$`
   */
  skill_id: string;
  skill_name: string;
  skill_type: "Deterministic_Loopy_MCP";
  reusable: boolean;
  /**
   * Pattern: `^\d+\.0\.\d+$`
   */
  version: string;
  status: ManifestStatus;
  owner: string;
  created_at: string;
  updated_at: string;
}
/**
 * Platform-only provenance and build evidence. Never accepted from the FDE or an LLM.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SbamBuildRecord".
 */
export interface SbamBuildRecord {
  isatvon_framework_version: "1.4";
  isatvon_detail: IsatvonDefinition;
  ids: BuildIds;
  provenance: Provenance;
  build: Build;
  evidence?: Evidence | null;
  certification?: Certification | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "BuildIds".
 */
export interface BuildIds {
  tenant_id: string;
  project_id: string;
  workflow_id: string;
  sub_skill_id: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Provenance".
 */
export interface Provenance {
  sow_version_id: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  sow_sha256: string;
  approved_at: string;
  approved_by: string;
  knowledge_version_id: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  okf_sha256: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  master_snapshot_sha256: string;
  /**
   * @minItems 1
   */
  sow_refs: [string, ...string[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Build".
 */
export interface Build {
  template: TemplateRef;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  runtime_image_digest: string;
  libraries: Library[];
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  schema_sql_sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "TemplateRef".
 */
export interface TemplateRef {
  name: string;
  version: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Evidence".
 */
export interface Evidence {
  code_attempt_id: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  bundle_sha256: string;
  gates: JsonValue[];
  test_run_id: string;
  sandbox_run_id: string;
  passed_cases: number;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Certification".
 */
export interface Certification {
  state: CertificationState;
  certified_at: string;
  reused_from?: string | null;
}
/**
 * The final SBAM output for one Workflow (TD §11.3).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowManifest".
 */
export interface WorkflowManifest {
  schema_version: "sbam-workflow-manifest/1.0";
  ids: WorkflowIds;
  name: string;
  objective: string;
  version: string;
  previous_version?: string | null;
  provenance: Provenance;
  isatvon_framework_version: "1.4";
  /**
   * @minItems 1
   */
  macro_skills: [WorkflowAgent, ...WorkflowAgent[]];
  schema_sql: SchemaSql;
  dag: Dag;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  runtime_image_digest: string;
  evidence_index: EvidenceEntry[];
  certification: WorkflowCertification;
  integrity: Integrity;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowIds".
 */
export interface WorkflowIds {
  tenant_id: string;
  project_id: string;
  workflow_id: string;
}
/**
 * One Agent (TD: Macro Skill) of the Workflow and its certified Sub-Skills.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowAgent".
 */
export interface WorkflowAgent {
  macro_category: string;
  name: string;
  agent_ref?: string | null;
  certification_id: string;
  /**
   * @minItems 1
   */
  skills: [WorkflowSkill, ...WorkflowSkill[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowSkill".
 */
export interface WorkflowSkill {
  skill_id: string;
  /**
   * Pattern: `^sha256:[0-9a-f]{64}$`
   */
  deterministic_hash: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  manifest_sha256: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  bundle_sha256: string;
  certification_id: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SchemaSql".
 */
export interface SchemaSql {
  artifact_key: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Dag".
 */
export interface Dag {
  nodes: {
    [k: string]: JsonValue | undefined;
  }[];
  edges: {
    [k: string]: JsonValue | undefined;
  }[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "EvidenceEntry".
 */
export interface EvidenceEntry {
  level: string;
  subject: string;
  certification_id: string;
  /**
   * Pattern: `^(sha256:)?[0-9a-f]{64}$`
   */
  sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowCertification".
 */
export interface WorkflowCertification {
  state: CertificationState;
  certified_at: string;
  frozen_at?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Integrity".
 */
export interface Integrity {
  /**
   * Pattern: `^sha256:[0-9a-f]{64}$`
   */
  content_sha256: string;
}
/**
 * `loop_spec.json`: the five loop bounds in runtime form (the full compiler is #102).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "LoopSpec".
 */
export interface LoopSpec {
  max_iterations: number;
  success_gate: string;
  no_op_condition: string;
  escalation_condition: string;
  budget_limit_ms: number;
  ui_update_template: string;
}
/**
 * The README tab: help text. Only its structure is checked, its content is not interpreted.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ReadmeTab".
 */
export interface ReadmeTab {
  /**
   * Pattern: `^BuLLM - Admin Masters \(BAM\)`
   */
  title: string;
  rows:
    | []
    | [string | null]
    | [string | null, string | null]
    | [string | null, string | null, string | null]
    | [string | null, string | null, string | null, string | null][];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "OrganizationPayload".
 */
export interface OrganizationPayload {
  org_name?: string;
  type?: string;
  code?: string;
  location?: string;
  org_details?: string;
  employees?: string;
  customers?: string;
  offerings?: string;
  competencies?: string;
  data_sources?: string;
  systems_tools?: string;
  assets?: string;
  certifications?: string;
  misc_audit?: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RulesPayload".
 */
export interface RulesPayload {
  domain_rule?: string;
  pcb_panelization?: string;
  components_bom?: string;
  materials_finish?: string;
  box_build?: string;
  machine_hr_rates?: string;
  outsourced_services?: string;
  assembly_routing?: string;
  rules_dfm_sourcing?: string;
  misc?: string;
  rag_reference?: RagReference | null;
}
/**
 * RAG Reference cell (Rules and Skills): a JSON object with exactly these 7 keys.
 *
 * Values may be empty strings. `Links / Path` is a pointer and is never fetched.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RagReference".
 */
export interface RagReference {
  Example: string;
  "Links / Path": string;
  "Entity / Node Type": string;
  "Security / Access Level": string;
  "Refresh Frequency": string;
  "ID Field": string;
  "Parent Entity": string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "AgentsPayload".
 */
export interface AgentsPayload {
  agent_name?: string;
  description?: string;
  model?: string;
  repo?: string;
  api?: string;
  mcp_server?: string;
  script?: string;
  prompt?: string;
  eval?: string;
  logs?: string;
  misc?: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SkillsPayload".
 */
export interface SkillsPayload {
  skill?: string;
  documents?: string;
  rfq_inquiries?: string;
  currency?: string;
  supplier_master?: string;
  commodity_pricing?: string;
  shipping_logistics?: string;
  templates?: string;
  hitl_governance?: string;
  misc?: string;
  rag_reference?: RagReference | null;
}
/**
 * Tooling master: add-on tool categories and their bindings (Manufacturing Masters v0.2).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ToolingPayload".
 */
export interface ToolingPayload {
  category?: string;
  examples?: string;
  links_path?: string;
  owner_access?: string;
  record_status?: string;
  source_version_checked_at?: string;
  search_tag?: string;
  tenant_account_scope?: string;
  entity_node_type?: string;
  security_access_level?: string;
  refresh_frequency?: string;
  parent_entity?: string;
  source_evidence_ref?: string;
  binding_review_state?: string;
  search_aliases_category?: string;
}
/**
 * One of the 43 frozen filetypes. Changed only by super-admin through a new workbook (O-23).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FiletypePayload".
 */
export interface FiletypePayload {
  file_format: string;
  tier: number;
  purpose: string;
  rag_ingestion: string;
  /**
   * Pattern: `^https?://\S+$`
   */
  example: string;
}
/**
 * The nine SOW sections of TD §10.6, in order.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SowDocument".
 */
export interface SowDocument {
  overview: Overview;
  users_authority: UsersAuthority;
  scope: Scope;
  /**
   * @minItems 1
   */
  modules: [Module, ...Module[]];
  /**
   * @minItems 1
   */
  features: [FeatureItem, ...FeatureItem[]];
  data_integrations: DataIntegrations;
  rules_constraints: RulesConstraints;
  deliverables_acceptance: DeliverablesAcceptance;
  assumptions_open_points: AssumptionsOpenPoints;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Overview".
 */
export interface Overview {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  client: string;
  objective: string;
  background: string;
  topic: string;
  history?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Citation".
 */
export interface Citation {
  okf_ref_id: string;
  document_version_id: string;
  location: SbamContracts_Sow__Location;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "sbam_contracts__sow__Location".
 */
export interface SbamContracts_Sow__Location {
  page?: number | null;
  line?: number | null;
  sheet?: string | null;
  cell?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UsersAuthority".
 */
export interface UsersAuthority {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  /**
   * @minItems 1
   */
  roles: [Role, ...Role[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Role".
 */
export interface Role {
  role: string;
  decision_rights: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Scope".
 */
export interface Scope {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  /**
   * @minItems 1
   */
  in_scope: [string, ...string[]];
  out_of_scope: string[];
}
/**
 * A future Agent (TD: Macro Skill).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Module".
 */
export interface Module {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  /**
   * Pattern: `^M-[A-Za-z0-9][A-Za-z0-9.-]*$`
   */
  id: string;
  name: string;
  purpose: string;
  agent_role: string;
}
/**
 * A future Sub-Skill. Fields map onto ISATVON 1.4 I/S/A/T/V/O/N.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FeatureItem".
 */
export interface FeatureItem {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  /**
   * Pattern: `^F-[A-Za-z0-9][A-Za-z0-9.-]*$`
   */
  id: string;
  /**
   * Pattern: `^M-[A-Za-z0-9][A-Za-z0-9.-]*$`
   */
  module_id: string;
  name: string;
  trigger: string;
  action: string;
  check: string;
  bound: Bound;
  /**
   * @minItems 1
   */
  inputs_state: [string, ...string[]];
  external_data: string[];
  tools: FeatureTools;
  tables: string[];
  /**
   * @minItems 1
   */
  success_criteria: [string, ...string[]];
  output: string;
  handoff: string;
  notifications: string[];
}
/**
 * Retry policy; becomes the Sub-Skill's max_iterations and escalation.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Bound".
 */
export interface Bound {
  max_attempts: number;
  on_exhaustion: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FeatureTools".
 */
export interface FeatureTools {
  /**
   * @minItems 1
   */
  action: [string, ...string[]];
  /**
   * @minItems 1
   */
  check: [string, ...string[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "DataIntegrations".
 */
export interface DataIntegrations {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  sources: string[];
  apis: string[];
  tables: string[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RulesConstraints".
 */
export interface RulesConstraints {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  rule_refs: string[];
  constraints: string[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "DeliverablesAcceptance".
 */
export interface DeliverablesAcceptance {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  /**
   * @minItems 1
   */
  deliverables: [string, ...string[]];
  /**
   * @minItems 1
   */
  acceptance_criteria: [string, ...string[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "AssumptionsOpenPoints".
 */
export interface AssumptionsOpenPoints {
  /**
   * @minItems 1
   */
  citations: [Citation, ...Citation[]];
  assumptions: string[];
  open_points: string[];
}
/**
 * One Temporal execution as the API returns it (mirror of `temporal_runs`).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Run".
 */
export interface Run {
  id: string;
  /**
   * Pattern: `^(?:pipeline-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|ingest-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|build-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[1-9][0-9]*|ping-sbam-(?:ingest|knowledge|llm|build|test|certify)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|knowledge-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$`
   */
  temporal_workflow_id: string;
  temporal_run_id: string;
  /**
   * Workflow type name
   */
  type: string;
  subject_type: SubjectType;
  subject_id: string;
  state: RunState;
  current_stage?: string | null;
  last_error?: string | null;
  started_at: string;
  updated_at: string;
}
/**
 * `202 Accepted` body of every long-running command.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RunAccepted".
 */
export interface RunAccepted {
  run: Run;
}
/**
 * `runtime_registry.json` as the generator writes it and the sync loads it.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RuntimeRegistry".
 */
export interface RuntimeRegistry {
  image_digest?: string | null;
  /**
   * Pattern: `^\d+\.\d+\.\d+$`
   */
  python_version: string;
  /**
   * @minItems 1
   */
  packages: [RuntimePackage, ...RuntimePackage[]];
  generated_at: string;
  /**
   * Pattern: `^sha256:[0-9a-f]{64}$`
   */
  content_sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RuntimePackage".
 */
export interface RuntimePackage {
  /**
   * Pattern: `^[a-z0-9]+(-[a-z0-9]+)*$`
   */
  name: string;
  version: string;
  /**
   * Items: Pattern: `^[A-Za-z_][A-Za-z0-9_]*$`
   */
  top_level_modules: string[];
  allowed: boolean;
}
/**
 * Every state enum in one model, so the generated TypeScript exports them as string unions.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "StateMachineStates".
 */
export interface StateMachineStates {
  actor: Actor;
  machine: MachineName;
  project_pipeline: ProjectPipelineState;
  workflow: WorkflowState;
  document_version: DocumentVersionState;
  sow_version: SowVersionState;
  sub_skill: SubSkillState;
  agent: AgentState;
  knowledge_version: KnowledgeVersionState;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Tenant".
 */
export interface Tenant {
  id: string;
  name: string;
  keycloak_org_id: string | null;
  status: TenantStatus;
  created_at: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "TenantCreate".
 */
export interface TenantCreate {
  /**
   * Pattern: `^[^\x00-\x1f\x7f-\x9f]+$`
   */
  name: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "TenantPatch".
 */
export interface TenantPatch {
  status: TenantStatus;
}
/**
 * GET /me. `user` and `tenant` are null for a platform super-admin, who has no tenant.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Me".
 */
export interface Me {
  user: UserProfile | null;
  tenant: Tenant | null;
  roles: string[];
  assigned_projects: string[];
}
/**
 * The caller's row in the users mirror, refreshed from the token on login.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UserProfile".
 */
export interface UserProfile {
  id: string;
  keycloak_sub: string;
  tenant_id: string;
  email: string | null;
  display_name: string | null;
  portal_roles: string[];
  last_login_at: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UserInvite".
 */
export interface UserInvite {
  email: string;
  roles: ("sbam-admin" | "sbam-fde")[];
}
/**
 * Partial update: `roles` replaces the user's portal roles; `active=false` deactivates.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UserPatch".
 */
export interface UserPatch {
  roles?: ("sbam-admin" | "sbam-fde")[] | null;
  active?: boolean | null;
}
/**
 * A user of the caller's tenant, as the admin sees it. The ETag header carries row_version.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "TenantUser".
 */
export interface TenantUser {
  id: string;
  email: string | null;
  display_name: string | null;
  portal_roles: string[];
  status: UserStatus;
  last_login_at: string | null;
  created_at: string;
  row_version: number;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Project".
 */
export interface Project {
  id: string;
  tenant_id: string;
  name: string;
  client_name: string | null;
  description: string | null;
  context_notes: string | null;
  allow_restricted?: boolean;
  status: ProjectStatus;
  pipeline_state: ProjectPipelineState;
  row_version: number;
  created_at: string;
  updated_at: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProjectCreate".
 */
export interface ProjectCreate {
  /**
   * Pattern: `^[^\x00-\x1f\x7f-\x9f]*$`
   */
  name: string;
  client_name?: string | null;
  description?: string | null;
  context_notes?: string | null;
}
/**
 * Partial update: only the fields sent change. `null` clears an optional field.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProjectPatch".
 */
export interface ProjectPatch {
  name?: string | null;
  client_name?: string | null;
  description?: string | null;
  context_notes?: string | null;
  allow_restricted?: boolean | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProjectMembersPut".
 */
export interface ProjectMembersPut {
  /**
   * @maxItems 50
   */
  user_ids: string[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ProjectMembers".
 */
export interface ProjectMembers {
  project_id: string;
  members: TenantUser[];
}
/**
 * `GET /projects/{id}/pipeline`: the dashboard's pipeline panel.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PipelineTimeline".
 */
export interface PipelineTimeline {
  state: ProjectPipelineState;
  steps: PipelineStep[];
  counts: PipelineCounts;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PipelineStep".
 */
export interface PipelineStep {
  name: ProjectPipelineState;
  status: PipelineStepStatus;
  started_at: string | null;
  ended_at: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PipelineCounts".
 */
export interface PipelineCounts {
  uploaded: number;
  parsed: number;
  rejected: number;
  failed: number;
}
/**
 * One Workflow SBAM created for an approved SOW version (read-only).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "WorkflowSummary".
 */
export interface WorkflowSummary {
  id: string;
  project_id: string;
  sow_version_id: string;
  version: number;
  state: WorkflowState;
  created_at: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UploadRequest".
 */
export interface UploadRequest {
  /**
   * @minItems 1
   * @maxItems 20
   */
  files:
    | [UploadFile]
    | [UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile, UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile]
    | [UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile, UploadFile]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ]
    | [
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile,
        UploadFile
      ];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UploadFile".
 */
export interface UploadFile {
  /**
   * Pattern: `^[^/\\\x00-\x1f\x7f-\x9f]+$`
   */
  name: string;
  size: number;
  mime: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "UploadToken".
 */
export interface UploadToken {
  token: string;
  tus_endpoint: string;
  expires_at: string;
  accepted: AcceptedFile[];
  rejected: RejectedFile[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "AcceptedFile".
 */
export interface AcceptedFile {
  name: string;
  size: number;
  mime: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "RejectedFile".
 */
export interface RejectedFile {
  name: string;
  code: ErrorCode;
}
/**
 * Result of the `scan_clamav` activity: the verdict and the signature set that gave it.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ScanOutcome".
 */
export interface ScanOutcome {
  document_version_id: string;
  verdict: ScanStatus;
  signature_version?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MasterEntryCreate".
 */
export interface MasterEntryCreate {
  payload: {
    [k: string]: unknown | undefined;
  };
  row_label?: string | null;
  project_id?: string | null;
}
/**
 * The full new payload. The entry's ETag goes in `If-Match`.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MasterVersionCreate".
 */
export interface MasterVersionCreate {
  payload: {
    [k: string]: unknown | undefined;
  };
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MasterEntry".
 */
export interface MasterEntry {
  id: string;
  tenant_id: string;
  project_id: string | null;
  master_type: MasterType;
  stable_id: string;
  row_label: string;
  status: MasterStatus;
  source_kind: SourceKind;
  version: number;
  payload: {
    [k: string]: unknown | undefined;
  };
  created_at: string;
  updated_at: string;
}
/**
 * GET /admin/filetypes (E3.7): a frozen Filetype row plus SBAM's loader configuration.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "FiletypeInfo".
 */
export interface FiletypeInfo {
  row_label: string;
  file_format: string;
  tier: number;
  purpose: string;
  rag_ingestion: string;
  example: string;
  mvp_enabled: boolean;
  loader: string | null;
  extensions: string[];
  intent_class: string;
  default_route: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MastersSnapshot".
 */
export interface MastersSnapshot {
  id: string;
  tenant_id: string;
  project_id: string;
  entries: SnapshotEntry[];
  /**
   * Pattern: `^sha256:[0-9a-f]{64}$`
   */
  sha256: string;
  created_at: string;
}
/**
 * One pinned entry. The snapshot pins every live entry of the project; whoever uses it decides
 * inclusion from `status` and `restricted` (E3.6: ILLUSTRATIVE / RETIRED never, Restricted only
 * when the project allows it).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SnapshotEntry".
 */
export interface SnapshotEntry {
  entry_id: string;
  master_type: MasterType;
  stable_id: string;
  version: number;
  status: MasterStatus;
  restricted?: boolean;
}
/**
 * What went into masters.jsonl and what didn't; included + excluded = snapshot size.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "InclusionReport".
 */
export interface InclusionReport {
  snapshot_id: string;
  dataset: string;
  storage_key: string;
  sha256: string;
  included: string[];
  excluded: ExcludedEntry[];
  refresh_due: string[];
  warnings: string[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ExcludedEntry".
 */
export interface ExcludedEntry {
  stable_id: string;
  master_type: MasterType;
  reason: "RESTRICTED" | "ILLUSTRATIVE" | "RETIRED";
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ImportReport".
 */
export interface ImportReport {
  mode: ImportMode;
  applied: boolean;
  /**
   * Pattern: `^sha256:[0-9a-f]{64}$`
   */
  sha256: string;
  tabs: ImportTabReport[];
  stable_ids: string[];
  statuses: {
    [k: string]: number | undefined;
  };
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ImportTabReport".
 */
export interface ImportTabReport {
  name: string;
  created?: number;
  updated?: number;
  unchanged?: number;
  missing?: number;
  errors?: ImportRowError[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "ImportRowError".
 */
export interface ImportRowError {
  tab: string;
  row?: number | null;
  column?: string | null;
  message: string;
}
/**
 * A document with the state of its stored file (`GET`, `POST .../restore`).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "DocumentOut".
 */
export interface DocumentOut {
  id: string;
  title: string;
  state: DocumentVersionState;
  size: number;
  mime: string;
  sha256: string;
  created_at: string;
  deleted_at?: string | null;
  deleted_by?: string | null;
  purge_after?: string | null;
}
/**
 * One deleted document that is due for purging (IDs only, like every workflow input).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PurgeTarget".
 */
export interface PurgeTarget {
  tenant_id: string;
  project_id: string;
  document_id: string;
}
/**
 * Input of the per-document purge activity; `as_of` is the workflow clock (workflow.now()).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PurgeRequest".
 */
export interface PurgeRequest {
  target: PurgeTarget;
  as_of: string;
}
/**
 * What one `PurgeDeleted` run did. `failed` documents are retried by the next run.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PurgeResult".
 */
export interface PurgeResult {
  purged?: number;
  skipped?: number;
  failed?: number;
}
/**
 * What one `CleanupAbandonedUploads` run did (E4.12). `failed` files are retried next run.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "CleanupResult".
 */
export interface CleanupResult {
  files_removed?: number;
  rows_deleted?: number;
  orphans?: number;
  failed?: number;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "Notification".
 */
export interface Notification {
  id: string;
  project_id: string | null;
  workflow_id: string | null;
  type: NotificationType;
  title: string;
  body: string;
  link: string | null;
  created_at: string;
  read_at: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "AuditEntry".
 */
export interface AuditEntry {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor_id: string;
  before: {
    [k: string]: unknown | undefined;
  } | null;
  after: {
    [k: string]: unknown | undefined;
  } | null;
  correlation_id: string;
  created_at: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "BundleManifest".
 */
export interface BundleManifest {
  id: string;
  project_id: string;
  workflow_id?: string | null;
  knowledge_version: number;
  okf_version?: "1.0";
  sources: BundleSource[];
  master_snapshot_ref?: MasterSnapshotRef | null;
  parts: {
    [k: string]: PartRef | undefined;
  };
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "BundleSource".
 */
export interface BundleSource {
  document_version_id: string;
  /**
   * Pattern: `^[0-9a-f]{64}$`
   */
  sha256: string;
  type?: "md";
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "MasterSnapshotRef".
 */
export interface MasterSnapshotRef {
  snapshot_id: string;
  /**
   * Pattern: `^[0-9a-f]{64}$`
   */
  sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "PartRef".
 */
export interface PartRef {
  storage_key: string;
  /**
   * Pattern: `^[0-9a-f]{64}$`
   */
  sha256: string;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "OkfEntity".
 */
export interface OkfEntity {
  id: string;
  type: string;
  name: string;
  attributes?: {
    [k: string]: string | undefined;
  };
  /**
   * @minItems 1
   */
  source_refs: [SourceRef, ...SourceRef[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SourceRef".
 */
export interface SourceRef {
  document_version_id: string;
  chunk_id: string;
  location: SbamContracts_Okf__Location;
}
/**
 * Where a chunk is in its source (E4.5's doc.json provenance). `scope` is `document` when only
 * the whole document is known (no doc.json, or an entity found in no block).
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "sbam_contracts__okf__Location".
 */
export interface SbamContracts_Okf__Location {
  scope?: "block" | "document";
  page?: number | null;
  sheet?: string | null;
  cell?: string | null;
  line?: number | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "OkfRelation".
 */
export interface OkfRelation {
  id: string;
  type: string;
  from: string;
  to: string;
  attributes?: {
    [k: string]: string | undefined;
  };
  /**
   * @minItems 1
   */
  source_refs: [SourceRef, ...SourceRef[]];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "OkfChunk".
 */
export interface OkfChunk {
  id: string;
  document_version_id: string;
  location: SbamContracts_Okf__Location;
  text: string;
  ocr?: string | null;
  unreadable?: boolean;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "BundleCoverage".
 */
export interface BundleCoverage {
  documents: DocumentCoverage[];
  dropped_entities?: number;
  warnings?: string[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "DocumentCoverage".
 */
export interface DocumentCoverage {
  document_version_id: string;
  status: CoverageStatus;
  chunks?: number;
  entities?: number;
  reason?: string | null;
}
/**
 * `GET P/knowledge`: this project's versions, newest first.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeVersionList".
 */
export interface KnowledgeVersionList {
  versions: KnowledgeVersionSummary[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeVersionSummary".
 */
export interface KnowledgeVersionSummary {
  id: string;
  version: number;
  status: string;
  coverage: KnowledgeCoverage;
  error_reason?: string | null;
  created_at: string;
  finished_at?: string | null;
  okf_sha256?: string | null;
  counts?: KnowledgeCounts;
  documents?: CoverageRow[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeCoverage".
 */
export interface KnowledgeCoverage {
  documents?: CoverageEntry[];
  masters_included?: number;
  entities?: number;
  relations?: number;
  chunks?: number;
  bundle?: BundleCoverage | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "CoverageEntry".
 */
export interface CoverageEntry {
  document_version_id: string;
  status: SourceStatus;
  reason?: string | null;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeCounts".
 */
export interface KnowledgeCounts {
  documents?: number;
  entities?: number;
  relations?: number;
  chunks?: number;
}
/**
 * One document of a version, ready to show (E5.6): the bundle's view once it exists, else the
 * build's. `title` is the uploaded file's name; `reason` is a code, shown as text.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "CoverageRow".
 */
export interface CoverageRow {
  document_version_id: string;
  document_id?: string | null;
  title: string;
  status: string;
  chunks?: number;
  entities?: number;
  reason?: string | null;
}
/**
 * `GET P/knowledge/{kv_id}/graph`: nodes sorted by id; each edge on its `from` node's page.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeGraphPage".
 */
export interface KnowledgeGraphPage {
  knowledge_version_id: string;
  status: string;
  nodes: GraphApiNode[];
  edges: GraphApiEdge[];
  next_cursor?: string | null;
  total_estimate: number;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "GraphApiNode".
 */
export interface GraphApiNode {
  id: string;
  type: string;
  name: string;
  degree?: number;
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "GraphApiEdge".
 */
export interface GraphApiEdge {
  id: string;
  type: string;
  from: string;
  to: string;
  external?: boolean;
}
/**
 * `GET P/knowledge/{kv_id}/nodes/{node_id}`.
 *
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "KnowledgeNodeDetail".
 */
export interface KnowledgeNodeDetail {
  knowledge_version_id: string;
  status: string;
  node: GraphApiNode;
  neighbours: GraphApiNode[];
  has_more?: boolean;
  source_passages: SourcePassage[];
}
/**
 * This interface was referenced by `SbamContracts`'s JSON-Schema
 * via the `definition` "SourcePassage".
 */
export interface SourcePassage {
  document_version_id: string;
  title: string;
  location: SbamContracts_Okf__Location;
  text: string;
  truncated?: boolean;
}
