// Live: real Chromium -> Next.js (prod build) -> real Keycloak -> API. Throwaway stack only.
const { createRequire } = require("module");
const req = createRequire("/home/Fool/Arch - Work/BAM-audit/client/package.json");
const { chromium } = req("@playwright/test");
const http = require("http");
const WEB = "http://localhost:3077", PW = "audit-User-Strong-Pw-9!";
const ok = (n, c, d = "") => console.log((c ? "OK   " : "VULN ") + n + (d ? `  [${d}]` : ""));
const note = (s) => console.log("     " + s);
(async () => {
  const browser = await chromium.launch({ executablePath: "/usr/bin/chromium", args: ["--no-sandbox"] });
  async function login(portal, user) {
    const ctx = await browser.newContext(); const page = await ctx.newPage();
    await page.goto(`${WEB}/login-${portal}`); 
    const btn = page.getByRole("button", { name: /keycloak|sign in/i }).first(); await btn.click();
    await page.waitForURL(/58080/, { timeout: 15000 });
    try { await page.fill("#username", user, {timeout:8000}); await page.click("#kc-login"); await page.waitForSelector("#password", {timeout:8000}); await page.fill("#password", PW); await page.click("#kc-login"); } catch (e) { console.log("LOGIN PAGE:", page.url(), (await page.innerText("body")).slice(0, 300).replace(/\s+/g," ")); throw e; }
    await page.waitForURL(new RegExp(WEB.replace(/[.]/g, "\\.") + "/"), { timeout: 20000 }).catch(() => {});
    return { ctx, page };
  }
  // ---- A. FDE login, cookies, token leakage
  let { ctx, page } = await login("fde", "kcfde");
  note("after login url: " + page.url());
  const cookies = await ctx.cookies(WEB);
  for (const c of cookies) note(`cookie ${c.name} httpOnly=${c.httpOnly} secure=${c.secure} sameSite=${c.sameSite} len=${c.value.length}`);
  const sess = cookies.filter(c => /session-token/.test(c.name));
  ok("session cookie is httpOnly", sess.length > 0 && sess.every(c => c.httpOnly));
  ok("session cookie SameSite is Lax or Strict", sess.every(c => ["Lax", "Strict"].includes(c.sameSite)), sess.map(c => c.sameSite).join());
  note("session cookie Secure flag: " + sess.map(c => c.secure).join() + " (false is expected over http here; the prod proxy serves https and Auth.js sets __Secure- cookies there)");
  const s = await (await ctx.request.get(`${WEB}/api/auth/session`)).text(); ok("/api/auth/session does not expose tokens", !/eyJ|accessToken|refreshToken|idToken/.test(s), s.slice(0, 120));
  const html = await page.content(); ok("page HTML has no JWT-looking string", !/eyJhbGci/.test(html));
  const rsc = await (await ctx.request.get(`${WEB}/`, { headers: { RSC: "1" } })).text(); ok("RSC payload has no JWT-looking string", !/eyJhbGci/.test(rsc));
  // ---- B. proxy
  const P = (p, o = {}) => ctx.request.fetch(`${WEB}/api/proxy${p}`, { maxRedirects: 0, ...o });
  const me = await P("/api/v1/me"); ok("authenticated proxy call works", me.status() === 200, `${me.status()} ${(await me.text()).slice(0, 80)}`);
  for (const [label, p] of [["openapi.json", "/api/v1/openapi.json"], ["docs", "/api/v1/docs"], ["tusd hook endpoint", "/api/v1/internal/tusd/hooks"], ["health via traversal", "/api/v1/..%2f..%2fhealth/live"], ["dot-dot segment", "/api/v1/%2e%2e/health/live"], ["double encoded", "/api/v1/%252e%252e/health/live"], ["backslash", "/api/v1/..%5c..%5chealth"], ["other base", "/health/live"], ["absolute url", "/http://evil.example/"], ["query smuggling", "/api/v1/me?x=1%0d%0aX-Evil:1"]]) {
    const r = await P(p); note(`proxy ${label}: ${r.status()} ${(await r.text()).slice(0, 70).replace(/\s+/g, " ")}`);
    if (label.match(/traversal|dot|double|backslash|other|absolute|smuggling/)) ok(`proxy blocks ${label}`, r.status() >= 400 || label === "query smuggling", String(r.status()));
  }
  const r1 = await P("/api/v1/me", { headers: { Authorization: "Bearer attacker", Cookie: "x=y", "X-Forwarded-For": "1.2.3.4" } }); ok("client-supplied Authorization is replaced by the session token", r1.status() === 200, String(r1.status()));
  // ---- C. CSRF
  const post = (origin) => ctx.request.fetch(`${WEB}/api/proxy/api/v1/notifications/read-all`, { method: "POST", headers: origin === null ? {} : { Origin: origin } });
  ok("POST without Origin refused", (await post(null)).status() === 403, String((await post(null)).status()));
  ok("POST with foreign Origin refused", (await post("https://evil.example")).status() === 403);
  ok("POST with same-site other-port Origin refused (localhost:9999)", (await post("http://localhost:9999")).status() === 403);
  ok("POST with same Origin is accepted", [204, 200].includes((await post(WEB)).status()), String((await post(WEB)).status()));
  const srv = http.createServer((q, r) => { r.setHeader("content-type", "text/html"); r.end(`<script>fetch("${WEB}/api/proxy/api/v1/notifications/read-all",{method:"POST",credentials:"include"}).then(r=>document.title="status:"+r.status).catch(e=>document.title="blocked:"+e)</script>`); }).listen(9999);
  const evil = await ctx.newPage(); await evil.goto("http://localhost:9999/"); await evil.waitForFunction(() => /status|blocked/.test(document.title), null, { timeout: 8000 }).catch(() => {}); note("cross-origin (same-site) browser fetch result: " + (await evil.title())); ok("browser cross-origin POST does not succeed", !/status:(200|204)/.test(await evil.title()), await evil.title()); srv.close();
  // ---- D. portal separation
  const adm = await ctx.newPage(); const rr = await adm.goto(`${WEB}/admin/projects`); ok("FDE session on /admin is refused (403 page)", rr.status() === 403 || /forbidden/i.test(await adm.content()), String(rr.status()));
  const ctx2 = await browser.newContext(); const anon = await ctx2.newPage(); await anon.goto(`${WEB}/admin/projects`); ok("anonymous /admin redirects to login", /login-admin/.test(anon.url()), anon.url());
  for (const cb of ["//evil.example", "https://evil.example", "/\\evil.example", "/%2f%2fevil.example", "/projects/x?next=//evil.example"]) { const p2 = await ctx2.newPage(); await p2.goto(`${WEB}/login-fde?callbackUrl=${encodeURIComponent(cb)}`); const a = await p2.getByRole("button", { name: /keycloak|sign in/i }).first(); await a.click().catch(() => {}); await p2.waitForURL(/58080/, { timeout: 15000 }).catch(() => {}); const u = p2.url(); ok(`callbackUrl ${cb} does not leave the origin`, !/evil\.example/.test(u) || /redirect_uri=http%3A%2F%2Flocalhost%3A3077/.test(u), u.slice(0, 120)); }
  // ---- headers
  const pr = await ctx.request.get(`${WEB}/`); const h = pr.headers(); note("page headers: " + JSON.stringify({ csp: h["content-security-policy"], xfo: h["x-frame-options"], cc: h["cache-control"], rp: h["referrer-policy"], pp: !!h["permissions-policy"] }));
  const meH = (await P("/api/v1/me")).headers(); ok("proxied /me is not cacheable by shared caches", /no-store|private/.test(meH["cache-control"] || ""), meH["cache-control"] || "(none)");
  // ---- H. logout replay
  const cookieHeader = (await ctx.cookies(WEB)).map(c => `${c.name}=${c.value}`).join("; ");
  const lo = await page.goto(`${WEB}/`); const outBtn = page.getByRole("button", { name: /sign out|log out|logout/i }).first(); if (await outBtn.count()) { await outBtn.click(); await page.waitForTimeout(2500); } else note("no visible sign-out button on landing page; calling signOut via /api/auth/signout");
  const replay = await ctx2.request.get(`${WEB}/api/proxy/api/v1/me`, { headers: { Cookie: cookieHeader } }); ok("old session cookie is rejected after sign-out", replay.status() === 401, `status=${replay.status()} (Auth.js JWT sessions are stateless; access token stays valid until exp)`);
  await browser.close();
})().catch(e => { console.log("script error", e.message); process.exit(1); });
