import { Refine } from "@refinedev/core";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { RuntimeRegistry } from "@/contracts";
import { dataProvider } from "@/providers/data-provider";

import { NOT_ALLOWED, NOT_BUILT_MESSAGE, RegistryView, matches } from "./registry-view";

const DIGEST = "sha256:" + "ab12".repeat(16);

const registry = (over: Partial<RuntimeRegistry> = {}): RuntimeRegistry => ({
  image_digest: DIGEST,
  python_version: "3.12.15",
  generated_at: "2026-10-15T09:00:00Z",
  content_sha256: "sha256:" + "0".repeat(64),
  packages: [
    { name: "anyio", version: "4.6.0", top_level_modules: ["anyio"], allowed: false },
    { name: "pydantic", version: "2.13.0", top_level_modules: ["pydantic"], allowed: true },
    {
      name: "pydantic-core",
      version: "2.33.0",
      top_level_modules: ["pydantic_core"],
      allowed: false,
    },
    { name: "pyjwt", version: "2.10.1", top_level_modules: ["jwt"], allowed: true },
  ],
  ...over,
});

type Reply = { status: number; body: unknown };

function fakeApi(...replies: Reply[]) {
  const calls: { method: string; url: string }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ method: (init.method ?? "GET").toUpperCase(), url: String(input) });
    const reply = replies.length > 1 ? replies.shift()! : replies[0]!;
    return new Response(JSON.stringify(reply.body), {
      status: reply.status,
      headers: {
        "content-type": reply.status >= 400 ? "application/problem+json" : "application/json",
      },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

const problem = (status: number, code: string, detail = code): Reply => ({
  status,
  body: { type: "about:blank", title: code, status, detail, code },
});

function renderView(endpoint: "admin/registry" | "registry" = "admin/registry") {
  return render(
    <App>
      <Refine
        dataProvider={dataProvider}
        options={{
          disableTelemetry: true,
          reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } },
        }}
      >
        <RegistryView endpoint={endpoint} />
      </Refine>
    </App>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Runtime registry view (E3.12)", () => {
  test("e3_12_t01 shows the full image digest with a copy action", async () => {
    fakeApi({ status: 200, body: registry() });
    renderView();
    const digest = await screen.findByTestId("image-digest");
    expect(digest.textContent).toContain(DIGEST); // in full, no truncation
    expect(within(digest.parentElement!).getByRole("button", { name: /copy/i })).toBeDefined();
    expect(screen.getByText("4 of 4 packages")).toBeDefined();
    expect(screen.getByText("2 allowed · 2 dependencies")).toBeDefined();
  });

  test("e3_12_t02 search filters by package or import name", async () => {
    fakeApi({ status: 200, body: registry() });
    renderView();
    const search = await screen.findByLabelText("Search packages");
    fireEvent.change(search, { target: { value: "PYDANTIC" } });
    await waitFor(() => expect(screen.getByText("2 of 4 packages")).toBeDefined());
    expect(screen.queryAllByText("anyio")).toEqual([]);
    fireEvent.change(search, { target: { value: "jwt" } }); // import name of pyjwt
    await waitFor(() => expect(screen.getByText("1 of 4 packages")).toBeDefined());
    expect(screen.getByText("pyjwt")).toBeDefined();
    expect(search.getAttribute("maxlength")).toBe("100");
  });

  test("e3_12_t03 a registry that isn't built yet shows the empty state", async () => {
    fakeApi(problem(404, "NOT_FOUND"));
    renderView();
    expect(await screen.findByText(NOT_BUILT_MESSAGE)).toBeDefined();
  });

  test("e3_12_t05 a disallowed package shows the Not allowed chip", async () => {
    fakeApi({ status: 200, body: registry() });
    renderView();
    // "anyio" is both the package name and its import-name chip: take the row of the first
    const row = (await screen.findAllByText("anyio"))[0]!.closest<HTMLElement>(".ant-table-row")!;
    expect(within(row).getByText(NOT_ALLOWED)).toBeDefined();
    const allowedRow = screen.getByText("pyjwt").closest<HTMLElement>(".ant-table-row")!;
    expect(within(allowedRow).queryByText(NOT_ALLOWED)).toBeNull();
    fireEvent.click(screen.getByText("Not allowed (2)"));
    await waitFor(() => expect(screen.queryByText("pyjwt")).toBeNull());
  });

  test("e3_12_t06 the view only ever issues GET requests", async () => {
    const calls = fakeApi({ status: 200, body: registry() });
    renderView("registry");
    await screen.findByTestId("image-digest");
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls.every((c) => c.method === "GET")).toBe(true);
    expect(calls[0]!.url).toMatch(/\/api\/proxy\/api\/v1\/registry$/);
    expect(screen.queryByRole("button", { name: /edit|delete|add|save/i })).toBeNull();
  });

  test("refresh shows a new digest", async () => {
    const next = "sha256:" + "cd34".repeat(16);
    fakeApi(
      { status: 200, body: registry() },
      { status: 200, body: registry({ image_digest: next }) },
    );
    renderView();
    await screen.findByText(DIGEST);
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    expect(await screen.findByText(next)).toBeDefined();
  });

  test("other errors show the problem detail with a retry", async () => {
    fakeApi(problem(503, "ORCHESTRATOR_UNAVAILABLE", "Database unavailable"));
    renderView();
    expect(await screen.findByText("Database unavailable")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  test("package names render as text, never as HTML", async () => {
    fakeApi({
      status: 200,
      body: registry({
        packages: [{ name: "<b>x</b>", version: "1", top_level_modules: [], allowed: false }],
      }),
    });
    const { container } = renderView();
    expect(await screen.findByText("<b>x</b>")).toBeDefined();
    expect(container.querySelector(".ant-table-row b")).toBeNull();
  });

  test("matches() is case-insensitive on names and import names", () => {
    const p = {
      name: "python-dateutil",
      version: "2",
      top_level_modules: ["dateutil"],
      allowed: true,
    };
    expect(matches(p, "DateUtil")).toBe(true);
    expect(matches(p, "python-")).toBe(true);
    expect(matches(p, "jwt")).toBe(false);
    expect(matches(p, "  ")).toBe(true);
  });
});
