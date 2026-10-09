import { expect, test } from "@playwright/test";

// E1.14 proxy checks against the real Next route (no sbam-api needed: each case stops before upstream).

test("e1_14_t02 no token in localStorage, sessionStorage or JS-readable cookies", async ({
  page,
  baseURL,
}) => {
  await page.context().addCookies([{ name: "sbam_dev_roles", value: "sbam-fde", url: baseURL! }]);
  await page.goto("/projects");
  await page.goto("/login-fde");
  const exposed = await page.evaluate(() =>
    JSON.stringify([{ ...localStorage }, { ...sessionStorage }, document.cookie]),
  );
  expect(exposed).not.toMatch(/token|eyJ[\w-]+\.[\w-]+/i);
});

test("e1_14_t03 SSRF-shaped proxy paths are 400", async ({ request }) => {
  for (const path of [
    "/api/proxy/http:/169.254.169.254/latest",
    "/api/proxy/api/v1/a%2F..%2Fadmin",
  ]) {
    const r = await request.get(path);
    expect(r.status(), path).toBe(400);
    expect(r.headers()["content-type"]).toBe("application/problem+json");
  }
});

test("e1_14_t07 anonymous proxy call is 401 problem+json", async ({ request }) => {
  const r = await request.get("/api/proxy/api/v1/projects");
  expect(r.status()).toBe(401);
  expect((await r.json()).status).toBe(401);
});

test("error-state demo renders every mapped status without hydration errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/dev/errors");
  for (const status of [403, 404, 409, 422, 423, 503, 412]) {
    await expect(page.getByText(`HTTP ${status}`)).toBeVisible();
  }
  await expect(page.getByText("name: required")).toBeVisible();
  await expect(page.getByRole("dialog").getByText("Out of date")).toBeVisible();
  expect(errors.filter((e) => /hydrat/i.test(e))).toEqual([]);
});
