import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { ParseInfo, Snapshot, UploadRecord } from "../api/types";

// The review screen's waiting state animates text with WebGL-free but timer-driven effects; a plain stub keeps the test about data.
vi.mock("./ReviewWaiting", () => ({ ReviewWaiting: () => <p>Waiting for files</p> }));

import { IngestionReview } from "./Ingestion";
import { StandardizeFooter, StandardizeView } from "./Standardize";

const parse = (over: Partial<ParseInfo> = {}): ParseInfo => ({ state: "PARSED", pages: 0, sheets: 0, tables: 0, warnings: [], ocrPages: 0, unreadablePages: 0, ...over });
const file = (id: string, name: string, p?: ParseInfo): UploadRecord =>
  ({ id, name, size: 2048, status: "stored", scan: "verified", released: true, uploadedAt: "2026-11-09T10:00:00Z", objectKey: "", parts: 1, batchId: "project", parse: p }) as UploadRecord;
const snapOf = (uploads: UploadRecord[], ingestion?: { fileIds: string[] }) =>
  ({ uploads, ingestion: ingestion && { runId: "r1", ...ingestion }, sowGeneration: [], persist: {}, steps: [{ editable: true }] }) as unknown as Snapshot;

afterEach(cleanup);

const withClient = (ui: ReactElement) => <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;

describe("Standardize shows the backend's verdict on each accepted file (#711)", () => {
  test("test_711_each_file_shows_its_verdict_reason_and_facts", () => {
    render(
      <StandardizeView
        snap={snapOf([
          file("1", "rfq.pdf", parse({ pages: 12, tables: 3 })),
          file("2", "locked.pdf", parse({ state: "PARSE_FAILED", reason: "The file is password protected" })),
          file("3", "scan.pdf", parse({ unreadablePages: 2, pages: 5 })),
        ])}
      />,
    );
    expect(screen.getByText("Read successfully")).toBeTruthy();
    expect(screen.getByText("The file is password protected")).toBeTruthy();
    expect(screen.getByText("2 pages could not be read")).toBeTruthy();
    expect(screen.getByText(/12 pages · 3 tables/)).toBeTruthy();
    expect(screen.getByLabelText("Standardize summary").textContent).toMatch(/Ready\s*1/);
    expect(screen.getByLabelText("Standardize summary").textContent).toMatch(/Cannot be read\s*1/);
    expect(screen.getByText(/stays out of Context/)).toBeTruthy();
  });

  test("test_711_no_browser_side_checks_are_shown_nothing_is_invented", () => {
    render(<StandardizeView snap={snapOf([file("1", "rfq.pdf", parse())])} />);
    expect(screen.queryByText(/Local AI fix/i)).toBeNull();
    expect(screen.queryByText(/Manual review/i)).toBeNull();
    expect(screen.queryByText(/Gerber|Geometry Kernel|XREF/i)).toBeNull();
  });

  test("test_711_an_empty_project_points_back_to_upload", () => {
    render(<StandardizeView snap={snapOf([])} />);
    expect(screen.getByText(/No files yet. Add documents in Upload/)).toBeTruthy();
  });
});

describe("Review is a last look at what goes to Context, from the same verdicts (#711)", () => {
  test("test_711_before_ingestion_starts_review_is_the_waiting_state", () => {
    render(<IngestionReview snap={snapOf([file("1", "rfq.pdf", parse())])} />);
    expect(screen.getByText("Waiting for files")).toBeTruthy();
  });

  test("test_711_after_start_it_lists_handed_on_files_with_their_verdicts_and_the_not_handed_on_ones", () => {
    render(
      <IngestionReview
        snap={snapOf(
          [file("1", "rfq.pdf", parse({ pages: 4 })), file("2", "late.pdf", parse({ pages: 1 })), file("3", "bad.pdf", parse({ state: "PARSE_FAILED", reason: "The file is damaged and cannot be read" }))],
          { fileIds: ["1", "3"] },
        )}
      />,
    );
    expect(screen.getByLabelText("Accepted files").textContent).toContain("rfq.pdf");
    expect(screen.getByLabelText("Accepted files").textContent).toContain("The file is damaged and cannot be read");
    expect(screen.getByLabelText("Accepted files").textContent).not.toContain("late.pdf");
    expect(screen.getByText("Not handed on")).toBeTruthy();
    expect(screen.getByText("Ready: Read successfully · not sent to Context")).toBeTruthy();
    expect(screen.getByLabelText("Review summary").textContent).toMatch(/Cannot be read\s*1/);
  });
});

describe("Standardize's one action is Start ingestion (#711)", () => {
  const startButton = () => screen.getByRole("button", { name: /Start ingestion/ });

  test("test_711_start_is_offered_when_every_accepted_file_was_read", () => {
    render(withClient(<StandardizeFooter snap={snapOf([file("1", "rfq.pdf", parse({ pages: 2 }))])} />));
    expect(screen.getByText("1 file will go to Context")).toBeTruthy();
    expect(startButton().getAttribute("aria-disabled")).not.toBe("true");
  });

  test("test_711_start_waits_while_a_file_is_still_being_read", () => {
    render(withClient(<StandardizeFooter snap={snapOf([file("1", "rfq.pdf", parse({ state: "PARSING" }))])} />));
    expect(screen.getAllByText("Checking files").length).toBeGreaterThan(0);
    expect(startButton().getAttribute("aria-disabled") === "true" || (startButton() as HTMLButtonElement).disabled).toBe(true);
  });

  test("test_711_start_is_refused_when_no_file_could_be_read", () => {
    render(withClient(<StandardizeFooter snap={snapOf([file("1", "bad.pdf", parse({ state: "PARSE_FAILED", reason: "The file is damaged and cannot be read" }))])} />));
    expect(startButton().getAttribute("aria-disabled") === "true" || (startButton() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/1 cannot be read/)).toBeTruthy();
  });

  test("test_711_once_started_the_footer_opens_parse_instead_of_starting_twice", () => {
    render(withClient(<StandardizeFooter snap={snapOf([file("1", "rfq.pdf", parse())], { fileIds: ["1"] })} />));
    expect(screen.getByText("Ingestion has started")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open Parse" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Start ingestion/ })).toBeNull();
  });
});
