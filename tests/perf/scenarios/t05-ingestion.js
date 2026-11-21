// E15.4-T05 (#138): ingestion of 20 × 15 MB files, uploaded concurrently through the real path.
// Expected: every upload accepted and every document PARSED (PLAN §28, #138 T05 "All PARSED").
//
// The path is the portal's (client/src/app/(fde)/projects/[projectId]/documents/upload-session.ts):
//   1. announce:  POST /projects/{p}/uploads {files:[{name,size,mime}]}  server/api/sbam_api/ingest/router.py:78
//                 -> UploadToken {token, tus_endpoint, accepted, rejected}  server/contracts/sbam_contracts/documents.py:124
//   2. tus create: POST {tus_endpoint}, Upload-Length + Upload-Metadata (filename, name, filetype, type,
//                 token, project_id; the pre-create hook reads `filename`, `token`, `project_id`:
//                 server/api/sbam_api/ingest/service.py:233,251,260). No Authorization header: tusd
//                 must never see the Keycloak token (upload-session.ts:218-220).
//   3. tus PATCH: the whole 15 MB body at offset 0.
//   4. poll GET /projects/{p}/documents (ingest/router.py:103) until all 20 are PARSED, then
//      GET /projects/{p}/knowledge (knowledge/router.py:97) until the newest version is READY.
//
// PROJECT_ID must be an EMPTY synthetic project: the server allows 20 active files per project
// (MAX_ACTIVE_FILES, upload-session.ts:19), so setup() aborts if the project already has documents.
// TUS_URL overrides the tus endpoint the API hands out (the API's TUSD_PUBLIC_URL may be a browser URL).
// Each file has unique content: identical files are linked, not stored twice (DUPLICATE).
//
// NOT encoded: "SOW ≤ 5 min after knowledge READY". The 2026-11-18 update says the SOW timing
// predates the workflow pivot; the run records `knowledge_ready_s` for the capacity notes only.
import http from "k6/http";
import { check, sleep } from "k6";
import encoding from "k6/encoding";
import exec from "k6/execution";
import { Rate, Trend } from "k6/metrics";

import { API, PROJECT_ID, apiGet, guard, serverErrors, token } from "../lib/config.js";
import { TARGETS, commonThresholds } from "../lib/targets.js";

guard(["PROJECT_ID"]);

const FILES = Number(__ENV.FILES || TARGETS.ingestFiles);
const BYTES = Number(__ENV.FILE_BYTES || TARGETS.ingestFileBytes);
const RUN = __ENV.RUN_ID || `${Date.now()}`;
const uploaded = new Rate("ingest_upload_ok");
const parsed = new Rate("ingest_documents_parsed");
const parsedAfter = new Trend("ingest_all_parsed_s");
const readyAfter = new Trend("knowledge_ready_s");

export const options = {
  scenarios: {
    upload: { executor: "per-vu-iterations", vus: FILES, iterations: 1, maxDuration: "20m" },
    settle: {
      executor: "per-vu-iterations",
      vus: 1,
      iterations: 1,
      startTime: __ENV.SETTLE_START || "30s",
      maxDuration: `${TARGETS.ingestSettleSeconds + 120}s`,
    },
  },
  thresholds: Object.assign(commonThresholds(), {
    ingest_upload_ok: ["rate==1"],
    ingest_documents_parsed: ["rate==1"],
  }),
};

const nameOf = (i) => `perf-${RUN}-${String(i).padStart(2, "0")}.txt`;

export function setup() {
  const res = apiGet(`/projects/${PROJECT_ID}/documents`, "/projects/{project_id}/documents");
  if (res.status !== 200) exec.test.abort(`documents list answered ${res.status}`);
  if (res.json().items.length > 0) exec.test.abort("PROJECT_ID must be an empty synthetic project (20-file limit)");
  return { started: Date.now() };
}

/** Synthetic text, unique per file (first line), padded to exactly BYTES. */
function body(i) {
  const head = `synthetic perf file ${i} run ${RUN} ${Math.random()}\n`;
  const line = "Synthetic SBAM performance-test content. No customer data. ".repeat(16) + "\n";
  const reps = Math.ceil((BYTES - head.length) / line.length);
  return (head + line.repeat(reps)).slice(0, BYTES);
}

function upload(i) {
  const name = nameOf(i);
  const announce = http.post(
    `${API}/projects/${PROJECT_ID}/uploads`,
    JSON.stringify({ files: [{ name, size: BYTES, mime: "text/plain" }] }),
    {
      headers: { Authorization: `Bearer ${token()}`, "Content-Type": "application/json" },
      tags: { name: "POST /projects/{project_id}/uploads" },
    },
  );
  if (announce.status >= 500) serverErrors.add(1);
  if (!check(announce, { "announce 201": (r) => r.status === 201 })) return false;
  const t = announce.json();
  if (!check(t, { "file accepted": (x) => x.accepted.length === 1 })) return false;

  const b64 = (s) => encoding.b64encode(s);
  const meta = { filename: name, name, filetype: "text/plain", type: "text/plain", token: t.token, project_id: PROJECT_ID };
  const tus = __ENV.TUS_URL || t.tus_endpoint;
  const create = http.post(tus, null, {
    headers: {
      "Tus-Resumable": "1.0.0",
      "Upload-Length": String(BYTES),
      "Upload-Metadata": Object.entries(meta).map(([k, v]) => `${k} ${b64(v)}`).join(","),
    },
    tags: { name: "tus create" },
  });
  if (!check(create, { "tus create 201": (r) => r.status === 201 })) return false;
  const loc = create.headers.Location || create.headers.location;
  const url = /^https?:/.test(loc) ? loc : /^https?:\/\/[^/]+/.exec(tus)[0] + loc; // k6 has no global URL
  const patch = http.patch(url, body(i), {
    headers: { "Tus-Resumable": "1.0.0", "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream" },
    tags: { name: "tus patch" },
    timeout: "300s",
  });
  return check(patch, { "tus patch 204": (r) => r.status === 204 });
}

function settle(started) {
  const deadline = Date.now() + TARGETS.ingestSettleSeconds * 1000;
  const mine = new Set(Array.from({ length: FILES }, (_, i) => nameOf(i)));
  let docs = [];
  while (Date.now() < deadline) {
    const res = apiGet(`/projects/${PROJECT_ID}/documents`, "/projects/{project_id}/documents");
    docs = res.status === 200 ? res.json().items.filter((d) => mine.has(d.title)) : [];
    const done = docs.filter((d) => ["PARSED", "PARSE_FAILED", "REJECTED", "SCAN_FAILED"].includes(d.state));
    if (docs.length === FILES && done.length === FILES) break;
    sleep(5);
  }
  for (const name of mine) parsed.add(docs.some((d) => d.title === name && d.state === "PARSED"));
  parsedAfter.add((Date.now() - started) / 1000);

  while (Date.now() < deadline) {
    const res = apiGet(`/projects/${PROJECT_ID}/knowledge`, "/projects/{project_id}/knowledge");
    const newest = res.status === 200 ? res.json().versions[0] : null;
    if (newest && newest.status === "READY") return readyAfter.add((Date.now() - started) / 1000);
    if (newest && newest.status === "FAILED") return console.error(`knowledge version FAILED: ${newest.error_reason}`);
    sleep(10);
  }
  console.error("knowledge never READY before the deadline");
}

export default function (data) {
  if (exec.scenario.name === "upload") uploaded.add(upload(exec.vu.idInTest - 1));
  else settle(data.started);
}
