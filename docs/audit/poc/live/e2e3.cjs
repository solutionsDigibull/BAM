const { createRequire } = require("module"); const req = createRequire("/home/Fool/Arch - Work/BAM-audit/client/package.json"); const { chromium } = req("@playwright/test");
const WEB = "http://localhost:3077", PW = "audit-User-Strong-Pw-9!";
(async () => {
  const b = await chromium.launch({ executablePath: "/usr/bin/chromium", args: ["--no-sandbox"] }); const ctx = await b.newContext(); const page = await ctx.newPage();
  await page.goto(`${WEB}/login-fde`); await page.getByRole("button", { name: /keycloak|sign in/i }).first().click(); await page.waitForURL(/58080/);
  await page.fill("#username", "kcfde"); await page.click("#kc-login"); await page.waitForSelector("#password"); await page.fill("#password", PW); await page.click("#kc-login"); await page.waitForURL(/localhost:3077\/(?!login)/);
  const stolen = (await ctx.cookies(WEB)).map(c => `${c.name}=${c.value}`).join("; "); const t0 = Date.now();
  await page.getByText(/^Sign out$/).first().click(); await page.waitForTimeout(3000);
  const anon = await b.newContext(); const probe = async () => (await anon.request.get(`${WEB}/api/proxy/api/v1/me`, { headers: { Cookie: stolen } })).status();
  const out = []; for (const wait of [0, 120, 120, 100, 30, 60]) { await new Promise(r => setTimeout(r, wait * 1000)); out.push(`t+${Math.round((Date.now() - t0) / 1000)}s:${await probe()}`); }
  console.log("replay of the pre-logout session cookie ->", out.join("  ")); await b.close();
})();
