// The code viewer's data rules (TD §9.3, E11.5), free of React so they can be tested alone.
import type { CodeFinding, LineRange } from "@/contracts";

export const codeUrl = (projectId: string, workflowId: string, subId: string) =>
  `projects/${projectId}/workflows/${workflowId}/sub-skills/${subId}/code`;
export const attemptsUrl = (p: string, w: string, s: string) => `${codeUrl(p, w, s)}/attempts`;
export const fileUrl = (p: string, w: string, s: string, n: number) =>
  `${attemptsUrl(p, w, s)}/${n}/files`;
export const diffUrl = (p: string, w: string, s: string, from: number, to: number) =>
  `${attemptsUrl(p, w, s)}/${from}/diff/${to}`;

export type CodeLine = {
  n: number; // 1-based
  text: string;
  editable: boolean; // inside an LLM_BODY slot; every other line is protected template code
  findings: CodeFinding[];
};

const inside = (n: number, ranges: LineRange[]) => ranges.some((r) => n >= r.start && n <= r.end);

/**
 * One entry per line of `content`. A finding whose line is past the end (or has none) is not dropped:
 * it is returned in `loose` so the viewer can still list it.
 */
export function toLines(
  content: string,
  slots: LineRange[],
  findings: CodeFinding[],
): { lines: CodeLine[]; loose: CodeFinding[] } {
  const texts = content.split("\n");
  if (texts.length > 1 && texts[texts.length - 1] === "") texts.pop(); // the trailing newline
  const lines = texts.map<CodeLine>((text, i) => ({
    n: i + 1,
    text,
    editable: inside(i + 1, slots),
    findings: [],
  }));
  const loose: CodeFinding[] = [];
  for (const f of findings) {
    const line = typeof f.line === "number" ? lines[f.line - 1] : undefined;
    if (line) line.findings.push(f);
    else loose.push(f);
  }
  return { lines, loose };
}

export type LineDecoration = {
  line: number; // 1-based
  kind: "protected" | "finding";
  hover?: string; // findings only: gate code and message
};

/** What the editor paints: protected lines shaded, and a marker with its message on each finding. */
export function decorations(lines: CodeLine[]): LineDecoration[] {
  const out: LineDecoration[] = [];
  for (const l of lines) {
    if (!l.editable) out.push({ line: l.n, kind: "protected" });
    if (l.findings.length > 0)
      out.push({ line: l.n, kind: "finding", hover: l.findings.map(findingText).join("\n") });
  }
  return out;
}

/** A tooltip for a finding: its code first, then the message. */
export const findingText = (f: CodeFinding) => `${f.code}: ${f.message}`;

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "context";

/** A unified diff line's role, from its first characters. */
export function diffKind(line: string): DiffLineKind {
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "context";
}
