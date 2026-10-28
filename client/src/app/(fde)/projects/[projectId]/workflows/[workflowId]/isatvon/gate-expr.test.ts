import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import {
  GRAMMAR_FORBIDDEN,
  errorLabel,
  gateDiagnostics,
  gateScope,
  lineCol,
  parseGate,
  pyRepr,
} from "./gate-expr";

type Entry = { expr: string; ok: boolean; code?: string; message?: string; identifiers?: string[] };

// Read at run time (from client/, as pnpm test runs), not imported: the web image is built from
// client/ alone, without server/.
const corpus = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../server/tests/fixtures/isatvon/gate_corpus.json"),
    "utf-8",
  ),
) as Entry[];

describe("gate expressions (E8.8)", () => {
  test("E8.8-T01 a valid gate parses and its identifiers resolve", () => {
    const parsed = parseGate('retries >= max_iterations OR status == "FAILED"');
    expect(parsed.ok && parsed.identifiers).toEqual(["max_iterations", "retries", "status"]);
    expect(
      gateDiagnostics('retries >= max_iterations OR status == "FAILED"', [
        "retries",
        "status",
        "max_iterations",
      ]),
    ).toEqual([]);
  });

  test("E8.8-T02 a parse error is placed at col 10: unexpected end of expression", () => {
    const parsed = parseGate("status == ");
    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.error).toEqual({
      code: "SYNTAX",
      message: "unexpected 'end of expression' at 10",
      pos: 10,
    });
  });

  test.each(['__import__("os").system("x")', "a.b()", "a.b", "f(x)", "a[0]", "__class__ == 1"])(
    "E8.8-T03 %s is rejected as GRAMMAR_FORBIDDEN and nothing is evaluated",
    (expr) => {
      const parsed = parseGate(expr);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(errorLabel(parsed.error)).toBe(GRAMMAR_FORBIDDEN);
    },
  );

  test("E8.8-T04 the TS parser agrees with the backend on the shared corpus", () => {
    const entries = corpus;
    expect(entries.length).toBeGreaterThanOrEqual(100);
    for (const entry of entries) {
      const parsed = parseGate(entry.expr);
      const got = parsed.ok
        ? { ok: true, identifiers: parsed.identifiers }
        : { ok: false, code: parsed.error.code, message: parsed.error.message };
      const want = entry.ok
        ? { ok: true, identifiers: entry.identifiers }
        : { ok: false, code: entry.code, message: entry.message };
      expect(got, JSON.stringify(entry.expr)).toEqual(want);
    }
  });

  test("E8.8-T05 an unknown identifier is a warning on each use, never inside a string", () => {
    expect(gateDiagnostics("foo == 1 OR x == 'foo' OR foo", ["x"])).toEqual([
      { from: 0, to: 3, severity: "warning", message: "Unknown identifier foo" },
      { from: 26, to: 29, severity: "warning", message: "Unknown identifier foo" },
    ]);
    expect(gateDiagnostics("", [])).toEqual([
      { from: 0, to: 0, severity: "error", message: "A gate expression is required" },
    ]);
    expect(gateDiagnostics("a.b()", ["a"])[0]).toMatchObject({
      severity: "error",
      message: "GRAMMAR_FORBIDDEN: attribute access at 1",
    });
  });

  test("the scope is outputs, fresh state, required inputs and max_iterations", () => {
    const scope = gateScope({
      o_outcome: { json_schema: { auth_status: ["A"], jwt_token: "string" } },
      s_source: { fresh_state_parameters: ["user_status"], required_inputs: ["otp_payload"] },
    });
    expect(scope).toEqual([
      "auth_status",
      "jwt_token",
      "max_iterations",
      "otp_payload",
      "user_status",
    ]);
    expect(
      gateScope({ o_outcome: { json_schema: { type: "object", properties: { ok: {} } } } }),
    ).toEqual(["max_iterations", "ok"]);
  });

  test("line and column of an offset", () => {
    expect(lineCol("a ==\n1 )", 7)).toEqual({ line: 2, col: 3 });
    expect(lineCol("status == ", 10)).toEqual({ line: 1, col: 11 });
  });

  test("pyRepr quotes like Python", () => {
    expect(pyRepr("f")).toBe("'f'");
    expect(pyRepr("'LOCKED'")).toBe(`"'LOCKED'"`);
    expect(pyRepr("﻿")).toBe("'\\ufeff'");
  });
});
