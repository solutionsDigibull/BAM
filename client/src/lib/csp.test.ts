import { describe, expect, it } from "vitest";

import { buildCsp, newNonce } from "./csp";

const directive = (csp: string, name: string) =>
  csp
    .split("; ")
    .find((d) => d.startsWith(`${name} `))
    ?.slice(name.length + 1);

describe("buildCsp (A-11)", () => {
  const csp = buildCsp("abc123");

  it("allows scripts only with the nonce and strict-dynamic, never inline or eval", () => {
    const script = directive(csp, "script-src");
    expect(script).toBe("'self' 'nonce-abc123' 'strict-dynamic'");
    expect(script).not.toMatch(/unsafe-inline|unsafe-eval|https?:|\*/);
  });

  it("allows eval only in development", () => {
    expect(directive(buildCsp("n", true), "script-src")).toContain("'unsafe-eval'");
    expect(directive(buildCsp("n", false), "script-src")).not.toContain("unsafe-eval");
  });

  it("locks down everything else", () => {
    expect(directive(csp, "default-src")).toBe("'self'");
    for (const d of ["object-src", "frame-ancestors"]) expect(directive(csp, d)).toBe("'none'");
    for (const d of ["base-uri", "form-action", "connect-src"]) {
      expect(directive(csp, d)).toBe("'self'");
    }
    expect(directive(csp, "worker-src")).toBe("'self' blob:");
    expect(csp).not.toMatch(/http:|https:|\*/);
  });

  describe("uploads to tusd (#568)", () => {
    const local = "http://localhost:1080/files/";

    it("allows the local tusd origin in development", () => {
      expect(directive(buildCsp("n", true, local), "connect-src")).toBe(
        "'self' http://localhost:1080",
      );
    });

    it("never allows it in production: connect-src stays 'self'", () => {
      expect(directive(buildCsp("n", false, local), "connect-src")).toBe("'self'");
      expect(buildCsp("n", false, local)).not.toContain("1080");
    });

    it("allows the origin only: no path, no wildcard", () => {
      const connect = directive(
        buildCsp("n", true, "http://localhost:1555/files/x?y=1"),
        "connect-src",
      );
      expect(connect).toBe("'self' http://localhost:1555");
      expect(connect).not.toMatch(/\*|\/files/);
    });

    it.each([
      "",
      "not a url",
      "javascript:alert(1)",
      "data:text/plain,x",
      "ftp://localhost/files/",
    ])("adds nothing for %j", (url) => {
      expect(directive(buildCsp("n", true, url), "connect-src")).toBe("'self'");
    });
  });

  it("makes a fresh unguessable nonce each time", () => {
    const seen = new Set(Array.from({ length: 50 }, newNonce));
    expect(seen.size).toBe(50);
    expect([...seen][0]).toMatch(/^[A-Za-z0-9+/=]{40,}$/);
  });
});
