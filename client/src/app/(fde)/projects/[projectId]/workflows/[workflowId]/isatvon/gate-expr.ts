// The SBAM gate grammar (E8.8, TD §10.4 rule 5) in TypeScript: a port of the backend's
// `sbam_contracts/gate_expr.py` for live parsing in the editor. Parse only: an expression is never
// evaluated in the browser, and the server's validation_errors stay the authority.
//
//   expr := or ; or := and ("OR" and)* ; and := not ("AND" not)* ; not := "NOT" not | cmp
//   cmp  := atom (("==" | "!=" | "<" | "<=" | ">" | ">=" | "is" ["not"]) atom)?
//   atom := NUMBER | STRING | True | False | None | IDENT | "(" expr ")"
//
// Parity with the backend (codes, messages, positions) is checked against the shared corpus in
// server/tests/fixtures/isatvon/gate_corpus.json by both test suites. Python's `\d`, `\s` and `\w`
// are Unicode-aware, so their classes are spelled out here.

export const MAX_LENGTH = 2000;
export const MAX_DEPTH = 32;
export const KEYWORDS = new Set(["AND", "OR", "NOT", "is", "not", "True", "False", "None"]);

export type GateCode =
  | "SYNTAX"
  | "CALL_NOT_ALLOWED"
  | "ATTRIBUTE_NOT_ALLOWED"
  | "SUBSCRIPT_NOT_ALLOWED"
  | "TOO_LONG"
  | "TOO_DEEP";

export type GateError = { code: GateCode; message: string; pos: number };
export type NameSpan = { name: string; pos: number };
export type GateParse =
  { ok: true; identifiers: string[]; spans: NameSpan[] } | { ok: false; error: GateError };

// Python str.isspace()
const SPACE =
  "\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const TOKEN = new RegExp(
  [
    `(?<ws>[${SPACE}]+)`,
    "(?<number>-?\\p{Nd}+(?:\\.\\p{Nd}+)?(?![\\p{L}\\p{N}_.]))",
    `(?<string>'[^'\\\\\\n]*'|"[^"\\\\\\n]*")`,
    "(?<ident>[A-Za-z_][A-Za-z0-9_]*)",
    "(?<op>==|!=|<=|>=|<|>)",
    "(?<paren>[()])",
    "(?<dot>\\.)",
    "(?<bracket>[\\[\\]])",
  ].join("|"),
  "uy",
);
const LEADING_SPACE = new RegExp(`^[${SPACE}]*`, "u");
const ONLY_SPACE = new RegExp(`^[${SPACE}]*$`, "u");

class Fail extends Error {
  constructor(
    readonly code: GateCode,
    readonly detail: string,
    readonly pos: number,
  ) {
    super(detail);
  }
}

/** Python's repr() of a string, as the backend puts it in messages. */
export function pyRepr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    if (ch === "\\") out += "\\\\";
    else if (ch === quote) out += `\\${quote}`;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch !== " " && /[\p{C}\p{Z}]/u.test(ch))
      out +=
        cp <= 0xff
          ? `\\x${cp.toString(16).padStart(2, "0")}`
          : cp <= 0xffff
            ? `\\u${cp.toString(16).padStart(4, "0")}`
            : `\\U${cp.toString(16).padStart(8, "0")}`;
    else out += ch;
  }
  return quote + out + quote;
}

type Tok = {
  kind: "number" | "string" | "ident" | "keyword" | "op" | "paren" | "end";
  text: string;
  pos: number;
};

