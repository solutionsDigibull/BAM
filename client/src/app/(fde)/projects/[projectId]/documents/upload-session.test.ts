import { describe, expect, test, vi } from "vitest";

import type { UploadToken } from "@/contracts";

import {
  MAX_ACTIVE_FILES,
  MAX_FILE_BYTES,
  REASONS,
  announceBody,
  createUploader,
  describeTusError,
  type Rejection,
} from "./upload-session";

const answer = (over: Partial<UploadToken> = {}): UploadToken => ({
  token: "tok-1",
  tus_endpoint: "https://files.example.test/files/",
  expires_at: "2026-10-15T10:00:00Z",
  accepted: [],
  rejected: [],
  ...over,
});

function setup(announce: (b: unknown) => Promise<UploadToken> = async () => answer(), active = 0) {
  const rejected: Rejection[] = [];
  const failed: string[] = [];
  const uppy = createUploader({
    projectId: "p1",
    getActive: () => active,
    announce: announce as never,
    onRejected: (r) => rejected.push(r),
    onFailed: (m) => failed.push(m),
  });
  const add = (name: string, size = 10, type = "application/pdf") =>
    uppy.addFile({
      name,
      type,
      data: new Blob([new Uint8Array(Math.min(size, 16))]),
      meta: {},
    } as never);
  return { uppy, rejected, failed, add };
}

describe("Documents upload session (E4.10)", () => {
  test("a file over 15 MB is refused at once with its reason", () => {
    const { uppy, rejected } = setup();
    const big = new Blob(["x"]);
    Object.defineProperty(big, "size", { value: MAX_FILE_BYTES + 1 });
    expect(() => uppy.addFile({ name: "big.pdf", type: "application/pdf", data: big })).toThrow();
    expect(rejected).toEqual([{ name: "big.pdf", reason: REASONS.FILE_TOO_LARGE }]);
    expect(uppy.getFiles()).toHaveLength(0);
  });

  test("a file type that is not allowed is refused at once", () => {
    const { uppy, rejected, add } = setup();
    expect(() => add("run.exe", 10, "application/x-msdownload")).toThrow();
    expect(rejected[0]).toEqual({ name: "run.exe", reason: REASONS.UNSUPPORTED_TYPE });
    expect(uppy.getFiles()).toHaveLength(0);
  });

  test("the 20-file limit counts the project's documents and the files waiting", () => {
    const { uppy, rejected, add } = setup(undefined, MAX_ACTIVE_FILES - 1);
    add("one.pdf");
    expect(uppy.getFiles()).toHaveLength(1);
    expect(() => add("two.pdf")).toThrow();
    expect(rejected.at(-1)).toEqual({ name: "two.pdf", reason: REASONS.FILE_LIMIT_REACHED });
  });

  test("the announce body carries each file's name, size and type", () => {
    const { uppy, add } = setup();
    add("a.pdf", 5);
    expect(announceBody(uppy.getFiles()).files).toEqual([
      { name: "a.pdf", size: 5, mime: "application/pdf" },
    ]);
  });

  test("files the server refuses are removed with its reason and never reach tusd", async () => {
    const { uppy, rejected, add } = setup(async () =>
      answer({
        rejected: [
          { name: "a.pdf", code: "FILE_TOO_LARGE" },
          { name: "b.pdf", code: "UNSUPPORTED_TYPE" },
        ],
      }),
    );
    add("a.pdf");
    add("b.pdf");
    await uppy.upload();
    expect(uppy.getFiles()).toHaveLength(0);
    expect(rejected).toEqual([
      { name: "a.pdf", reason: REASONS.FILE_TOO_LARGE },
      { name: "b.pdf", reason: REASONS.UNSUPPORTED_TYPE },
    ]);
  });

  test("a file the server did not list as accepted is dropped, not sent", async () => {
    const { uppy, rejected, add } = setup(async () => answer());
    add("a.pdf");
    await uppy.upload();
    expect(uppy.getFiles()).toHaveLength(0);
    expect(rejected[0]!.reason).toBe(REASONS.NOT_ACCEPTED);
  });

  test("a refused announce (4xx) removes the files with the problem's reason", async () => {
    const { uppy, rejected, add } = setup(async () => {
      throw { statusCode: 409, message: "x", problem: { code: "FILE_LIMIT_REACHED" } };
    });
    add("a.pdf");
    await uppy.upload();
    expect(uppy.getFiles()).toHaveLength(0);
    expect(rejected).toEqual([{ name: "a.pdf", reason: REASONS.FILE_LIMIT_REACHED }]);
  });

  test("an unavailable engine (5xx) keeps the files and asks to retry", async () => {
    const { uppy, rejected, failed, add } = setup(async () => {
      throw { statusCode: 503, message: "down", problem: { detail: "Engine unavailable" } };
    });
    add("a.pdf");
    await uppy.upload().catch(() => undefined);
    expect(uppy.getFiles()).toHaveLength(1);
    expect(failed).toEqual(["Engine unavailable"]);
    expect(rejected).toEqual([]);
  });

  test("a fresh token is requested at every upload start", async () => {
    const announce = vi.fn(async () => answer());
    const { uppy, add } = setup(announce);
    add("a.pdf");
    await uppy.upload();
    add("b.pdf");
    await uppy.upload();
    expect(announce).toHaveBeenCalledTimes(2);
  });

  test("tus is configured without an Authorization header or credentials", () => {
    const { uppy } = setup();
    const tus = uppy.getPlugin("Tus") as unknown as {
      opts: { headers?: unknown; withCredentials: boolean; allowedMetaFields: string[] };
    };
    expect(tus.opts.headers).toBeUndefined();
    expect(tus.opts.withCredentials).toBe(false);
    expect(tus.opts.allowedMetaFields).toEqual(
      expect.arrayContaining(["token", "project_id", "name", "type"]),
    );
  });

  test("tusd's 413 reads as larger than 15 MB; other refusals give the hook's reason", () => {
    const res = (status: number, body = "") => ({
      originalResponse: { getStatus: () => status, getBody: () => body },
    });
    expect(describeTusError(res(413, "ERR_MAX_SIZE_EXCEEDED"))).toBe(REASONS.FILE_TOO_LARGE);
    expect(describeTusError(res(415, JSON.stringify({ code: "UNSUPPORTED_TYPE" })))).toBe(
      REASONS.UNSUPPORTED_TYPE,
    );
    expect(describeTusError(res(500, "boom"))).toBe("Upload failed (500)");
    expect(describeTusError(new Error("network"))).toMatch(/interrupted/);
  });
});
