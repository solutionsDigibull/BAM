import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import Home from "./page";

test("e1_1_t03 home page renders", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "SBAM" })).toBeDefined();
});
