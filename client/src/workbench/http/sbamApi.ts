// The SBAM implementation of the workbench's BamApi seam. Step 1 is wired for real: uploads, scan states, the
// project pipeline, the knowledge build and the SOW generation status. A method whose backend or screen is not
// wired yet rejects with `NotWiredError`, so the screen shows an error state, never an invented result.
import type { BamApi } from "../api/adapter";
import type { IngestionRun } from "../api/types";
import type { RetryAccepted } from "../lib/workflowBuilds";
import { ragPipeline, setRagActions } from "../rag/ragPipeline";
import { API_BASE } from "@/providers/data-provider";
import type {
  BuildsResponse,
  ChangeNoteList,
  CommitAccepted,
  CommitStatus,
  Connection,
  ConnectionList,
  CodeAttemptList,
  CodeDiff,
  CodeFileView,
  GatesResponse,
  ManifestVersionList,
  ManifestView,
  HeldPatchResponse,
  PatchDecided,
  KnowledgeNodeDetail,
  ConnectionTestResult,
  ConnectionValueResult,
  RegisteredWorkflow,
  RepoAnswered,
  RepoQuestions,
  RequestChangeAccepted,
  StagingStatus,
  WorkflowCodeTree,
  WorkflowFileView,
  WorkflowRepositories,
} from "@/contracts";
import { ApiFailure, deleteJson, getJson, idempotencyKey, postJson, putJson } from "./http";
import { buildSnapshot, resetSnapshotCache } from "./snapshot";
import { uploadOne } from "./upload";

export class NotWiredError extends Error {
  constructor(what: string) {
    super(`${what} is not available yet.`);
    this.name = "NotWiredError";
  }
}

const notWired =
  (what: string) =>
  (): Promise<never> =>
    Promise.reject(new NotWiredError(what));

const problemMessage = (e: unknown): string => {
  const f = e as ApiFailure;
  return f.problem?.detail ?? f.message ?? "The request failed.";
};

