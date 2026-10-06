import { expect, test } from "vitest";

import { GET } from "./route";

test("e1_9_web_health_route returns ok", async () => {
  const response = GET();
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ status: "ok" });
});
