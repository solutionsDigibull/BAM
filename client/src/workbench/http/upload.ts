// One file to the project: announce it (POST uploads -> token), then send it to tusd with that token as tus
// metadata. Only the upload token reaches tusd: no credentials, no Authorization header (the browser never
// holds the Keycloak token, and tusd must not see it).
import Uppy from "@uppy/core";
import Tus from "@uppy/tus";

import type { UploadToken } from "@/contracts";

import type { UploadRecord } from "../api/types";
import { REASONS, reasonFor } from "./documents";
import { ApiFailure, postJson } from "./http";

/** Reason for a tus request tusd or the API's hook refused: problem+json from the hook, or tusd's own 413. */
export function describeTusError(error: unknown): string {
  const response = (
    error as { originalResponse?: { getStatus(): number; getBody(): string } | null } | null
  )?.originalResponse;
  const status = response?.getStatus();
  if (status === 413) return REASONS.FILE_TOO_LARGE!;
  try {
    const problem = JSON.parse(response?.getBody() ?? "") as { code?: string; detail?: string };
    return reasonFor(problem.code, problem.detail ?? "Upload failed");
  } catch {
    return status ? `Upload failed (${status})` : "Upload interrupted: it resumes when you retry";
  }
}

export interface UploadInput {
  name: string;
  size: number;
  type?: string;
  data: Blob;
}

export async function uploadOne(
  projectId: string,
  file: UploadInput,
  onProgress: (loaded: number, total: number) => void,
): Promise<UploadRecord> {
  const mime = file.type || "application/octet-stream";
  let answer: UploadToken;
  try {
    ({ body: answer } = await postJson<UploadToken>(`projects/${projectId}/uploads`, {
      files: [{ name: file.name, size: file.size, mime }],
    }));
  } catch (error) {
    const e = error as ApiFailure;
    throw new Error(reasonFor(e.problem?.code, e.problem?.detail ?? e.message));
  }
  if (!answer.accepted.length) {
    throw new Error(reasonFor(answer.rejected[0]?.code, REASONS.NOT_ACCEPTED));
  }

  const uppy = new Uppy({ autoProceed: false });
  uppy.use(Tus, {
    endpoint: answer.tus_endpoint,
    retryDelays: [0, 1000, 3000, 5000, 10000],
    limit: 1,
    withCredentials: false,
    removeFingerprintOnSuccess: true,
    allowedMetaFields: ["name", "type", "token", "project_id"],
  });
  uppy.addFile({
    name: file.name,
    type: mime,
    data: file.data,
    meta: { token: answer.token, project_id: projectId },
  });
  uppy.on("upload-progress", (_f, p) => onProgress(p.bytesUploaded as number, p.bytesTotal as number));
  try {
    const result = await uppy.upload();
    const failed = result?.failed?.[0];
    if (failed) throw new Error(describeTusError(failed.error ?? failed));
  } catch (error) {
    throw error instanceof Error && !("originalResponse" in error)
      ? error
      : new Error(describeTusError(error));
  } finally {
    uppy.destroy();
  }
  // The document row appears when tusd has stored it; the next snapshot lists it with its scan state.
  return {
    id: `pending:${file.name}`,
    batchId: "project",
    name: file.name,
    size: file.size,
    status: "stored",
    uploadedAt: new Date().toISOString(),
    objectKey: "",
    parts: 1,
    scan: "scanning",
    released: false,
    clamavVerdict: "pending",
  };
}