function tokenize(text: string): Tok[] {
  const tokens: Tok[] = [];
  let pos = 0;
  while (pos < text.length) {
    TOKEN.lastIndex = pos;
    const match = TOKEN.exec(text);
    const groups = match?.groups;
    if (!match || !groups) {
      const ch = String.fromCodePoint(text.codePointAt(pos)!);
      throw new Fail("SYNTAX", `unexpected character ${pyRepr(ch)} at ${pos}`, pos);
    }
    const value = match[0];
    const end = pos + value.length;
    if (groups.dot !== undefined)
      throw new Fail("ATTRIBUTE_NOT_ALLOWED", `attribute access at ${pos}`, pos);
    if (groups.bracket !== undefined)
      throw new Fail("SUBSCRIPT_NOT_ALLOWED", `subscript at ${pos}`, pos);
    if (groups.ident !== undefined && !KEYWORDS.has(value)) {
      if (text.slice(end).replace(LEADING_SPACE, "").startsWith("("))
        throw new Fail("CALL_NOT_ALLOWED", `call of ${pyRepr(value)} at ${pos}`, pos);
      if (value.startsWith("__"))
        throw new Fail("SYNTAX", `dunder name ${pyRepr(value)} at ${pos}`, pos);
    }
    if (groups.ws === undefined) {
      const kind =
        groups.ident !== undefined
          ? KEYWORDS.has(value)
            ? "keyword"
            : "ident"
          : groups.number !== undefined
            ? "number"
            : groups.string !== undefined
              ? "string"
              : groups.op !== undefined
                ? "op"
                : "paren";
      tokens.push({ kind, text: value, pos });
    }
    pos = end;
  }
  tokens.push({ kind: "end", text: "", pos: text.length });
  return tokens;
}

class Parser {
  private i = 0;
  private depth = 0;
  readonly spans: NameSpan[] = [];

  constructor(private readonly tokens: Tok[]) {}

  private peek(): Tok {
    return this.tokens[this.i]!;
  }

  private take(): Tok {
    return this.tokens[this.i++]!;
  }

  private atKeyword(word: string): boolean {
    const tok = this.peek();
    return tok.kind === "keyword" && tok.text === word;
  }

  private enter(pos: number): void {
    this.depth += 1;
    if (this.depth > MAX_DEPTH) throw new Fail("TOO_DEEP", `nesting deeper than ${MAX_DEPTH}`, pos);
  }

  parse(): void {
    this.parseOr();
    const tok = this.peek();
    if (tok.kind !== "end")
      throw new Fail("SYNTAX", `unexpected ${pyRepr(tok.text)} at ${tok.pos}`, tok.pos);
  }

  private parseOr(): void {
    this.parseAnd();
    while (this.atKeyword("OR")) {
      this.take();
      this.parseAnd();
    }
  }

  private parseAnd(): void {
    this.parseNot();
    while (this.atKeyword("AND")) {
      this.take();
      this.parseNot();
    }
  }

  private parseNot(): void {
    if (this.atKeyword("NOT")) {
      const tok = this.take();
      this.enter(tok.pos);
      this.parseNot();
      this.depth -= 1;
      return;
    }
    this.parseCmp();
  }

  private parseCmp(): void {
    this.parseAtom();
    const tok = this.peek();
    if (tok.kind === "op") {
      this.take();
      this.parseAtom();
    } else if (this.atKeyword("is")) {
      this.take();
      if (this.atKeyword("not")) this.take();
      this.parseAtom();
    }
  }

  private parseAtom(): void {
    const tok = this.take();
    if (tok.kind === "number" || tok.kind === "string") return;
    if (tok.kind === "ident") {
      this.spans.push({ name: tok.text, pos: tok.pos });
      return;
    }
    if (tok.kind === "keyword" && ["True", "False", "None"].includes(tok.text)) return;
    if (tok.kind === "paren" && tok.text === "(") {
      this.enter(tok.pos);
      this.parseOr();
      const close = this.take();
      if (close.text !== ")") throw new Fail("SYNTAX", `expected ')' at ${close.pos}`, close.pos);
      this.depth -= 1;
      return;
    }
    const shown = tok.text || "end of expression";
    throw new Fail("SYNTAX", `unexpected ${pyRepr(shown)} at ${tok.pos}`, tok.pos);
  }
}

