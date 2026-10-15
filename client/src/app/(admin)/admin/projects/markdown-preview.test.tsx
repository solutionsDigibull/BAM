import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MarkdownPreview } from "./markdown-preview";

describe("context notes Markdown preview (E3.9)", () => {
  test("e3_9_t06 <script> is rendered as text and never executes", () => {
    const { container } = render(<MarkdownPreview source="<script>alert(1)</script>" />);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByTestId("markdown-preview").textContent).toContain(
      "<script>alert(1)</script>",
    );
  });

  test("e3_9_t06 inline HTML, event handlers and javascript: links are neutralised", () => {
    const { container } = render(
      <MarkdownPreview
        source={
          'Hi <img src=x onerror="alert(1)"> [click](javascript:alert(1)) ![t](https://x/t.png)'
        }
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    const link = screen.getByText("click").closest("a")!;
    expect(link.getAttribute("href") ?? "").not.toMatch(/javascript:/i);
    expect(screen.getByText("[image: t]")).toBeDefined();
  });

  test("renders Markdown structure and safe links", () => {
    render(
      <MarkdownPreview source={"## Domain\n\n- **EMS** RFQ\n\n[docs](https://example.com)"} />,
    );
    expect(screen.getByRole("heading", { name: "Domain" })).toBeDefined();
    expect(screen.getByText("EMS").tagName).toBe("STRONG");
    const link = screen.getByRole("link", { name: "docs" });
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  test("empty notes show a hint", () => {
    render(<MarkdownPreview source="  " />);
    expect(screen.getByText("Nothing to preview yet.")).toBeDefined();
  });
});
