import { describe, expect, test, vi } from "vitest";

import type { DocumentOut, UploadToken } from "@/contracts";

import {
  MAX_ACTIVE_FILES,
  MAX_FILE_BYTES,
  IN_BIN,
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

function setup(
  announce: (b: unknown) => Promise<UploadToken> = async () => answer(),
  active = 0,
  documents: DocumentOut[] = [],
) {
  const rejected: Rejection[] = [];
  const failed: string[] = [];
  const uppy = createUploader({
    projectId: "p1",
    getActive: () => active,
    getDocuments: () => documents,
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

// sha256("abc"): a known vector, so the test does not depend on the code that hashes.
const ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

const binned = (over: Partial<DocumentOut> = {}): DocumentOut => ({
  id: "bin-1",
  title: "old-quote.pdf",
  state: "DELETED",
  size: 3,
  mime: "application/pdf",
  sha256: ABC,
  created_at: "2026-10-01T10:00:00Z",
  deleted_at: "2026-10-10T10:00:00Z",
  purge_after: "2027-01-08T10:00:00Z",
  ...over,
});

const addAbc = (uppy: ReturnType<typeof setup>["uppy"], name = "quote.pdf") =>
  uppy.addFile({ name, type: "application/pdf", data: new Blob(["abc"]), meta: {} } as never);

describe("Documents upload: a file that is in the recycle bin (E4.4)", () => {
  test("e4_4 it is not uploaded; the FDE is offered the document to restore", async () => {
    const announce = vi.fn(async () => answer());
    const { uppy, rejected } = setup(announce, 0, [binned()]);
    addAbc(uppy);
    await uppy.upload();
    expect(announce).not.toHaveBeenCalled();
    expect(uppy.getFiles()).toHaveLength(0);
    expect(rejected).toEqual([
      { name: "quote.pdf", reason: IN_BIN, restore: { id: "bin-1", title: "old-quote.pdf" } },
    ]);
  });

  test("e4_4 the other files of the batch still go on", async () => {
    const announce = vi.fn(async (body: unknown) => (void body, answer()));
    const { uppy, rejected, add } = setup(announce, 0, [binned()]);
    addAbc(uppy);
    add("new.pdf", 5);
    await uppy.upload();
    expect(announce).toHaveBeenCalledTimes(1);
    expect(announce.mock.calls[0]![0]).toMatchObject({ files: [{ name: "new.pdf" }] });
    expect(rejected.filter((r) => r.reason === IN_BIN).map((r) => r.name)).toEqual(["quote.pdf"]);
  });

  test("e4_4 a file that matches no deleted document is uploaded as usual", async () => {
    const other = binned({ sha256: "0".repeat(64) });
    const announce = vi.fn(async () => answer());
    const { uppy, rejected } = setup(announce, 0, [other]);
    addAbc(uppy);
    await uppy.upload();
    expect(announce).toHaveBeenCalledTimes(1); // the file went on to the server
    expect(rejected.map((r) => r.reason)).not.toContain(IN_BIN);
  });

  test("e4_4 a live copy wins: the server links the upload to it, there is nothing to restore", async () => {
    const live = binned({ id: "live-1", state: "PARSED", deleted_at: undefined });
    const announce = vi.fn(async () => answer());
    const { uppy, rejected } = setup(announce, 0, [binned(), live]);
    addAbc(uppy);
    await uppy.upload();
    expect(announce).toHaveBeenCalledTimes(1); // the file went on to the server
    expect(rejected.map((r) => r.reason)).not.toContain(IN_BIN);
  });

  test("e4_4 a purged document cannot be restored, so the file is uploaded", async () => {
    const announce = vi.fn(async () => answer());
    const { uppy, rejected } = setup(announce, 0, [binned({ state: "PURGED" })]);
    addAbc(uppy);
    await uppy.upload();
    expect(announce).toHaveBeenCalledTimes(1); // the file went on to the server
    expect(rejected.map((r) => r.reason)).not.toContain(IN_BIN);
  });

  test("e4_4 with nothing in the bin the file is not read at all", async () => {
    const announce = vi.fn(async () => answer());
    const { uppy } = setup(announce, 0, []);
    const blob = new Blob(["abc"]);
    const read = vi.spyOn(blob, "arrayBuffer");
    uppy.addFile({ name: "quote.pdf", type: "application/pdf", data: blob, meta: {} } as never);
    await uppy.upload();
    expect(read).not.toHaveBeenCalled();
  });

  test("e4_4 where Web Crypto is missing the check is skipped and the server decides", async () => {
    vi.stubGlobal("crypto", {});
    const announce = vi.fn(async () => answer());
    const { uppy, rejected } = setup(announce, 0, [binned()]);
    addAbc(uppy);
    await uppy.upload();
    vi.unstubAllGlobals();
    expect(announce).toHaveBeenCalledTimes(1); // the file went on to the server
    expect(rejected.map((r) => r.reason)).not.toContain(IN_BIN);
  });
});
