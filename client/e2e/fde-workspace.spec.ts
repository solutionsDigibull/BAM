import { expect, test, type Page, type Route } from "@playwright/test";

// FDE workspace (E4.10): My projects, the project overview with Start processing, and the upload screen.
// The API is faked at the proxy (there is no sbam-api here) and tusd is faked on the same origin, so the
// browser really runs Uppy and tus-js-client against it.
const PROJECT = "0190f3a2-7b1c-7d4e-8f00-000000000001";
const API = "**/api/proxy/api/v1";
const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const project = {
  id: PROJECT,
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  name: "EMS RFQ costing (ORG-EMS)",
  client_name: "Illustrative EMS manufacturer",
  description: null,
  context_notes: null,
  status: "ACTIVE",
  pipeline_state: "IDLE",
  row_version: 1,
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};

const signIn = async (page: Page, baseURL: string) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL }]);
  await page.route(`${API}/projects`, (r) => json(r, { items: [project] }));
};

type Tus = { creations: { metadata: string; authorization: string | undefined }[] };

/** A same-origin tusd: creation answers 201 with a Location, PATCH takes the whole body. */
async function fakeTus(page: Page): Promise<Tus> {
  const seen: Tus = { creations: [] };
  const lengths = new Map<string, string>();
  await page.route("**/fake-tus/**", (route) => {
    const req = route.request();
    const h = req.headers();
    const base = { "tus-resumable": "1.0.0", "access-control-allow-origin": "*" };
    if (req.method() === "POST") {
      const id = `u${seen.creations.length}`;
      seen.creations.push({
        metadata: h["upload-metadata"] ?? "",
        authorization: h["authorization"],
      });
      lengths.set(id, h["upload-length"] ?? "0");
      const origin = new URL(req.url()).origin;
      return route.fulfill({
        status: 201,
        headers: { ...base, location: `${origin}/fake-tus/${id}` },
      });
    }
    if (req.method() === "PATCH") {
      const id = req.url().split("/").pop()!;
      return route.fulfill({
        status: 204,
        headers: { ...base, "upload-offset": lengths.get(id) ?? "0" },
      });
    }
    return route.fulfill({ status: 204, headers: base });
  });
  return seen;
}

const metadata = (raw: string): Record<string, string> =>
  Object.fromEntries(
    raw.split(",").map((pair) => {
      const [k, v] = pair.split(" ");
      return [k, Buffer.from(v ?? "", "base64").toString()];
    }),
  );

const pick = (page: Page, files: { name: string; mimeType: string; buffer: Buffer }[]) =>
  page.locator("input.uppy-Dashboard-input[type=file]").first().setInputFiles(files);

const pdf = (name: string, size = 64) => ({
  name,
  mimeType: "application/pdf",
  buffer: Buffer.alloc(size, "a"),
});

async function documentsPage(page: Page, baseURL: string, docs: unknown[] = []) {
  await signIn(page, baseURL);
  let items = docs;
  await page.route(`${API}/projects/${PROJECT}/documents`, (r) => json(r, { items }));
  await page.goto(`/projects/${PROJECT}/documents`);
  await expect(page.getByTestId("upload-dashboard")).toBeVisible();
  return (next: unknown[]) => (items = next);
}

test("e4_10_t01 an accepted file uploads to tusd with the token and no Authorization header", async ({
  page,
  baseURL,
}) => {
  const setDocs = await documentsPage(page, baseURL!);
  const tus = await fakeTus(page);
  const announced: unknown[] = [];
  await page.route(`${API}/projects/${PROJECT}/uploads`, (r) => {
    announced.push(r.request().postDataJSON());
    return json(r, {
      token: "tok-1",
      tus_endpoint: `${new URL(baseURL!).origin}/fake-tus/`,
      expires_at: "2026-10-15T10:00:00Z",
      accepted: [{ name: "rfq.pdf", size: 64, mime: "application/pdf" }],
      rejected: [],
    });
  });
  await pick(page, [pdf("rfq.pdf")]);
  setDocs([
    {
      id: "d1",
      title: "rfq.pdf",
      state: "UPLOADED",
      size: 64,
      mime: "application/pdf",
      sha256: "x",
      created_at: "2026-10-15T10:00:00Z",
    },
  ]);
  await page.getByRole("button", { name: "Upload 1 file" }).click();
  await expect(page.getByRole("cell", { name: "rfq.pdf" })).toBeVisible();
  expect(announced).toEqual([{ files: [{ name: "rfq.pdf", size: 64, mime: "application/pdf" }] }]);
  expect(tus.creations).toHaveLength(1);
  expect(metadata(tus.creations[0]!.metadata)).toMatchObject({
    token: "tok-1",
    filename: "rfq.pdf",
    project_id: PROJECT,
  });
  expect(tus.creations[0]!.authorization).toBeUndefined();
});

