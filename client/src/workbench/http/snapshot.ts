// The workbench's Snapshot, composed from the SBAM API: documents, the project pipeline and the knowledge
// versions. Every status shown comes from one of these backend objects; nothing is invented. Steps 2 to 4
// (skills, contract and checks, tests): step 2 follows the project's Workflow; 3 and 4 stay locked until their screens are wired.
import type {
  DocumentOut,
  KnowledgeGraphPage,
  KnowledgeVersionList,
  KnowledgeVersionSummary,
  PipelineTimeline,
  ProjectPipelineState,
  SowOverview,
  WorkflowState,
  WorkflowSummary,
} from "@/contracts";

import type { IngestionRun, RunStatus, Snapshot, Sow, Stage, StepId, StepInfo } from "../api/types";
import { setRagState, type KnowledgeGraph, type KnowledgeStatus } from "../rag/ragPipeline";
import { isActive, scanEventsOf, toUploadRecord } from "./documents";
import { getJson, listAll } from "./http";

const TITLES = ["SOW generation and approval", "SOW to ISATVON skills", "Contract and checks", "Test, persist, deploy"];
const LOCKED = [
  "",
  "Approve the SOW to unlock the skills grid",
  "Generate the ISATVON contract in the skills grid to unlock validation",
  "Approve the manifest and code in validation to unlock testing",
];

/** The grid step from the project's Workflow (SBAM creates it when the SOW is approved; the FDE never does). */
const BUILD_STARTED: ReadonlySet<WorkflowState> = new Set(["BUILDING", "CERTIFYING", "CERTIFIED", "FROZEN"]);
export function gridStep(w: WorkflowSummary | undefined, pipeline: ProjectPipelineState): Pick<StepInfo, "state" | "reason" | "editable"> {
  if (!w) {
    return pipeline === "SOW_APPROVED"
      ? { state: "active", reason: "Creating the workflow from the approved SOW", editable: false }
      : { state: "locked", reason: LOCKED[1]!, editable: false };
  }
  switch (w.state) {
    case "CREATED":
    case "ISATVON_EXTRACTING":
      return { state: "active", reason: "Extracting the skills from the approved SOW", editable: false };
    case "ISATVON_REVIEW":
      return { state: "needs_review", reason: "Review the skills, then Generate", editable: true };
    case "NEEDS_FDE_REVIEW":
      return { state: "needs_review", reason: "Some skills need your review", editable: true };
    case "EXTRACTION_FAILED":
    case "WORKFLOW_VALIDATION_FAILED":
      return { state: "failed", reason: "The skills could not be extracted. Regenerate them in the grid.", editable: true };
    case "CANCELLED":
      return { state: "locked", reason: "This workflow was cancelled", editable: false };
    default:
      return { state: "done", reason: "Completed: the build has started", editable: false };
  }
}
export const buildStarted = (w: WorkflowSummary | undefined): boolean => !!w && BUILD_STARTED.has(w.state);
const NOT_WIRED = "The build has started. Its screens are not available in the workbench yet.";

const FAILED: ProjectPipelineState[] = ["INGESTION_FAILED", "KNOWLEDGE_FAILED", "SOW_GENERATION_FAILED"];

/** From these pipeline states on, the project has (or is making) a SOW; before that there is nothing to ask for. */
const HAS_SOW: ReadonlySet<ProjectPipelineState> = new Set(["SOW_GENERATING", "SOW_REVIEW", "SOW_APPROVED", "WORKFLOW_CREATED"]);

/** The SOW as the shell reads it (tracker status, what blocks the next step, the assistant): its version, whether it is
 *  approved or being regenerated, and the approval. The document itself is the Scope screen's, never copied here. */
export function sowOf(o: SowOverview | undefined, state: ProjectPipelineState): Sow | undefined {
  const cur = o?.current;
  if (!o || !cur) return undefined;
  const appr = o.approved;
  return {
    version: cur.version,
    generatedVersion: cur.version,
    html: "",
    generatedHtml: "",
    meta: { sowId: o.sow_id, client: "", lengthMode: "", sources: 0 },
    updatedAt: cur.created_at,
    status: appr && appr.version === cur.version ? "approved" : state === "SOW_GENERATING" ? "regenerating" : "draft",
    approval: appr ? { version: appr.version, at: appr.created_at, by: appr.author ?? "" } : undefined,
  };
}

/** From SOW approval on, SBAM makes (or has made) the Workflow. */
const HAS_WORKFLOW: ReadonlySet<ProjectPipelineState> = new Set(["SOW_APPROVED", "WORKFLOW_CREATED"]);
const newest = (items: WorkflowSummary[]): WorkflowSummary | undefined =>
  [...items].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

const graphs = new Map<string, KnowledgeGraph>();
const GRAPH_PAGE = 500;
const GRAPH_PAGES = 10;

/** The knowledge graph of a ready version: fetched once, then kept (a version never changes). */
async function graphOf(projectId: string, kv: KnowledgeVersionSummary): Promise<KnowledgeGraph | undefined> {
  if (kv.status !== "READY") return undefined;
  const cached = graphs.get(kv.id);
  if (cached) return cached;
  const nodes: KnowledgeGraph["nodes"] = [];
  const edges: KnowledgeGraph["edges"] = [];
  let cursor: string | null | undefined;
  for (let n = 0; n < GRAPH_PAGES; n++) {
    const qs = new URLSearchParams({ limit: String(GRAPH_PAGE) });
    if (cursor) qs.set("cursor", cursor);
    const { body } = await getJson<KnowledgeGraphPage>(`projects/${projectId}/knowledge/${kv.id}/graph?${qs}`);
    for (const x of body.nodes) nodes.push({ id: x.id, label: x.name, type: x.type, degree: x.degree });
    for (const e of body.edges) edges.push({ source: e.from, target: e.to, label: e.type });
    cursor = body.next_cursor;
    if (!cursor) break;
  }
  const graph = { nodes, edges };
  graphs.set(kv.id, graph);
  return graph;
}

