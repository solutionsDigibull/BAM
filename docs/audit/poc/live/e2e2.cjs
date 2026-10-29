const { createRequire } = require("module"); const req = createRequire("/home/Fool/Arch - Work/BAM-audit/client/package.json"); const { chromium } = req("@playwright/test");
const WEB = "http://localhost:3077", API = "http://127.0.0.1:55997/api/v1", PW = "audit-User-Strong-Pw-9!";
const ok = (n, c, d = "") => console.log((c ? "OK   " : "VULN ") + n + (d ? `  [${d}]` : "")); const note = (s) => console.log("     " + s);
(async () => {
  const browser = await chromium.launch({ executablePath: "/usr/bin/chromium", args: ["--no-sandbox"] });
  async function login(portal, user) {
    const ctx = await browser.newContext(); const page = await ctx.newPage(); await page.goto(`${WEB}/login-${portal}`);
    await page.getByRole("button", { name: /keycloak|sign in/i }).first().click(); await page.waitForURL(/58080/);
    await page.fill("#username", user); await page.click("#kc-login"); await page.waitForSelector("#password"); await page.fill("#password", PW); await page.click("#kc-login");
    await page.waitForURL(/localhost:3077\/(?!login)/, { timeout: 20000 }); return { ctx, page };
  }
  // 1. proxy header handling without clobbering the session cookie
  let { ctx, page } = await login("fde", "kcfde");
  const r1 = await ctx.request.get(`${WEB}/api/proxy/api/v1/me`, { headers: { Authorization: "Bearer attacker", "X-Forwarded-For": "1.2.3.4" } }); ok("client-supplied Authorization does not replace the session token", r1.status() === 200, String(r1.status()));
  // 2. real sign-out through the UI, then replay the stolen cookie
  const stolen = (await ctx.cookies(WEB)).map(c => `${c.name}=${c.value}`).join("; ");
  const buttons = await page.getByRole("button").allInnerTexts(); note("buttons on /projects: " + JSON.stringify(buttons.slice(0, 12)));
  const out = page.getByText(/^Sign out$/).first();
  if (await out.count()) { await out.click(); await page.waitForTimeout(3000); note("after sign-out url: " + page.url()); }
  const anon = await browser.newContext();
  const replay = await anon.request.get(`${WEB}/api/proxy/api/v1/me`, { headers: { Cookie: stolen } });
  note(`replaying the pre-logout cookie right after sign-out -> ${replay.status()}`);
  ok("a stolen session cookie stops working immediately after sign-out", replay.status() === 401, `status=${replay.status()}; Auth.js JWT sessions are stateless, so the cookie keeps working until the 300 s access token expires`);
  // 3. stored XSS: admin creates project with hostile name/notes; admin portal renders it
  const A = await login("admin", "kcadmin");
  const payload = '"><img src=x onerror="window.__xss=1;alert(1)">';
  const notes = 'hi <script>window.__xss=2</script> [click](javascript:window.__xss=3) <img src=x onerror="window.__xss=4"> ![i](http://127.0.0.1:1/track.png)';
  const cr = await A.ctx.request.fetch(`${WEB}/api/proxy/api/v1/admin/projects`, { method: "POST", headers: { Origin: WEB, "content-type": "application/json" }, data: JSON.stringify({ name: "XSS " + Date.now(), client_name: payload, description: payload, context_notes: notes }) });
  note("create hostile project -> " + cr.status() + " " + (await cr.text()).slice(0, 100));
  let dialogs = []; A.page.on("dialog", d => { dialogs.push(d.message()); d.dismiss(); });
  const reqs = []; A.page.on("request", r => { if (/127\.0\.0\.1:1\/|evil/.test(r.url())) reqs.push(r.url()); });
  await A.page.goto(`${WEB}/admin/projects`); await A.page.waitForTimeout(2500);
  await A.page.getByText(/XSS \d+/).first().click().catch(() => {}); await A.page.waitForTimeout(2500);
  for (const u of [`${WEB}/admin/projects`]) { await A.page.goto(u); await A.page.waitForTimeout(1500); }
  const x = await A.page.evaluate(() => window.__xss); ok("no script executed from project fields/notes (window.__xss unset, no dialogs)", x === undefined && dialogs.length === 0, `xss=${x} dialogs=${dialogs.length}`);
  ok("markdown preview does not auto-load a remote image (tracking pixel)", reqs.length === 0, reqs.join(",") || "none");
  const links = await A.page.$$eval("a[href^='javascript:']", as => as.length); ok("no javascript: links rendered", links === 0, String(links));
  // 4. wrong-portal login
  const w = await browser.newContext(); const wp = await w.newPage(); await wp.goto(`${WEB}/login-admin`); await wp.getByRole("button", { name: /keycloak|sign in/i }).first().click(); await wp.waitForURL(/58080/);
  await wp.fill("#username", "kcfde"); await wp.click("#kc-login"); await wp.waitForSelector("#password"); await wp.fill("#password", PW); await wp.click("#kc-login"); await wp.waitForTimeout(4000);
  const wc = await w.cookies(WEB); note("FDE-only user signing in at the admin portal ends at: " + wp.url()); ok("FDE-only user gets no session from the admin portal login", !wc.some(c => /session-token/.test(c.name)), wp.url());
  await browser.close();
})().catch(e => { console.log("script error", e.message); process.exit(1); });
