import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine } from "@refinedev/core";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { KnowledgeNodeDetail } from "@/contracts";

import { type BamApi, dataProvider, setBamApi } from "../api/adapter";
import { locationLabel } from "../lib/knowledgeNode";
import { NodeSources } from "./NodeSources";

const detail = (passages: KnowledgeNodeDetail["source_passages"]): KnowledgeNodeDetail =>
  ({ knowledge_version_id: "kv1", status: "READY", node: { id: "n1", type: "Customer", name: "Acme" }, neighbours: [], source_passages: passages }) as KnowledgeNodeDetail;
const passage = (over = {}) => ({ document_version_id: "dv1", title: "rfq.pdf", location: { page: 4 }, text: "Acme asked for 500 units.", ...over });

const install = (get: BamApi["getKnowledgeNode"]) => setBamApi({ getKnowledgeNode: get } as unknown as BamApi);
const view = () =>
  render(
    <div className="bam-wb">
      <Refine dataProvider={dataProvider} options={{ disableTelemetry: true, reactQuery: { clientConfig: { defaultOptions: { queries: { retry: false } } } } }}>
        <NodeSources versionId="kv1" nodeId="n1" />
      </Refine>
    </div>,
  );
const fail = (statusCode: number, detail?: string) => Promise.reject(Object.assign(new Error("failed"), { statusCode, problem: detail ? { detail } : undefined }));

afterEach(() => {
  cleanup();
  setBamApi(null);
});

describe("the passages a graph entity was read from, read only (#712)", () => {
  test("test_712_sources_show_title_location_and_text", async () => {
    install(async () => detail([passage(), passage({ title: "costs.xlsx", location: { sheet: "Costs", cell: "B7" }, text: "Unit price 4.20", truncated: true })]));
    view();
    const list = await screen.findByRole("list", { name: "Source passages" });
    expect(list.textContent).toContain("rfq.pdf");
    expect(list.textContent).toContain("page 4");
    expect(list.textContent).toContain("sheet Costs, cell B7");
    expect(list.textContent).toContain("Unit price 4.20…");
  });

  test("test_712_loading_then_empty_when_no_passage_is_recorded", async () => {
    install(() => new Promise((r) => setTimeout(() => r(detail([])), 20)));
    view();
    expect(screen.getByText(/Loading the sources/)).toBeTruthy();
    expect(await screen.findByText(/No source passage is recorded/)).toBeTruthy();
  });

  test("test_712_error_shows_the_servers_detail_and_retry_asks_again", async () => {
    const get = vi.fn().mockImplementationOnce(() => fail(503, "The graph store is unavailable")).mockResolvedValue(detail([passage()]));
    install(get);
    view();
    expect((await screen.findByRole("alert")).textContent).toContain("The graph store is unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("list", { name: "Source passages" })).toBeTruthy());
    expect(get).toHaveBeenCalledTimes(2);
  });

  test("test_712_forbidden_and_gone_are_said_in_words", async () => {
    install(() => fail(403));
    view();
    expect((await screen.findByRole("alert")).textContent).toContain("not assigned");
    cleanup();
    install(() => fail(404));
    view();
    expect(await screen.findByText(/not in the knowledge version any more/)).toBeTruthy();
  });

  test("test_712_location_label_names_page_sheet_cell_line_or_the_whole_document", () => {
    expect(locationLabel({ page: 2 })).toBe("page 2");
    expect(locationLabel({ line: 12 })).toBe("line 12");
    expect(locationLabel({ scope: "document" })).toBe("whole document");
  });
});