export function createSbamApi(projectId: string): BamApi {
  resetSnapshotCache();

  async function startRun(): Promise<IngestionRun> {
    try {
      await postJson(`projects/${projectId}/pipeline/start`, {}, { "Idempotency-Key": idempotencyKey() });
    } catch (e) {
      throw new Error(problemMessage(e));
    }
    return { runId: "pipeline", at: new Date().toISOString(), fileIds: [] };
  }

  // The knowledge build starts with the project pipeline; a rebuild of a failed build is the same command.
  setRagActions({
    start: async () => {
      await startRun();
    },
    retry: async () => {
      await startRun();
    },
    downloadBundle: () => {
      const id = ragPipeline.getState().runId;
      if (id) window.location.assign(okfBundleUrl(projectId, id));
    },
  });

  return {
    getSnapshot: () => buildSnapshot(projectId),
    getSkills: notWired("The skills grid"),
    getCode: notWired("The generated code"),
    // Read-only whole-workflow code (#763): the stored files with their tags, and one file at a time.
    getWorkflowCode: async (workflowId) =>
      (await getJson<WorkflowCodeTree>(`projects/${projectId}/workflows/${workflowId}/code`)).body,
    getWorkflowFile: async (workflowId, group, path) => {
      const qs = new URLSearchParams({ group, path });
      return (await getJson<WorkflowFileView>(`projects/${projectId}/workflows/${workflowId}/code/files?${qs}`)).body;
    },
    // Read-only repositories of the workflow (#843): patches, the scan report found by URL and commit, components.
    getWorkflowRepositories: async (workflowId) =>
      (await getJson<WorkflowRepositories>(`projects/${projectId}/workflows/${workflowId}/repositories`)).body,
    // The registry entry of the workflow (#849): the newest enabled one, or one version. 404 = none registered.
    getWorkflowRegistration: async (workflowId, version) =>
      (await getJson<RegisteredWorkflow>(
        `projects/${projectId}/workflows/${workflowId}/registration${version ? `?version=${encodeURIComponent(version)}` : ""}`,
      )).body,
    // Commit to Staging (#766): the checks, and the FDE's approval of exactly the hash they looked at.
    getCommitStatus: async (workflowId) =>
      (await getJson<CommitStatus>(`projects/${projectId}/workflows/${workflowId}/commit`)).body,
    commitWorkflow: async (workflowId, manifestHash, key) =>
      (
        await postJson<CommitAccepted>(`projects/${projectId}/workflows/${workflowId}/commit`, undefined, {
          "Idempotency-Key": key,
          "If-Match": `"${manifestHash}"`,
        })
      ).body,
    // One graph entity with the passages it was read from (#712): the node id is opaque, so it is encoded.
    getKnowledgeNode: async (versionId, nodeId) =>
      (await getJson<KnowledgeNodeDetail>(`projects/${projectId}/knowledge/${versionId}/nodes/${encodeURIComponent(nodeId)}`)).body,
    // Staging (#861): the deployment's state and history, and Request change from Staging (#767).
    getStagingStatus: async (workflowId) =>
      (await getJson<StagingStatus>(`projects/${projectId}/workflows/${workflowId}/staging`)).body,
    getChangeNotes: async (workflowId) =>
      (await getJson<ChangeNoteList>(`projects/${projectId}/workflows/${workflowId}/request-change`)).body,
    requestChange: async (workflowId, body, key) =>
      (await postJson<RequestChangeAccepted>(`projects/${projectId}/workflows/${workflowId}/request-change`, body, { "Idempotency-Key": key })).body,
    getWorkflowBuilds: async (workflowId) =>
      (await getJson<BuildsResponse>(`projects/${projectId}/workflows/${workflowId}/builds`)).body,
    // The gates of one Sub-Skill attempt and Retry of a build that waits for the FDE (#715). The server decides the refusals.
    getSubSkillGates: async (workflowId, subId, attempt) =>
      (await getJson<GatesResponse>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/gates${attempt === undefined ? "" : `?attempt=${attempt}`}`)).body,
    retrySubSkill: async (workflowId, subId, reason, key) =>
      (await postJson<RetryAccepted>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/retry`, { reason }, { "Idempotency-Key": key })).body,
    // The code attempts of a Sub-Skill, one file of one and a diff of two (#715, read only). The path is a query value, so it is encoded.
    getCodeAttempts: async (workflowId, subId) =>
      (await getJson<CodeAttemptList>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/code/attempts`)).body,
    getCodeAttemptFile: async (workflowId, subId, attempt, path) =>
      (await getJson<CodeFileView>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/code/attempts/${attempt}/files?${new URLSearchParams({ path })}`)).body,
    getCodeAttemptDiff: async (workflowId, subId, from, to, path) =>
      (await getJson<CodeDiff>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/code/attempts/${from}/diff/${to}?${new URLSearchParams({ path })}`)).body,
    // The stored manifests of the workflow or of one Sub-Skill and one of them (#715, read only).
    getManifestVersions: async (workflowId, subId) =>
      (await getJson<ManifestVersionList>(`projects/${projectId}/workflows/${workflowId}${subId ? `/sub-skills/${subId}` : ""}/manifests`)).body,
    getManifest: async (workflowId, subId, buildNo, version) => {
      const qs = new URLSearchParams();
      if (version && subId) qs.set("version", version);
      if (buildNo !== undefined) qs.set("build_no", String(buildNo));
      return (await getJson<ManifestView>(`projects/${projectId}/workflows/${workflowId}${subId ? `/sub-skills/${subId}` : ""}/manifest${qs.size ? `?${qs}` : ""}`)).body;
    },
    // The held ISATVON patch of a Sub-Skill and the FDE's decision on it (#715): accepting is guarded by the Sub-Skill's hash (If-Match).
    getHeldPatch: async (workflowId, subId) =>
      (await getJson<HeldPatchResponse>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/held-patch`)).body,
    decideHeldPatch: async (workflowId, subId, decision, reason, key, ifMatch) =>
      (
        await postJson<PatchDecided>(
          `projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/held-patch/${decision}`,
          reason ? { reason } : {},
          { "Idempotency-Key": key, ...(ifMatch ? { "If-Match": ifMatch } : {}) },
        )
      ).body,
    // The project's secrets (#764): the value goes out once in a PUT and is never returned.
    getConnections: async () => (await getJson<ConnectionList>(`projects/${projectId}/connections`)).body,
    setConnectionValue: async (name, value, ifMatch) =>
      (
        await putJson<ConnectionValueResult>(
          `projects/${projectId}/connections/${encodeURIComponent(name)}/value`,
          { value },
          ifMatch ? { "If-Match": ifMatch } : undefined,
        )
      ).body,
    clearConnectionValue: async (name, ifMatch) => {
      await deleteJson(`projects/${projectId}/connections/${encodeURIComponent(name)}/value`, ifMatch ? { "If-Match": ifMatch } : undefined);
    },
    updateConnection: async (name, body, ifMatch) =>
      (
        await putJson<Connection>(
          `projects/${projectId}/connections/${encodeURIComponent(name)}`,
          body,
          ifMatch ? { "If-Match": ifMatch } : undefined,
        )
      ).body,
    testConnection: async (name) =>
      (await postJson<ConnectionTestResult>(`projects/${projectId}/connections/${encodeURIComponent(name)}/test`)).body,
    // The choose-a-repository question of the builds and the FDE's answer (#756). The server validates the answer.
    getRepoQuestions: async (workflowId) =>
      (await getJson<RepoQuestions>(`projects/${projectId}/workflows/${workflowId}/repo-questions`)).body,
    answerRepoQuestion: async (workflowId, subSkillId, body, key) =>
      (await postJson<RepoAnswered>(`projects/${projectId}/workflows/${workflowId}/sub-skills/${subSkillId}/repo-answer`, body, { "Idempotency-Key": key })).body,
    uploadFile: (file, onProgress) => uploadOne(projectId, file, onProgress),
    removeUpload: async (id) => {
      try {
        await deleteJson(`projects/${projectId}/documents/${id}`);
      } catch (e) {
        throw new Error(problemMessage(e));
      }
    },
    restoreUpload: async (id) => {
      await postJson(`projects/${projectId}/documents/${id}/restore`);
    },
    retryScan: async (id) => {
      try {
        await postJson(`projects/${projectId}/documents/${id}/retry`);
      } catch (e) {
        throw new Error(problemMessage(e));
      }
    },
    getVerifiedFileAccess: notWired("Opening an uploaded file"),
    startIngestionRun: () => startRun(),
    startSowGeneration: notWired("Starting the SOW generation (the pipeline starts it)"),
    saveSowDraft: notWired("Saving the SOW"),
    approveSow: notWired("Approving the SOW"),
    requestSowChanges: notWired("Requesting SOW changes"),
    reopen: notWired("Reopening a step"),
    closeReopen: notWired("Closing a reopened step"),
    updateSkillField: notWired("Editing a skill"),
    markGridReviewed: notWired("Marking the grid reviewed"),
    generateContract: notWired("Generating the contract"),
    retryActivity: notWired("Retrying a check"),
    saveCode: notWired("Saving code"),
    saveManifest: notWired("Saving the manifest"),
    approveManifest: notWired("Approving the manifest"),
    runTests: notWired("Running the tests"),
    markTestsReviewed: notWired("Marking the tests reviewed"),
    commitToPostgres: notWired("Committing"),
    deployToStaging: notWired("A separate deploy (Commit starts Staging)"),
  };
}

/** The signed URL of a ready knowledge version's bundle; the proxy forwards the API's redirect. */
export const okfBundleUrl = (projectId: string, versionId: string): string =>
  `${API_BASE}/projects/${projectId}/knowledge/${versionId}/okf-bundle`;
