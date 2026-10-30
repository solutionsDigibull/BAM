// A-18c (#542): drive Keycloak's browser flow in Chromium with a virtual authenticator (a software
// security key), so a real WebAuthn registration and sign-in happen. Usage:
//   node kc_webauthn.mjs <playwright-module-path> <json args>
// args: {base, clientId, redirect, username, password, otp?, useKey?}  (otp: answer a one-time code
// prompt; useKey: on a one-time code prompt, take "try another way" and use the security key)
// Prints one JSON line: {outcome, steps}. outcome = "code" (signed in), "registered" (the key was
// registered and the flow went on), "totp" (asked for a one-time code), "setup" (asked to enrol TOTP),
// or "other:<what the page said>".
import { createRequire } from "node:module";

const require = createRequire(process.argv[2]);
const { chromium } = require("@playwright/test");
const a = JSON.parse(process.argv[3]);

const steps = [];
const browser = await chromium.launch();
const context = await browser.newContext();
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send("WebAuthn.enable");
const store = process.env.WEBAUTHN_STATE ? JSON.parse(process.env.WEBAUTHN_STATE) : null;
const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
  options: {
    protocol: "ctap2",
    transport: "usb",
    hasResidentKey: true,
    hasUserVerification: true,
    isUserVerified: true,
    automaticPresenceSimulation: true,
  },
});
if (store) {
  for (const credential of store) await cdp.send("WebAuthn.addCredential", { authenticatorId, credential });
}

let code = null;
let landed = null;
// The redirect target is not a real server: note the request the browser makes to it (it carries
// the code) and let it fail.
const target = new URL(a.redirect).origin;
page.on("request", (request) => {
  if (!request.url().startsWith(target)) return;
  landed = new URL(request.url()).search.slice(0, 120);
  if (new URL(request.url()).searchParams.get("code")) code = "seen";
});

const auth = new URL(`${a.base}/realms/bullm/protocol/openid-connect/auth`);
auth.search = new URLSearchParams({
  client_id: a.clientId,
  redirect_uri: a.redirect,
  response_type: "code",
  scope: "openid",
  code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
  code_challenge_method: "S256",
}).toString();
await page.goto(auth.toString());
await page.fill("#username", a.username);
await page.fill("#password", a.password);
await page.click("#kc-login");
steps.push("password");

const text = async () => (await page.locator("body").innerText()).toLowerCase();
let outcome = "other";
for (let i = 0; i < 12 && !code; i++) {
  await page.waitForTimeout(700);
  if (code) break;
  const body = await text().catch(() => "");
  if (await page.locator("#kc-register-credential-button, button:has-text('Register')").count().catch(() => 0)) {
    if (body.includes("security key") || body.includes("passkey") || body.includes("webauthn")) {
      steps.push("register-page");
      await page.locator("#registerWebAuthn, input[value='Register'], button:has-text('Register')").first().click().catch(() => {});
      continue;
    }
  }
  if (await page.locator("#authenticateWebAuthnButton, input[value='Use security key'], button:has-text('Use')").count().catch(() => 0)) {
    steps.push("authenticate-page");
    await page.locator("#authenticateWebAuthnButton, input[value='Use security key'], button:has-text('Use')").first().click().catch(() => {});
    continue;
  }
  if (await page.locator("input[name='totpSecret']").count()) { outcome = "setup"; break; }
  if (await page.locator("input[name='otp']").count()) {
    if (a.useKey && !steps.includes("other-way")) {
      steps.push("other-way");
      await page.click("#try-another-way");
      await page.waitForSelector("body[data-page-id='login-select-authenticator']", { timeout: 15000 });
      if (process.env.KC_DEBUG) console.error("SELECTION:" + (await page.locator("body").innerHTML()).replace(/<script.*?<\/script>/gs, "").replace(/\s+/g, " ").slice(0, 3500));
      await page.locator(".select-auth-box-parent").filter({ hasText: /security key|passkey|webauthn/i }).first().click();
      continue;
    }
    if (a.otp && !steps.includes("otp")) {
      steps.push("otp");
      await page.fill("input[name='otp']", a.otp);
      await page.click("#kc-login");
      continue;
    }
    outcome = "totp";
    break;
  }
}
const credentials = (await cdp.send("WebAuthn.getCredentials", { authenticatorId })).credentials;
if (code) outcome = "code";
else if (steps.includes("register-page") && outcome === "other") outcome = "registered";
if (outcome === "other") outcome = `other:${landed} ${page.url().slice(0, 60)} ${(await text().catch(() => "")).replace(/\s+/g, " ").slice(0, 200)}`;
console.log(JSON.stringify({ outcome, steps, credentials }));
await browser.close();
