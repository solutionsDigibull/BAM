import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { fakeApi, problem, renderWithRefine } from "@/app/(admin)/admin/projects/test-harness";
import type { KnowledgeDiff } from "@/contracts";

import { KnowledgeDiffCard, diffPath } from "./knowledge-diff";

const P = "0190f3a2-7b1c-7d4e-8f00-00000000a001";
const KV = "0190f3a2-7b1c-7d4e-8f00-00000000c0a1";
const GET = `GET /projects/${P}/knowledge/${KV}/diff`;

const diff = (over: Partial<KnowledgeDiff> = {}): KnowledgeDiff => ({
  knowledge_version_id: KV,
  against_version_id: "0190f3a2-7b1c-7d4e-8f00-00000000c0a0",
  version: 7,
  against_version: 6,
  added: 3,
  removed: 1,
  unchanged: 300,
  retyped: 2,
  relations_added: 12,
  relations_removed: 4,
  added_entities: [{ name: "Harness H1", type: "product", degree: 5 }],
  removed_entities: [{ name: "Lapp", type: "organization", degree: 2 }],
  documents_added: ["price-list.xlsx"],
  documents_removed: [],
  ...over,
});

const render = (version = 7) =>
  renderWithRefine(<KnowledgeDiffCard projectId={P} kvId={KV} version={version} />);

describe("Knowledge diff", () => {
  test("shows the counts, the documents that changed and the names on expand", async () => {
    fakeApi({ [GET]: { body: diff() } });
    render();
    expect(await screen.findByText("Changes since v6")).toBeDefined();
    expect(screen.getByText("Entities added")).toBeDefined();
    expect(screen.getByText("Documents: + price-list.xlsx")).toBeDefined();
    fireEvent.click(screen.getByText("Added entities (3)"));
    expect(await screen.findByText("Harness H1")).toBeDefined();
  });

  test("the path is relative like every other API path", () => {
    expect(diffPath(P, KV)).toBe(`projects/${P}/knowledge/${KV}/diff`);
  });

  test("nothing is shown for the first version, or when there is nothing to compare", async () => {
    const first = fakeApi({ [GET]: { body: diff() } });
    const view = render(1);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/Changes since/)).toBeNull();
    expect(first.calls.length).toBe(0); // no request at all for v1
    view.unmount();
    fakeApi({ [GET]: problem(404, "NOT_FOUND") });
    render();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/Changes since/)).toBeNull();
  });

  test("a long list says how many more there are", async () => {
    const many = Array.from({ length: 100 }, (_, i) => ({ name: `Thing ${i}`, type: "x" }));
    fakeApi({ [GET]: { body: diff({ added: 250, added_entities: many }) } });
    render();
    fireEvent.click(await screen.findByText("Added entities (250)"));
    expect(await screen.findByText("and 210 more")).toBeDefined();
  });
});
