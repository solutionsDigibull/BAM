import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { RejectionList } from "./rejection-list";
import { IN_BIN, REASONS, type Rejection } from "./upload-session";

const plain: Rejection = { name: "big.pdf", reason: REASONS.FILE_TOO_LARGE! };
const binned: Rejection = {
  name: "quote.pdf",
  reason: IN_BIN,
  restore: { id: "bin-1", title: "old-quote.pdf" },
};

describe("Documents upload: refused files (E4.10, E4.4)", () => {
  test("nothing is shown when no file was refused", () => {
    const { container } = render(
      <RejectionList rejections={[]} restoring={false} onRestore={() => {}} />,
    );
    expect(container.textContent).toBe("");
  });

  test("each refused file is listed with its reason and no restore button", () => {
    render(<RejectionList rejections={[plain]} restoring={false} onRestore={() => {}} />);
    expect(screen.getByText("Some files were not uploaded")).toBeDefined();
    expect(screen.getByText("big.pdf")).toBeDefined();
    expect(screen.queryByRole("button")).toBeNull();
  });

  test("e4_4 a file that is in the recycle bin offers its document to restore", () => {
    const onRestore = vi.fn();
    render(<RejectionList rejections={[plain, binned]} restoring={false} onRestore={onRestore} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Restore old-quote.pdf" }));
    expect(onRestore).toHaveBeenCalledWith(binned);
  });

  test("e4_4 the restore button is disabled while a restore is running", () => {
    render(<RejectionList rejections={[binned]} restoring onRestore={() => {}} />);
    expect(
      (screen.getByRole("button", { name: "Restore old-quote.pdf" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