const latest = (versions: KnowledgeVersionSummary[]): KnowledgeVersionSummary | undefined =>
  [...versions].sort((a, b) => b.version - a.version)[0];

const stage = (key: string, label: string, status: RunStatus, error?: string): Stage => ({ key, label, status, error });

function sowStages(state: ProjectPipelineState): Stage[] {
  const label = "Generate the SOW";
  if (state === "SOW_GENERATING") return [stage("sow", label, "running")];
  if (state === "SOW_GENERATION_FAILED") return [stage("sow", label, "failed", "The SOW could not be generated.")];
  if (state === "SOW_REVIEW" || state === "SOW_APPROVED" || state === "WORKFLOW_CREATED") return [stage("sow", label, "succeeded")];
  return [];
}

function stepOne(state: ProjectPipelineState, files: number, error?: string): Pick<StepInfo, "state" | "reason"> {
  if (FAILED.includes(state)) return { state: "failed", reason: error ?? "Processing failed. Retry it." };
  switch (state) {
    case "INGESTING":
      return { state: "active", reason: "Scanning and parsing the documents" };
    case "KNOWLEDGE_BUILDING":
      return { state: "active", reason: "Building the knowledge from the documents" };
    case "SOW_GENERATING":
      return { state: "active", reason: "Generating the SOW from the knowledge" };
    case "SOW_REVIEW":
      return { state: "needs_review", reason: "The SOW is waiting for your review" };
    case "SOW_APPROVED":
    case "WORKFLOW_CREATED":
      return { state: "done", reason: "Completed" };
    default:
      return { state: "active", reason: files ? "Start processing once every file check passes" : "Upload documents in Upload" };
  }
}

export async function buildSnapshot(projectId: string): Promise<Snapshot> {
  const [documents, pipeline, knowledge] = await Promise.all([
    listAll<DocumentOut>(`projects/${projectId}/documents`),
    getJson<PipelineTimeline>(`projects/${projectId}/pipeline`).then((r) => r.body),
    getJson<KnowledgeVersionList>(`projects/${projectId}/knowledge`).then((r) => r.body.versions),
  ]);
  const sowOverview = HAS_SOW.has(pipeline.state)
    ? await getJson<SowOverview>(`projects/${projectId}/sow`).then((r) => r.body, () => undefined)
    : undefined;
  const workflow = HAS_WORKFLOW.has(pipeline.state)
    ? await getJson<{ items: WorkflowSummary[] }>(`projects/${projectId}/workflows`).then((r) => newest(r.body.items), () => undefined)
    : undefined;
  const active = documents.filter(isActive);
  const uploads = active.map(toUploadRecord);
  const kv = latest(knowledge);
  const graphData = kv ? await graphOf(projectId, kv) : undefined;

  const c = kv?.counts;
  const cov = kv?.coverage;
  setRagState({
    loaded: true,
    runId: kv?.id,
    version: kv?.version,
    status: kv?.status as KnowledgeStatus | undefined,
    error: kv?.error_reason ?? undefined,
    lastSyncAt: kv?.finished_at ?? undefined,
    counts: {
      documents: c?.documents ?? cov?.documents?.length ?? 0,
      entities: c?.entities ?? cov?.entities ?? 0,
      relations: c?.relations ?? cov?.relations ?? 0,
      chunks: c?.chunks ?? cov?.chunks ?? 0,
    },
    masters:
      kv?.coverage?.masters_included !== undefined
        ? { included: kv.coverage.masters_included, documents: kv.coverage.master_documents ?? 0 }
        : undefined,
    docs: (kv?.documents ?? []).map((d) => ({
      id: d.document_version_id,
      title: d.title,
      status: d.status,
      chunks: d.chunks ?? 0,
      entities: d.entities ?? 0,
      reason: d.reason ?? undefined,
    })),
    graphData,
  });

  const state = pipeline.state;
  const started = pipeline.steps.find((s) => s.started_at)?.started_at;
  const ingestion: IngestionRun | undefined =
    state === "IDLE" || state === "CANCELLED"
      ? undefined
      : { runId: "pipeline", at: started ?? new Date().toISOString(), fileIds: uploads.filter((u) => u.released).map((u) => u.id) };

  const one = stepOne(state, uploads.length, kv?.error_reason ?? undefined);
  const steps: StepInfo[] = ([1, 2, 3, 4] as StepId[]).map((n) => {
    const info =
      n === 1
        ? { ...one, editable: one.state !== "locked" }
        : n === 2
          ? gridStep(workflow, state)
          : { state: "locked" as const, reason: buildStarted(workflow) ? NOT_WIRED : LOCKED[n - 1]!, editable: false };
    return {
      id: n,
      title: TITLES[n - 1]!,
      ...info,
      stale: false,
      background: n === 1 && ["INGESTING", "KNOWLEDGE_BUILDING", "SOW_GENERATING"].includes(state) ? 1 : 0,
    };
  });

  return {
    serverTime: new Date().toISOString(),
    workflowName: workflow?.name ?? "Workflow",
    activeStep: workflow && workflow.state !== "CANCELLED" ? 2 : 1,
    steps,
    uploads,
    scanEvents: scanEventsOf(documents),
    ingestion,
    sowGeneration: sowStages(state),
    sow: sowOf(sowOverview, state),
    extraction: [],
    skillsRevision: 0,
    testHistory: [],
    versions: [],
    persist: { artifacts: [], deployedSkills: [] },
  };
}

/** Forget cached graphs (a different project). */
export const resetSnapshotCache = (): void => graphs.clear();
