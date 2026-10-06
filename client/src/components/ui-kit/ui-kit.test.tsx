import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { PagePlaceholder } from "@/components/shell/page-placeholder";

import { ErrorPanel, HashChip, LOCKED_MESSAGE, StatusBadge, WORKFLOW_STATES } from ".";

describe("StatusBadge", () => {
  test.each(WORKFLOW_STATES)("e1_13_t04 known state %s has a label and colour", (state) => {
    const { container } = render(<StatusBadge status={state} />);
    const tag = container.querySelector(`[data-status="${state}"]`);
    expect(tag).not.toBeNull();
    expect(tag?.textContent).toMatch(/^[A-Z][\w ]+$/);
    expect(tag?.textContent).not.toBe("UNKNOWN");
  });

  test("e1_13_t04 unknown state renders neutral UNKNOWN", () => {
    render(<StatusBadge status="XYZ" />);
    expect(screen.getByText("UNKNOWN")).toBeDefined();
  });
});

describe("HashChip", () => {
  const value = `sha256:${"ab12cd34".repeat(8)}`;

  test("e1_13_t05 shows the first 8 chars and copies the full value", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<HashChip value={value} />);
    expect(screen.getByText("ab12cd34")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Copy hash" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(value));
  });

  test("e1_13_t05 rejects a malformed hash", () => {
    render(<HashChip value="sha256:not-a-hash" />);
    expect(screen.getByText("invalid hash")).toBeDefined();
  });
});

describe("ErrorPanel", () => {
  test("e1_13_t06 problem+json 423 shows the locked message", () => {
    render(<ErrorPanel problem={{ status: 423, title: "Locked", code: "SOW_LOCKED" }} />);
    expect(screen.getByText(LOCKED_MESSAGE)).toBeDefined();
    expect(LOCKED_MESSAGE).toMatch(/frozen \/ approved — create a new version/);
  });

  test("e1_13_t06 other problems show title and detail", () => {
    render(<ErrorPanel problem={{ status: 409, title: "Conflict", detail: "SOW changed" }} />);
    expect(screen.getByText("Conflict")).toBeDefined();
    expect(screen.getByText("SOW changed")).toBeDefined();
  });
});

describe("XSS", () => {
  test("e1_13_t07 a malicious project name renders as text", () => {
    const name = "<img src=x onerror=alert(1)>";
    const { container } = render(<PagePlaceholder title={name} empty="nothing" />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getAllByText(name).length).toBeGreaterThan(0);
  });
});
