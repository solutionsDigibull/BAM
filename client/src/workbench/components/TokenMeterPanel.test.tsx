import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { BudgetState, TokenMeter } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { EVIDENCE_VIEWS, EvidenceBody } from "./BuildEvidence";
import { TokenMeterPanel } from "./TokenMeterPanel";

const BUILD = "build-0190f3a2-1";
const state = (over: Partial<BudgetState> & Pick<BudgetState, "scope">): BudgetState => ({
  used: 0,
  cap: 1000,
  level: "OK",
  raised_once: false,
  build_id: BUILD,
  ...over,
});
const meter = (scopes: BudgetState[]): TokenMeter => ({ scopes, first_alert_pct: 50, second_alert_pct: 80 });
const idle = () =>
  meter([state({ scope: "SUB_SKILL", cap: 500 }), state({ scope: "WORKFLOW" }), state({ scope: "KNOWLEDGE", cap: 2000 })]);
const problem = (statusCode: number, detail?: string) => Object.assign(new Error("failed"), { statusCode, problem: { detail } });

const install = (over: Partial<BamApi>) =>
  setBamApi({ getTokenBudget: async () => idle(), raiseTokenCap: async () => state({ scope: "WORKFLOW" }), ...over } as unknown as BamApi);
const renderPanel = () =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <TokenMeterPanel />
      </Refine>
    </div>,
  );

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the token meter (#776)", () => {
  test("test_wg23_t05_the_meter_shows_used_against_the_cap_and_the_level_per_scope", async () => {
    install({
      getTokenBudget: async () =>
        meter([
          state({ scope: "SUB_SKILL", used: 450, cap: 500, level: "SECOND_ALERT" }),
          state({ scope: "WORKFLOW", used: 500, cap: 1000, level: "FIRST_ALERT" }),
          state({ scope: "KNOWLEDGE", used: 0, cap: 2000, level: "OK" }),
        ]),
    });
    renderPanel();
    const list = await screen.findByRole("list", { name: "Token usage by scope" });
    const rows = Array.from(list.querySelectorAll("li[data-scope]"));
    expect(rows.map(r => r.getAttribute("data-level"))).toEqual(["SECOND_ALERT", "FIRST_ALERT", "OK"]);
    expect(rows[0]!.textContent).toContain("450 of 500 tokens");
    expect(rows[0]!.textContent).toContain("Nearly used up");
    expect(rows[1]!.textContent).toContain("Half used");
    const bar = within(rows[0] as HTMLElement).getByRole("meter");
    expect(bar.getAttribute("aria-valuenow")).toBe("90");
    expect(screen.getByText(/Alerts at 50% and 80%/)).toBeTruthy();
  });

  test("test_wg23_t05_a_stopped_scope_is_marked_and_offers_the_one_raise", async () => {
    install({ getTokenBudget: async () => meter([state({ scope: "WORKFLOW", used: 1200, level: "STOPPED" })]) });
    renderPanel();
    expect((await screen.findByText("Stopped at the limit")).closest("li")!.getAttribute("data-level")).toBe("STOPPED");
    expect(screen.getByRole("form", { name: "Raise the Workflow build limit" })).toBeTruthy();
  });

  test("test_wg23_loading_state", () => {
    install({ getTokenBudget: () => new Promise(() => undefined) });
    renderPanel();
    expect(screen.getByRole("status", { name: "Loading the token budget" })).toBeTruthy();
  });

  test("test_wg23_empty_state_nothing_counted_yet", async () => {
    install({});
    renderPanel();
    expect(await screen.findByText(/No model calls are counted yet/)).toBeTruthy();
  });

  test("test_wg23_error_state_shows_the_problem_detail_and_retries", async () => {
    const read = vi.fn().mockRejectedValueOnce(problem(500, "The database is down")).mockResolvedValue(idle());
    install({ getTokenBudget: read });
    renderPanel();
    expect(await screen.findByText("The database is down")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("list", { name: "Token usage by scope" })).toBeTruthy();
  });

  test("test_wg23_forbidden_state", async () => {
    install({ getTokenBudget: async () => { throw problem(403); } });
    renderPanel();
    expect(await screen.findByText(/You can't see this project's token budget/)).toBeTruthy();
    cleanup();
    install({ getTokenBudget: async () => { throw problem(404); } });
    renderPanel();
    expect(await screen.findByText(/You can't see this project's token budget/)).toBeTruthy();
  });
});

