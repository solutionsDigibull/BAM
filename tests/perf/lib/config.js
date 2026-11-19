// Env-only configuration, the production guard and auth for the E15.4 (#138) k6 scenarios.
//
// Env:
//   BASE_URL      API origin, e.g. https://bam-perf.internal (the API routes live under API_PREFIX).
//   API_PREFIX    default /api/v1 (server/api/sbam_api/main.py mounts every router there).
//   PROJECT_ID    a synthetic perf project the FDE user is assigned to.
//   WORKFLOW_ID   a workflow of that project (T01 builds list, T02 grid).
//   Auth, one of:
//   TOKEN         a Keycloak access token (aud sbam-api). The realm gives it 300 s
//                 (compose/keycloak/realm-bullm.json "accessTokenLifespan"), so it only suits short runs.
//   LOGIN_USER + LOGIN_PASSWORD + KEYCLOAK_URL (realm URL, e.g. https://kc.example/realms/bullm)
//                 + WEB_URL (portal origin; Keycloak only accepts its registered redirect URI):
//                 each VU logs in like the portal does (authorization code + PKCE, client sbam-fde,
//                 the same flow as server/tests/integration/test_keycloak_realm.py) and refreshes its
//                 own token before it expires. Direct password grants are disabled on sbam-fde
//                 (realm-bullm.json "directAccessGrantsEnabled": false), so there is no shortcut.
import http from "k6/http";
import crypto from "k6/crypto";
import encoding from "k6/encoding";

import { Counter } from "k6/metrics";

export const BASE_URL = (__ENV.BASE_URL || "").replace(/\/$/, "");
export const API = BASE_URL + (__ENV.API_PREFIX || "/api/v1");
export const PROJECT_ID = __ENV.PROJECT_ID || "";
export const WORKFLOW_ID = __ENV.WORKFLOW_ID || "";

/** 5xx responses: #138 safety check "No 5xx ... under concurrency". */
export const serverErrors = new Counter("server_errors");

/** Refuse to run without a target, or against anything that looks like production (#138 safety check
 * "Perf data synthetic; never on production"). Called in the init context, so k6 stops before any VU. */
export function guard(required) {
  if (!__ENV.BASE_URL) throw new Error("BASE_URL is required");
  for (const name of ["BASE_URL", "KEYCLOAK_URL", "WEB_URL", "TUS_URL"]) {
    if ((__ENV[name] || "").toLowerCase().includes("prod")) {
      throw new Error(`${name} looks like production (contains "prod"); perf tests never run there`);
    }
  }
  for (const name of required || []) {
    if (!__ENV[name]) throw new Error(`${name} is required`);
  }
  if (!__ENV.TOKEN && !(__ENV.LOGIN_USER && __ENV.LOGIN_PASSWORD && __ENV.KEYCLOAK_URL && __ENV.WEB_URL)) {
    throw new Error("Set TOKEN, or LOGIN_USER + LOGIN_PASSWORD + KEYCLOAK_URL + WEB_URL");
  }
}

// ---- per-VU token -------------------------------------------------------------------------------

const CLIENT = __ENV.KEYCLOAK_CLIENT || "sbam-fde";
const REFRESH_MARGIN_S = 60;
let session = null; // { access, refresh, expiresAt } for this VU (module state is per VU in k6)

const noJar = () => new http.CookieJar();

function cookieHeader(jar) {
  return Object.entries(jar)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}

function keep(jar, res) {
  for (const [name, list] of Object.entries(res.cookies || {})) {
    if (list.length) jar[name] = list[0].value;
  }
}

function tokenRequest(form) {
  const res = http.post(`${__ENV.KEYCLOAK_URL}/protocol/openid-connect/token`, form, {
    jar: noJar(),
    tags: { name: "keycloak token" },
  });
  if (res.status !== 200) throw new Error(`Keycloak token request failed: ${res.status}`);
  const t = res.json();
  return { access: t.access_token, refresh: t.refresh_token, expiresAt: Date.now() / 1000 + t.expires_in };
}

/** Authorization code + PKCE through Keycloak's identity-first login pages (username, then password). */
function login() {
  const verifier = encoding.b64encode(crypto.randomBytes(48), "rawurl");
  const challenge = crypto.sha256(verifier, "base64rawurl");
  const redirect = `${__ENV.WEB_URL.replace(/\/$/, "")}/api/auth/callback/keycloak-${CLIENT.replace("sbam-", "")}`;
  const q = [
    `client_id=${CLIENT}`,
    "response_type=code",
    "scope=openid",
    `redirect_uri=${encodeURIComponent(redirect)}`,
    `state=${crypto.hexEncode(crypto.randomBytes(8))}`,
    `code_challenge=${challenge}`,
    "code_challenge_method=S256",
  ].join("&");
  const jar = {};
  const params = () => ({ jar: noJar(), redirects: 0, headers: { Cookie: cookieHeader(jar) }, tags: { name: "keycloak login" } });
  let page = http.get(`${__ENV.KEYCLOAK_URL}/protocol/openid-connect/auth?${q}`, params());
  keep(jar, page);
  for (let i = 0; i < 4; i++) {
    const form = /<form[^>]*action="([^"]+)"/.exec(page.body || "");
    if (!form) throw new Error(`no Keycloak login form (status ${page.status})`);
    const action = form[1].replace(/&amp;/g, "&");
    page = http.post(action, { username: __ENV.LOGIN_USER, password: __ENV.LOGIN_PASSWORD }, params());
    keep(jar, page);
    const location = page.headers.Location || page.headers.location || "";
    if (location.startsWith(redirect)) {
      const code = /[?&]code=([^&]+)/.exec(location)[1];
      return tokenRequest({
        grant_type: "authorization_code",
        code: decodeURIComponent(code),
        redirect_uri: redirect,
        client_id: CLIENT,
        code_verifier: verifier,
      });
    }
  }
  throw new Error("Keycloak login never reached the callback");
}

/** This VU's bearer token: TOKEN as given, or a login of its own refreshed before it expires. Each VU
 * keeps its own refresh token because the realm rotates them ("revokeRefreshToken": true). */
export function token() {
  if (__ENV.TOKEN) return __ENV.TOKEN;
  const now = Date.now() / 1000;
  if (session && now < session.expiresAt - REFRESH_MARGIN_S) return session.access;
  session = session
    ? tokenRequest({ grant_type: "refresh_token", client_id: CLIENT, refresh_token: session.refresh })
    : login();
  return session.access;
}

/** GET an API path with the bearer token, tagged with its route template so metrics group by route. */
export function apiGet(path, route, extraTags) {
  const res = http.get(API + path, {
    headers: { Authorization: `Bearer ${token()}`, Accept: "application/json" },
    tags: Object.assign({ name: `GET ${route}` }, extraTags || {}),
  });
  if (res.status >= 500) serverErrors.add(1, { name: `GET ${route}` });
  return res;
}
