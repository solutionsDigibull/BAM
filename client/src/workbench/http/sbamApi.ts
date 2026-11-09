// The SBAM implementation of the workbench's BamApi seam. Step 1 is wired for real: uploads, scan states, the
// project pipeline, the knowledge build and the SOW generation status. A method whose backend or screen is not
// wired yet rejects with `NotWiredError`, so the screen shows an error state, never an invented result.
import type { BamApi } from "../api/adapter";
import type { IngestionRun } from "../api/types";
import { ragPipeline, setRagActions } from "../rag/ragPipeline";
import { API_BASE } from "@/providers/data-provider";
import { ApiFailure, deleteJson, idempotencyKey, postJson } from "./http";
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
    uploadFile: (file, onProgress) => uploadOne(projectId, file, onProgress),
    removeUpload: async (id) => {
      try {
        await deleteJson(`projects/${projectId}/documents/${id}`);
      } catch (e) {
        throw new Error(problemMessage(e));
      }
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
    deployToStaging: notWired("Deploying"),
  };
}

/** The signed URL of a ready knowledge version's bundle; the proxy forwards the API's redirect. */
export const okfBundleUrl = (projectId: string, versionId: string): string =>
  `${API_BASE}/projects/${projectId}/knowledge/${versionId}/okf-bundle`;