describe("raise a cap once per build (#776)", () => {
  test("test_wg23_t02_the_raise_sends_the_scope_the_build_and_the_new_cap", async () => {
    const stopped = meter([state({ scope: "WORKFLOW", used: 1200, level: "STOPPED" })]);
    const raised = meter([state({ scope: "WORKFLOW", used: 1200, cap: 5000, level: "OK", raised_once: true })]);
    const read = vi.fn().mockResolvedValueOnce(stopped).mockResolvedValue(raised);
    const raise = vi.fn(async () => state({ scope: "WORKFLOW", used: 1200, cap: 5000, raised_once: true }));
    install({ getTokenBudget: read, raiseTokenCap: raise });
    renderPanel();
    const form = await screen.findByRole("form", { name: "Raise the Workflow build limit" });
    const input = within(form).getByLabelText(/New limit/) as HTMLInputElement;
    expect(input.value).toBe("2000");
    fireEvent.change(input, { target: { value: "5000" } });
    fireEvent.click(within(form).getByRole("button", { name: "Raise the limit" }));
    await waitFor(() => expect(raise).toHaveBeenCalledWith({ scope: "WORKFLOW", build_id: BUILD, tokens: 5000 }));
    expect(await screen.findByText(/Press Retry on the stopped Sub-Skill/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("form", { name: /Raise the/ })).toBeNull());
  });

  test("test_wg23_a_new_cap_must_be_a_whole_number_above_the_cap", async () => {
    const raise = vi.fn();
    install({ getTokenBudget: async () => meter([state({ scope: "WORKFLOW", used: 1200, level: "STOPPED" })]), raiseTokenCap: raise });
    renderPanel();
    const form = await screen.findByRole("form", { name: "Raise the Workflow build limit" });
    fireEvent.change(within(form).getByLabelText(/New limit/), { target: { value: "900" } });
    expect((within(form).getByRole("button", { name: "Raise the limit" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(form).getByText(/Use a whole number above 1,000/)).toBeTruthy();
    expect(raise).not.toHaveBeenCalled();
  });

  test("test_wg23_t03_a_second_raise_is_not_offered_and_a_refusal_shows_the_server_reason", async () => {
    install({ getTokenBudget: async () => meter([state({ scope: "WORKFLOW", used: 1200, level: "STOPPED", raised_once: true })]) });
    renderPanel();
    expect(await screen.findByText(/raised once already and cannot be raised again/)).toBeTruthy();
    expect(screen.queryByRole("form", { name: /Raise the/ })).toBeNull();
    cleanup();
    const refuse = vi.fn().mockRejectedValue(problem(409, "This build's cap was raised once already; it cannot be raised again."));
    install({ getTokenBudget: async () => meter([state({ scope: "WORKFLOW", used: 1200, level: "STOPPED" })]), raiseTokenCap: refuse });
    renderPanel();
    const form = await screen.findByRole("form", { name: "Raise the Workflow build limit" });
    fireEvent.click(within(form).getByRole("button", { name: "Raise the limit" }));
    expect((await within(form).findByRole("alert")).textContent).toContain("cannot be raised again");
  });
});

describe("where the meter sits (#776)", () => {
  test("test_wg23_the_meter_is_a_translate_view_next_to_the_other_evidence", async () => {
    expect(EVIDENCE_VIEWS.map(e => e.id)).toContain("budget");
    install({});
    render(
      <div className="bam-wb">
        <Refine dataProvider={dataProvider} options={{ disableTelemetry: true }}>
          <EvidenceBody view="budget" workflowId="w1" />
        </Refine>
      </div>,
    );
    expect(await screen.findByRole("region", { name: "Token budget" })).toBeTruthy();
  });
});
