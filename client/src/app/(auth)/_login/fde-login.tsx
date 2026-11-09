"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { devSignIn, keycloakSignIn } from "../actions";
import panel from "./assets/bull-panel.png";
import wordmarkDark from "./assets/bullm-wordmark-dark.png";
import wordmarkLight from "./assets/bullm-wordmark-light.png";
import { LOGIN_MESSAGES } from "./login-messages";
import "./login.css";

const ERRORS: Record<string, string> = {
  WrongPortal: "This account cannot use this portal. Sign in through the portal for your role.",
  NoRole: "This account has no SBAM role. Ask your Admin for access.",
  Configuration: "Sign-in is unavailable, try again later.", // Keycloak unreachable
};

const DEV_CHOICES = [
  { roles: "sbam-fde", label: "Sign in as FDE" },
  { roles: "sbam-admin,sbam-fde", label: "Sign in as Admin + FDE" },
];

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const Alert = () => (
  <svg
    width="15"
    height="15"
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    <circle cx="8" cy="8" r="6.2" />
    <path d="M8 4.6v4M8 11.2v.1" />
  </svg>
);

const Arrow = ({ dir }: { dir: "left" | "right" }) => (
  <svg
    width="20"
    height="20"
    viewBox="0 0 16 16"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    {dir === "left" ? <path d="M13 8H3m4-4L3 8l4 4" /> : <path d="M3 8h10M9 4l4 4-4 4" />}
  </svg>
);

/**
 * The FDE sign-in, in the BuLLM look. The password is never typed here: "Sign in" goes to Keycloak, which asks for
 * it on its own page. An email typed here only travels to Keycloak as a hint, so its form arrives filled in.
 */
export function FdeLogin({
  enabled,
  keycloak = false,
  error,
  reference,
  callbackUrl,
}: {
  /** Development sign-in (SBAM_DEV_AUTH=1): the role buttons stay for local work without Keycloak. */
  enabled: boolean;
  keycloak?: boolean;
  error?: string;
  reference?: string;
  callbackUrl?: string;
}) {
  const [email, setEmail] = useState("");
  const [invalid, setInvalid] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);

  function onSubmit(e: FormEvent) {
    // An empty email is fine (Keycloak asks for it); a typed one must look like an email.
    if (email.trim() && !EMAIL_RE.test(email.trim())) {
      e.preventDefault();
      setInvalid(true);
      emailRef.current?.focus();
    }
  }

  return (
    <main className="login-page">
      <div className="login-card">
        <Showcase />
        <section className="login-left">
          <div className="login-brand">
            <picture>
              <source media="(prefers-color-scheme: light)" srcSet={wordmarkLight.src} />
              <img
                src={wordmarkDark.src}
                alt="BuLLM. Enterprise Workflows. Intelligently Done."
                className="login-lockup"
                width={215}
              />
            </picture>
          </div>
          <h1 className="login-title">Welcome back</h1>
          <p className="login-lede">Sign in to your BuLLM workspace.</p>

          {error && (
            <div className="login-alert" role="alert">
              <Alert />
              <span>
                <span>{ERRORS[error] ?? "Sign-in failed. Try again."}</span>
                {reference && <span className="login-ref">Reference: {reference}</span>}
              </span>
            </div>
          )}

          {keycloak && (
            <form className="login-form" action={keycloakSignIn} noValidate onSubmit={onSubmit}>
              <input type="hidden" name="portal" value="fde" />
              {callbackUrl && <input type="hidden" name="callbackUrl" value={callbackUrl} />}
              <div className="login-field">
                <label htmlFor="login-email">Work email</label>
                <input
                  id="login-email"
                  ref={emailRef}
                  name="email"
                  type="email"
                  autoComplete="username"
                  placeholder="you@company.com"
                  autoFocus
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setInvalid(false);
                  }}
                  aria-invalid={invalid}
                  aria-describedby={invalid ? "login-email-err" : "login-pw-note"}
                />
                {invalid && (
                  <p id="login-email-err" className="login-err">
                    <Alert />
                    Enter a valid work email
                  </p>
                )}
                <p id="login-pw-note" className="login-hint">
                  You enter your password on the next secure step.
                </p>
              </div>
              <button type="submit" className="login-submit" aria-label="Sign in with Keycloak">
                Sign in
              </button>
            </form>
          )}

          {enabled && (
            <div className="login-dev">
              <p className="login-hint">Development sign-in (SBAM_DEV_AUTH=1)</p>
              {DEV_CHOICES.map((c) => (
                <form key={c.roles} action={devSignIn}>
                  <input type="hidden" name="roles" value={c.roles} />
                  <button type="submit" className="login-submit">
                    {c.label}
                  </button>
                </form>
              ))}
            </div>
          )}

          {!enabled && !keycloak && (
            <p className="login-hint">Keycloak sign-in is not configured yet.</p>
          )}

          <p className="login-note">
            Access is invite-only. Contact your administrator for an account.
          </p>
          <p className="login-hint">
            <a href="/login-admin">Admin? Use the Admin login</a>
          </p>
        </section>
      </div>
    </main>
  );
}

