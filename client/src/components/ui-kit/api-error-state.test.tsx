import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { ApiErrorState, CONFLICT_MESSAGE, LOCKED_MESSAGE, STALE_MESSAGE } from ".";

describe("ApiErrorState (E1.14)", () => {
  test("e1_14_t04 412 shows the stale-data dialog with a reload option", () => {
    const onReload = vi.fn();
    render(<ApiErrorState error={{ statusCode: 412 }} onReload={onReload} />);
    expect(screen.getByRole("dialog")).toBeDefined();
    expect(screen.getByText(STALE_MESSAGE)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  test("e1_14_t06 423 shows the locked banner", () => {
    render(<ApiErrorState error={{ statusCode: 423, problem: { code: "LOCKED" } }} />);
    expect(screen.getByText(LOCKED_MESSAGE)).toBeDefined();
  });

  test("e1_14_t06 409 shows the conflict reload prompt", () => {
    const onReload = vi.fn();
    render(<ApiErrorState error={{ statusCode: 409 }} onReload={onReload} />);
    expect(screen.getByText(CONFLICT_MESSAGE)).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  test("e1_14_t05 422 lists the field errors", () => {
    render(<ApiErrorState error={{ statusCode: 422, errors: { name: ["required"] } }} />);
    expect(screen.getByText("name: required")).toBeDefined();
  });

  test("503 offers a retry", () => {
    const onRetry = vi.fn();
    render(<ApiErrorState error={{ statusCode: 503 }} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