/** Parse a gate expression: its identifiers, or the first error (code, message, offset). */
export function parseGate(text: string): GateParse {
  try {
    if ([...text].length > MAX_LENGTH)
      throw new Fail("TOO_LONG", `longer than ${MAX_LENGTH} characters`, MAX_LENGTH);
    if (ONLY_SPACE.test(text)) throw new Fail("SYNTAX", "empty expression", 0);
    const parser = new Parser(tokenize(text));
    parser.parse();
    const identifiers = [...new Set(parser.spans.map((s) => s.name))].sort();
    return { ok: true, identifiers, spans: parser.spans };
  } catch (err) {
    if (err instanceof Fail)
      return { ok: false, error: { code: err.code, message: err.detail, pos: err.pos } };
    throw err;
  }
}

/** Calls, attribute access, subscripts and dunder names: shown as one label in the editor. */
export const GRAMMAR_FORBIDDEN = "GRAMMAR_FORBIDDEN";

export function errorLabel(error: GateError): string {
  const forbidden =
    error.code === "CALL_NOT_ALLOWED" ||
    error.code === "ATTRIBUTE_NOT_ALLOWED" ||
    error.code === "SUBSCRIPT_NOT_ALLOWED" ||
    (error.code === "SYNTAX" && error.message.startsWith("dunder name"));
  return forbidden ? GRAMMAR_FORBIDDEN : error.code;
}

/** "SYNTAX unexpected ')' at 7 (line 1, col 8)": the label, the backend's message and where. */
export function gateMessage(text: string, error: GateError): string {
  const { line, col } = lineCol(text, error.pos);
  return `${errorLabel(error)} ${error.message} (line ${line}, col ${col})`;
}

/** 1-based line and column of an offset, for "line 2, col 4" messages. */
export function lineCol(text: string, pos: number): { line: number; col: number } {
  const before = text.slice(0, pos).split("\n");
  return { line: before.length, col: before[before.length - 1]!.length + 1 };
}

/** Output field names of O.json_schema (real JSON Schema or the ISA14 short form), as the backend. */
export function outputFields(schema: Record<string, unknown> | undefined): string[] {
  if (!schema) return [];
  const props = schema.properties;
  if (schema.type === "object" && props && typeof props === "object" && !Array.isArray(props))
    return Object.keys(props);
  return Object.keys(schema);
}

/** Names a gate may read (TD §10.4 rule 5): outputs, fresh state, required inputs, max_iterations. */
export function gateScope(definition: {
  o_outcome?: { json_schema?: Record<string, unknown> };
  s_source?: { fresh_state_parameters?: string[]; required_inputs?: string[] };
}): string[] {
  const names = new Set<string>([
    ...outputFields(definition.o_outcome?.json_schema),
    ...(definition.s_source?.fresh_state_parameters ?? []),
    ...(definition.s_source?.required_inputs ?? []),
    "max_iterations",
  ]);
  return [...names].sort();
}

export type GateDiagnostic = {
  from: number;
  to: number;
  severity: "error" | "warning";
  message: string;
};

/**
 * What the editor marks: the parse error at its position, else a warning on every use of a name
 * outside the scope ("Unknown identifier foo"). The server's validation_errors stay the authority.
 */
export function gateDiagnostics(text: string, scope: readonly string[]): GateDiagnostic[] {
  if (!text.trim())
    return [{ from: 0, to: 0, severity: "error", message: "A gate expression is required" }];
  const parsed = parseGate(text);
  if (!parsed.ok) {
    const from = Math.min(parsed.error.pos, text.length);
    return [
      {
        from,
        to: Math.min(from + 1, text.length),
        severity: "error",
        message: `${errorLabel(parsed.error)}: ${parsed.error.message}`,
      },
    ];
  }
  const known = new Set(scope);
  return parsed.spans
    .filter((s) => !known.has(s.name))
    .map((s) => ({
      from: s.pos,
      to: s.pos + s.name.length,
      severity: "warning" as const,
      message: `Unknown identifier ${s.name}`,
    }));
}
