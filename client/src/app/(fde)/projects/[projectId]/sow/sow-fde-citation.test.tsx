import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { CitationPanel } from "./sow-panels";

describe("FDE-authored citations (E6.3b)", () => {
  test("e6_3b_t09 the panel shows author, date and note and fetches no source", () => {
    // No Refine provider is mounted: a source lookup (useCustom) would throw, so rendering at all
    // proves the panel did not try to fetch one.
    render(
      <CitationPanel
        projectId="p1"
        version={1}
        cite={{
          kind: "fde",
          author: "Asha",
          date: "2026-10-28",
          note: "Call with the client",
          number: 2,
        }}
      />,
    );
    expect(screen.getByTestId("citation-detail-fde")).toBeTruthy();
    expect(screen.getByText("Added by FDE")).toBeTruthy();
    expect(screen.getByText("Asha, 2026-10-28")).toBeTruthy();
    expect(screen.getByText("Call with the client")).toBeTruthy();
  });

  test("e6_3b_t10 an FDE citation without a note shows no note", () => {
    render(
      <CitationPanel
        projectId="p1"
        version={1}
        cite={{ kind: "fde", author: "Asha", date: "2026-10-28", note: null, number: 1 }}
      />,
    );
    expect(screen.getByText("Asha, 2026-10-28")).toBeTruthy();
    expect(screen.queryByText("Call with the client")).toBeNull();
  });
});
