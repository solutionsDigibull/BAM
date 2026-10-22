// The Uppy upload session of the Documents screen (E4.10, TD §8.5): limits, the reasons shown for a refused
// file, and the Uppy instance with its tus uploader. Plain TypeScript, no React, so it can be tested without
// a browser (the Dashboard is mounted by upload-panel.tsx).
//
// The flow: the FDE picks files; the client limits (15 MB, 20 files, allowed types) refuse the obvious ones
// at once; when the upload starts, a pre-processor sends the files' names, sizes and types to
// `POST /projects/{id}/uploads` and gets a short-lived token; the server's per-file refusals are removed
// with their reason; the rest go to tusd, each carrying the token, file name and project id as tus metadata.
// The client limits are UX only: the server checks everything again (E4.2).
import Uppy, { type UppyFile } from "@uppy/core";
import Tus from "@uppy/tus";

import type { UploadRequest, UploadToken } from "@/contracts";
import type { ApiError } from "@/providers/data-provider";

export const MAX_FILE_BYTES = 15 * 1024 * 1024;
export const MAX_ACTIVE_FILES = 20;

// ponytail: mirrors the server's ALLOWED_EXTENSIONS (sbam_api/ingest/rules.py, TD §5.2). Read it from the
// Filetype master (#60, `mvp_enabled`) when that exists. `.gz` stands for `.tar.gz`; the server tells a
// `.tar.gz` from any other `.gz` and says UNSUPPORTED_TYPE for the latter.
export const ALLOWED_EXTENSIONS = [
  "md",
  "txt",
  "eml",
  "msg",
  "json",
  "xml",
  "yaml",
  "yml",
  "py",
  "sql",
  "js",
  "go",
  "cpp",
  "csv",
  "xlsx",
  "docx",
  "pdf",
  "zip",
  "gz",
  "png",
  "jpg",
  "jpeg",
  "tiff",
] as const;
export const ALLOWED_FILE_TYPES = ALLOWED_EXTENSIONS.map((e) => `.${e}`);

export const NOTE =
  "PDF, Word, Excel, CSV, Markdown, text, e-mail, images, archives and source files. " +
  "Up to 15 MB each and 20 files per project.";
export const DUPLICATE_NOTE =
  "Identical files already in this project are linked, not stored twice.";

/** What the FDE reads for each reason the client or the server refuses a file. */
export const REASONS: Record<string, string> = {
  FILE_TOO_LARGE: "Larger than 15 MB",
  UNSUPPORTED_TYPE: "File type not supported",
  FILE_LIMIT_REACHED: "This project already has 20 files",
  DUPLICATE: "Already in this project",
  FORBIDDEN: "This upload is not allowed for this project",
  PROJECT_ARCHIVED: "This project is archived and read-only",
  NOT_FOUND: "Project not found",
  VALIDATION_ERROR: "This file name or size is not valid",
  NOT_ACCEPTED: "The server did not accept this file",
};

export type Meta = { token?: string; project_id?: string };
export type SbamUppy = Uppy<Meta, Record<string, never>>;
type File = UppyFile<Meta, Record<string, never>>;
export type Rejection = { name: string; reason: string };

export const reasonFor = (code: string | undefined, fallback = "Upload failed"): string =>
  (code && REASONS[code]) || fallback;

/** Reason for a refused announce request (problem+json from the data provider). */
export function describeProblem(error: unknown): string {
  const e = error as ApiError | null;
  const code = e?.problem?.code;
  return reasonFor(code, e?.problem?.detail ?? e?.message ?? "Upload failed");
}

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
    return status ? `Upload failed (${status})` : "Upload interrupted — it resumes when you retry";
  }
}

export function announceBody(files: File[]): UploadRequest {
  return {
    files: files.map((f) => ({
      name: f.name ?? "",
      size: f.size ?? 0,
      mime: f.type || "application/octet-stream",
    })),
  } as UploadRequest;
}

const countBy = (names: string[]): Map<string, number> =>
  names.reduce((m, n) => m.set(n, (m.get(n) ?? 0) + 1), new Map<string, number>());

/**
 * Apply the server's answer to the files of this batch. An accepted file gets the token and project id as
 * tus metadata and the tus endpoint; any other file is removed and returned with the reason the server
 * gave. The accepted list is authoritative: a file the server did not accept never goes to tusd.
 */
