// The workbench's Snapshot, composed from the SBAM API: documents, the project pipeline and the knowledge
// versions. Every status shown comes from one of these backend objects; nothing is invented. Steps 2 to 4
// (skills, contract and checks, tests) are filled in by their own screens' wiring and stay locked here.
import type {
  DocumentOut,
  KnowledgeGraphPage,
  KnowledgeVersionList,
  KnowledgeVersionSummary,
  PipelineTimeline,
  ProjectPipelineState,
} from "@/contracts";

import type { IngestionRun, RunStatus, Snapshot, Stage, StepId, StepInfo } from "../api/types";
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

const FAILED: ProjectPipelineState[] = ["INGESTION_FAILED", "KNOWLEDGE_FAILED", "SOW_GENERATION_FAILED"];

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
    const info = n === 1 ? one : { state: "locked" as const, reason: LOCKED[n - 1]! };
    return {
      id: n,
      title: TITLES[n - 1]!,
      ...info,
      stale: false,
      editable: n === 1 && info.state !== "locked",
      background: n === 1 && ["INGESTING", "KNOWLEDGE_BUILDING", "SOW_GENERATING"].includes(state) ? 1 : 0,
    };
  });

  return {
    serverTime: new Date().toISOString(),
    workflowName: "Workflow",
    activeStep: 1,
    steps,
    uploads,
    scanEvents: scanEventsOf(documents),
    ingestion,
    sowGeneration: sowStages(state),
    extraction: [],
    skillsRevision: 0,
    testHistory: [],
    versions: [],
    persist: { artifacts: [], deployedSkills: [] },
  };
}

/** Forget cached graphs (a different project). */
export const resetSnapshotCache = (): void => graphs.clear();