const ROTATE_MS = 6000;

function Showcase() {
  const n = LOGIN_MESSAGES.length;
  const [i, setI] = useState(0);
  const [prev, setPrev] = useState<number | null>(null);
  const [manual, setManual] = useState(false);
  const [held, setHeld] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [restart, setRestart] = useState(0);
  const cur = useRef(0);

  const show = (to: number, byUser: boolean) => {
    const next = ((to % n) + n) % n;
    if (next === cur.current) return;
    setPrev(cur.current);
    cur.current = next;
    setI(next);
    setManual(byUser);
    if (byUser) setRestart((r) => r + 1);
  };

  useEffect(() => {
    const f = () => setHidden(document.hidden);
    f();
    document.addEventListener("visibilitychange", f);
    return () => document.removeEventListener("visibilitychange", f);
  }, []);
  useEffect(() => {
    if (held || hidden || n < 2) return;
    const t = setInterval(() => show(cur.current + 1, false), ROTATE_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [held, hidden, n, restart]);

  const m = LOGIN_MESSAGES[i]!;
  const old = prev !== null ? LOGIN_MESSAGES[prev] : null;
  return (
    <aside className="login-panel" aria-label="FDE workspace highlights">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={panel.src} alt="" className="login-panel-art" width={580} height={440} />
      <div className="login-panel-body">
        <ul className="login-chips" aria-label="Product areas">
          <li>Four stages</li>
          <li>FDE Workbench</li>
          <li>Sandbox</li>
        </ul>
        <div
          className="login-rotator"
          onMouseEnter={() => setHeld(true)}
          onMouseLeave={() => setHeld(false)}
          onFocus={() => setHeld(true)}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHeld(false);
          }}
        >
          <div className="login-quote" aria-live={manual ? "polite" : "off"}>
            {old && (
              <div
                key={`o${prev}`}
                className="login-quote-text out"
                aria-hidden
                onAnimationEnd={() => setPrev(null)}
              >
                <p className="login-quote-head">{old.headline}</p>
                <p className="login-quote-flow">{old.flow}</p>
              </div>
            )}
            <div key={i} className="login-quote-text in">
              <p className="login-quote-head">{m.headline}</p>
              <p className="login-quote-flow">{m.flow}</p>
            </div>
          </div>
          <div className="login-controls">
            <div className="login-dots">
              {LOGIN_MESSAGES.map((_, k) => (
                <button
                  key={k}
                  type="button"
                  className="login-dot"
                  aria-label={`Show message ${k + 1} of ${n}`}
                  aria-current={k === i ? "true" : undefined}
                  onClick={() => show(k, true)}
                />
              ))}
            </div>
            <div className="login-arrows">
              <button
                type="button"
                className="login-arrow"
                aria-label="Previous message"
                onClick={() => show(cur.current - 1, true)}
              >
                <Arrow dir="left" />
              </button>
              <button
                type="button"
                className="login-arrow"
                aria-label="Next message"
                onClick={() => show(cur.current + 1, true)}
              >
                <Arrow dir="right" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );
}
