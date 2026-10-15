import { screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  fakeApi,
  problem,
  project,
  renderWithRefine,
} from "@/app/(admin)/admin/projects/test-harness";
import type { DocumentOut } from "@/contracts";

import { EMPTY_DOCUMENTS } from "../../workspace-api";
import { DocumentsScreen, countActive } from "./documents-screen";

vi.mock("next/dynamic", () => ({ default: () => () => <div data-testid="upload-panel" /> }));

const ID = project().id;
const DOCS = `GET /projects/${ID}/documents`;

afterEach(() => vi.unstubAllGlobals());

const doc = (over: Partial<DocumentOut> = {}): DocumentOut => ({
  id: "d1",
  title: "rfq.pdf",
  state: "PARSED",
  size: 2048,
  mime: "application/pdf",
  sha256: "x",
  created_at: "2026-10-15T10:00:00Z",
  ...over,
});

describe("Documents screen (E4.10)", () => {
  test("an empty project says no documents and still offers the upload panel", async () => {
    fakeApi({ [DOCS]: { body: { items: [] } } });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText(EMPTY_DOCUMENTS)).toBeDefined();
    expect(screen.getByTestId("upload-panel")).toBeDefined();
  });

  test("documents show name, size and state; deleted ones are hidden", async () => {
    fakeApi({
      [DOCS]: {
        body: {
          items: [doc(), doc({ id: "d2", title: "old.pdf", deleted_at: "2026-10-14T10:00:00Z" })],
        },
      },
    });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText("rfq.pdf")).toBeDefined();
    expect(screen.getByText("2 KB")).toBeDefined();
    expect(screen.queryByText("old.pdf")).toBeNull();
  });

  test("a failed request shows the problem detail with a retry", async () => {
    fakeApi({ [DOCS]: problem(503, "ORCHESTRATOR_UNAVAILABLE", "Try again shortly") });
    renderWithRefine(<DocumentsScreen projectId={ID} />);
    expect(await screen.findByText("Try again shortly")).toBeDefined();
    expect(screen.getByRole("button", { name: "Retry" })).toBeDefined();
  });

  test("rejected, deleted and purged documents do not count toward the 20-file limit", () => {
    expect(
      countActive([
        doc({ id: "1" }),
        doc({ id: "2", state: "REJECTED" }),
        doc({ id: "3", state: "DELETED" }),
        doc({ id: "4", state: "PURGED" }),
        doc({ id: "5", deleted_at: "2026-10-14T10:00:00Z" }),
        doc({ id: "6", state: "SCAN_FAILED" }),
      ]),
    ).toBe(2);
  });
});
