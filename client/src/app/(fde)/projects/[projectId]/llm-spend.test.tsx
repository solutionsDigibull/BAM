import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import type { LlmUsage } from "@/contracts";

import { LlmSpend, stageName, usagePath } from "./llm-spend";

const ID = "0190f3a2-7b1c-7d4e-8f00-00000000a001";
const GET = `GET /projects/${ID}/llm-usage`;

const usage = (over: Partial<LlmUsage> = {}): LlmUsage => ({
  total_usd: "0.2154",
  calls: 9,
  tokens_in: 79710,
  tokens_out: 1614,
  stages: [
    {
      alias: "sbam-sow",
      calls: 1,
      failed: 0,
      tokens_in: 79710,
      tokens_out: 1614,
      cost_usd: "0.2154",
    },
    {
      alias: "sbam-knowledge",
      calls: 8,
      failed: 2,
      tokens_in: 1,
      tokens_out: 1,
      cost_usd: "0.0000",
    },
  ],
  ...over,
});

describe("LLM spend", () => {
  test("shows the total and each stage by name, failures highlighted", async () => {
    fakeApi({ [GET]: { body: usage() } });
    renderWithRefine(<LlmSpend projectId={ID} />);
    expect(await screen.findByText("LLM spend")).toBeDefined();
    expect(screen.getByText("SOW")).toBeDefined();
    expect(screen.getByText("Knowledge graph")).toBeDefined();
    expect(screen.getByText("$0.2154", { selector: "td" })).toBeDefined();
    expect(screen.getByText("79.7k / 1.6k")).toBeDefined();
    expect(screen.getByText("not reported")).toBeDefined(); // tokens used, no price: not "free"
  });

  test("renders nothing before any model call, and nothing when the request fails", async () => {
    fakeApi({ [GET]: { body: usage({ calls: 0, stages: [], total_usd: "0.0000" }) } });
    const first = renderWithRefine(<LlmSpend projectId={ID} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText("LLM spend")).toBeNull();
    first.unmount();
    fakeApi({ [GET]: problem(500, "INTERNAL_ERROR") });
    renderWithRefine(<LlmSpend projectId={ID} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText("LLM spend")).toBeNull();
  });

  test("the path is relative like every other API path (a leading slash 404s on the web server)", () => {
    expect(usagePath(ID)).toBe(`projects/${ID}/llm-usage`);
  });

  test("an unknown alias is shown as it is, never hidden", () => {
    expect(stageName("sbam-future")).toBe("sbam-future");
  });
});
