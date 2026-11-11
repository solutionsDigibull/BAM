import { screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import type { Preflight } from "@/contracts";

import { PREFLIGHT_PATH, PreflightBanner } from "./preflight-banner";

const GET = "GET /preflight";

const report = (checks: Preflight["checks"], status: Preflight["status"]): Preflight => ({
  status,
  checks,
  checked_at: "2026-11-07T10:00:00+00:00",
});

describe("PreflightBanner", () => {
  test("a refused LLM key is named with the fix before anything is built", async () => {
    fakeApi({
      [GET]: {
        body: report(
          [
            { id: "db", name: "Database", status: "ok" },
            {
              id: "embeddings",
              name: "Embeddings (knowledge search)",
              status: "down",
              reason: "credentials_refused",
              fix: "Ask an admin to check OPENROUTER_API_KEY.",
            },
          ],
          "down",
        ),
      },
    });
    renderWithRefine(<PreflightBanner />);
    const banner = await screen.findByTestId("preflight");
    expect(banner.textContent).toContain("Builds will fail until this is fixed");
    expect(banner.textContent).toContain("Embeddings (knowledge search)");
    expect(banner.textContent).toContain("OPENROUTER_API_KEY");
    expect(banner.textContent).not.toContain("Database"); // only what is wrong
  });

  test("the mock LLM is labelled, not hidden", async () => {
    fakeApi({
      [GET]: {
        body: report(
          [
            {
              id: "chat_model",
              name: "Language model",
              status: "mock",
              fix: "Set LLM_PROVIDER=bifrost.",
            },
          ],
          "mock",
        ),
      },
    });
    renderWithRefine(<PreflightBanner />);
    expect((await screen.findByTestId("preflight")).textContent).toContain("results are synthetic");
  });

  test("silent when everything works, and when the check itself cannot be loaded", async () => {
    fakeApi({ [GET]: { body: report([{ id: "db", name: "Database", status: "ok" }], "ok") } });
    const view = renderWithRefine(<PreflightBanner />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("preflight")).toBeNull();
    view.unmount();
    fakeApi({ [GET]: problem(500, "INTERNAL_ERROR") });
    renderWithRefine(<PreflightBanner />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("preflight")).toBeNull();
  });

  test("the path is relative like every other API path", () => {
    expect(PREFLIGHT_PATH).toBe("preflight");
  });
});