export function applyToken(
  uppy: SbamUppy,
  projectId: string,
  files: File[],
  answer: UploadToken,
): Rejection[] {
  const accepted = countBy(answer.accepted.map((a) => a.name));
  const why = new Map(answer.rejected.map((r) => [r.name, reasonFor(r.code)]));
  const refused: Rejection[] = [];
  for (const file of files) {
    const name = file.name ?? "";
    const left = accepted.get(name) ?? 0;
    if (left > 0) {
      accepted.set(name, left - 1);
      uppy.setFileMeta(file.id, { token: answer.token, project_id: projectId });
      uppy.setFileState(file.id, { tus: { endpoint: answer.tus_endpoint } });
    } else {
      refused.push({ name, reason: why.get(name) ?? REASONS.NOT_ACCEPTED! });
      uppy.removeFile(file.id);
    }
  }
  return refused;
}

export type UploaderOptions = {
  projectId: string;
  /** Documents of this project that already count toward the 20-file limit. */
  getActive: () => number;
  /** `POST /projects/{id}/uploads`. Throws the data provider's ApiError. */
  announce: (body: UploadRequest) => Promise<UploadToken>;
  onRejected: (rejection: Rejection) => void;
  /** The announce request could not be made (the engine is down, offline): the files stay, retry later. */
  onFailed: (message: string) => void;
};

export function createUploader(opts: UploaderOptions): SbamUppy {
  let overLimit = false;
  const uppy: SbamUppy = new Uppy<Meta, Record<string, never>>({
    autoProceed: false,
    restrictions: { maxFileSize: MAX_FILE_BYTES, allowedFileTypes: ALLOWED_FILE_TYPES },
    locale: {
      pluralize: (n: number) => (n === 1 ? 0 : 1),
      strings: {
        exceedsSize: "Larger than %{size}",
        youCanOnlyUploadFileTypes: REASONS.UNSUPPORTED_TYPE!,
      },
    },
    // A project holds at most 20 active files; the ones still waiting in this session count too. (Uppy's
    // own maxNumberOfFiles would also count finished uploads, which the documents list already holds.)
    onBeforeFileAdded: (file, files) => {
      const waiting = Object.values(files).filter((f) => !f.progress.uploadComplete).length;
      if (opts.getActive() + waiting < MAX_ACTIVE_FILES) return true;
      overLimit = true; // Uppy reports the refusal through restriction-failed, which says why
      return false;
    },
  });

  // Only the upload token reaches tusd: no `headers`, no credentials, and nothing that adds an
  // Authorization header (the browser never holds the Keycloak token, and tusd must not see it). The
  // endpoint is set per file once the token is issued.
  uppy.use(Tus, {
    endpoint: "",
    retryDelays: [0, 1000, 3000, 5000, 10000],
    limit: 3,
    withCredentials: false,
    removeFingerprintOnSuccess: true,
    allowedMetaFields: ["name", "type", "token", "project_id"],
  });

  uppy.on("restriction-failed", (file, error) => {
    const reason = overLimit ? REASONS.FILE_LIMIT_REACHED! : error.message;
    overLimit = false;
    opts.onRejected({ name: file?.name ?? "File", reason });
  });

  // Ask for a fresh token right before the files go out, so it is never stale: a retry long after the
  // first try asks again. A refused request (4xx) removes the files with its reason; a request that could
  // not be made (5xx, offline) throws, which stops the upload and keeps the files for another try.
  uppy.addPreProcessor(async (fileIds) => {
    const files = fileIds.map((id) => uppy.getFile(id));
    let answer: UploadToken;
    try {
      answer = await opts.announce(announceBody(files));
    } catch (error) {
      const status = (error as ApiError | null)?.statusCode;
      if (status !== undefined && status < 500) {
        const reason = describeProblem(error);
        for (const file of files) {
          opts.onRejected({ name: file.name ?? "", reason });
          uppy.removeFile(file.id);
        }
        return;
      }
      opts.onFailed(describeProblem(error));
      throw error;
    }
    for (const rejection of applyToken(uppy, opts.projectId, files, answer))
      opts.onRejected(rejection);
  });

  // tusd (or the API's pre-create hook) refused or lost an upload: say why, per file.
  uppy.on("upload-error", (file, error, response) => {
    void response;
    opts.onRejected({ name: file?.name ?? "File", reason: describeTusError(error) });
  });

  return uppy;
}