test("e4_10_t02 a file over 15 MB is refused with its reason and never announced", async ({
  page,
  baseURL,
}) => {
  await documentsPage(page, baseURL!);
  let announces = 0;
  await page.route(`${API}/projects/${PROJECT}/uploads`, (r) => {
    announces++;
    return json(r, {});
  });
  await pick(page, [pdf("huge.pdf", 15 * 1024 * 1024 + 1)]);
  await expect(page.getByText("huge.pdf").first()).toBeVisible();
  await expect(page.getByText(/Larger than 15 MB/).first()).toBeVisible();
  expect(announces).toBe(0);
});

test("e4_10_t03 an unsupported file type is refused with its reason", async ({ page, baseURL }) => {
  await documentsPage(page, baseURL!);
  await pick(page, [
    { name: "run.exe", mimeType: "application/x-msdownload", buffer: Buffer.from("MZ") },
  ]);
  await expect(page.getByText(/File type not supported/).first()).toBeVisible();
});

test("e4_10_t04 files the server rejects are listed with the reason and not sent to tusd", async ({
  page,
  baseURL,
}) => {
  await documentsPage(page, baseURL!);
  const tus = await fakeTus(page);
  await page.route(`${API}/projects/${PROJECT}/uploads`, (r) =>
    json(r, {
      token: "tok-2",
      tus_endpoint: `${new URL(baseURL!).origin}/fake-tus/`,
      expires_at: "2026-10-15T10:00:00Z",
      accepted: [],
      rejected: [{ name: "notes.pdf", code: "FILE_LIMIT_REACHED" }],
    }),
  );
  await pick(page, [pdf("notes.pdf")]);
  await page.getByRole("button", { name: "Upload 1 file" }).click();
  await expect(page.getByText("Some files were not uploaded")).toBeVisible();
  await expect(page.getByText(/This project already has 20 files/)).toBeVisible();
  expect(tus.creations).toHaveLength(0);
});

test("e4_10_t08 an unavailable engine keeps the file and says to retry", async ({
  page,
  baseURL,
}) => {
  await documentsPage(page, baseURL!);
  await page.route(`${API}/projects/${PROJECT}/uploads`, (r) =>
    json(
      r,
      {
        type: "about:blank",
        title: "x",
        status: 503,
        code: "ORCHESTRATOR_UNAVAILABLE",
        detail: "Engine unavailable",
      },
      503,
    ),
  );
  await pick(page, [pdf("rfq.pdf")]);
  await page.getByRole("button", { name: "Upload 1 file" }).click();
  await expect(page.getByText("Engine unavailable")).toBeVisible();
  await expect(page.getByText("Your files are kept. Retry the upload.")).toBeVisible();
});

test("e4_10_t05 Start processing on the overview posts with an Idempotency-Key", async ({
  page,
  baseURL,
}) => {
  await signIn(page, baseURL!);
  let state = "IDLE";
  const counts = { uploaded: 2, parsed: 0, rejected: 0, failed: 0 };
  await page.route(`${API}/projects/${PROJECT}/pipeline`, (r) =>
    json(r, { state, steps: [], counts }),
  );
  await page.route(`${API}/projects/${PROJECT}/workflows`, (r) => json(r, { items: [] }));
  let key: string | undefined;
  await page.route(`${API}/projects/${PROJECT}/pipeline/start`, (r) => {
    key = r.request().headers()["idempotency-key"];
    state = "INGESTING";
    return json(r, { run: {} }, 202);
  });
  await page.goto(`/projects/${PROJECT}`);
  await page.getByRole("button", { name: "Start processing" }).click();
  await expect(page.getByText("Processing is running.")).toBeVisible();
  expect(key).toMatch(/^[0-9a-f-]{36}$/);
  await expect(page.getByRole("button", { name: /create|new workflow/i })).toHaveCount(0);
});
