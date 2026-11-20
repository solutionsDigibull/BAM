import { cleanup, render, screen, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, beforeAll, describe, expect, test } from "vitest";

import type { LlmUsage, Preflight } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { SystemSettings } from "./SystemSettings";

const preflight = (checks: Preflight["checks"]) => ({ status: "ok", checks, checked_at: "2026-11-20T08:00:00Z" }) as Preflight;
const usage = (over: Partial<LlmUsage> = {}) =>
  ({
    total_usd: "0.1300", calls: 3, tokens_in: 46405, tokens_out: 1483,
    stages: [{ alias: "sbam-sow", calls: 3, failed: 1, tokens_in: 46405, tokens_out: 1483, cost_usd: "0.1300" }],
    ...over,
  }) as LlmUsage;
const problem = (statusCode: number, detail = "x") => Object.assign(new Error(detail), { statusCode, problem: { detail } });

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
});
const install = (api: Partial<BamApi>) => setBamApi(api as unknown as BamApi);
const wrap = () =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <SystemSettings open onClose={() => {}} />
      </Refine>
    </div>,
  );
afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("System settings (#826): engines and model usage, read only", () => {
  test("test_826_engines_show_each_check_with_its_status_and_fix", async () => {
    install({
      getPreflight: async () => preflight([
        { id: "database", name: "Database", status: "ok" },
        { id: "llm_provider", name: "Model provider", status: "down", reason: "credentials_refused", fix: "Renew the OpenRouter key" },
      ]),
      getLlmUsage: async () => usage(),
    });
    wrap();
    const table = await screen.findByRole("table", { name: "Engines and services" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[2]!.textContent).toContain("Down");
    expect(rows[2]!.textContent).toContain("Renew the OpenRouter key");
  });

  test("test_826_the_mock_model_is_named_as_such", async () => {
    install({ getPreflight: async () => preflight([{ id: "llm_provider", name: "Model provider", status: "mock" }]), getLlmUsage: async () => usage() });
    wrap();
    expect(await screen.findByText(/The mock model is on/)).toBeTruthy();
  });

  test("test_826_model_usage_by_engine_with_the_total", async () => {
    install({ getPreflight: async () => preflight([]), getLlmUsage: async () => usage() });
    wrap();
    const table = await screen.findByRole("table", { name: "Model usage by engine" });
    expect(table.textContent).toContain("SOW");
    expect(table.textContent).toContain("0.1300");
    expect(screen.getByText(/No checks reported/)).toBeTruthy();
  });

  test("test_826_no_calls_yet_and_errors_and_forbidden", async () => {
    install({ getPreflight: async () => { throw problem(500, "preflight unavailable"); }, getLlmUsage: async () => usage({ calls: 0, stages: [] }) });
    wrap();
    expect(await screen.findByText(/No model calls yet/)).toBeTruthy();
    expect(await screen.findByText(/preflight unavailable/)).toBeTruthy();
    cleanup();
    install({ getPreflight: async () => preflight([]), getLlmUsage: async () => { throw problem(403); } });
    wrap();
    expect(await screen.findByText(/You can't see these settings/)).toBeTruthy();
  });

  test("test_826_nothing_is_editable", async () => {
    install({ getPreflight: async () => preflight([{ id: "database", name: "Database", status: "ok" }]), getLlmUsage: async () => usage() });
    wrap();
    await screen.findByRole("table", { name: "Engines and services" });
    const dialog = document.querySelector("dialog")!;
    expect(dialog.querySelectorAll("input, select, textarea")).toHaveLength(0);
    expect(within(dialog).getByText(/Read only/)).toBeTruthy();
  });
});

describe("System settings in the profile menu (#826)", () => {
  test("test_826_no_settings_dialog_is_in_the_page_until_it_is_opened", async () => {
    const { TopBar } = await import("./shell");
    install({ getPreflight: async () => preflight([]), getLlmUsage: async () => usage() });
    render(
      <div className="bam-wb">
        <Refine dataProvider={dataProvider} options={{ disableTelemetry: true }}>
          <TopBar syncedAt={0} offline={false} />
        </Refine>
      </div>,
    );
    expect(document.querySelector("dialog")).toBeNull(); // another dialog's aria-labelledby must not resolve to it
    screen.getByRole("button", { name: "Profile" }).click();
    (await screen.findByText("System settings")).click();
    expect(await screen.findByRole("dialog", { name: "System settings" })).toBeTruthy();
  });
});
