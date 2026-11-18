import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ChromaticButton } from "./chromatic-button";

// jsdom has no WebGL, ResizeObserver or IntersectionObserver: the button must still work as a plain button there
// (the shader is skipped and the CSS gradient face shows), which is the same path as a browser without WebGL.
class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", NoopObserver);
  vi.stubGlobal("IntersectionObserver", NoopObserver);
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("reduce"),
    media: query,
    addEventListener() {},
    removeEventListener() {},
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("goal send uses the chromatic button (#823)", () => {
  test("823-T01 without WebGL it renders a working button with its label", () => {
    const onClick = vi.fn();
    render(
      <ChromaticButton type="button" className="goal-send" aria-label="Send goal" onClick={onClick}>
        go
      </ChromaticButton>,
    );
    const button = screen.getByRole("button", { name: "Send goal" });
    expect(button.className).toContain("goal-send");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  test("823-T02 disabled: no click goes through", () => {
    const onClick = vi.fn();
    render(
      <ChromaticButton type="button" aria-label="Send goal" disabled onClick={onClick}>
        go
      </ChromaticButton>,
    );
    const button = screen.getByRole("button", { name: "Send goal" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});
