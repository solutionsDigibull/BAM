// Pure helpers of the Rules Engine (#784): the workflow map, rule category and status, prompts.
import type { MasterEntry, MicroPrompt, RulePrompts } from "@/contracts";

import { cellText } from "./masters-api";

/** The rule types of the Rules column description (Policy, Prompt, Estimation, ...). */
export const RULE_TYPES = [
  "Policy",
  "Prompt",
  "Estimation",
  "Calculation",
  "Validation",
  "Decision/routing",
] as const;
export const OTHER = "Other";
export const RULE_STATUSES = ["Proposed", "Approved", "Illustrative"] as const;
export const MAX_MICRO = 50; // the contract's maxItems

/** The first known type named in the rule's description; Other when none is. */
export function ruleCategory(e: MasterEntry): string {
  const text = cellText(e.payload.rule_type_description).toLowerCase();
  return RULE_TYPES.find((t) => text.includes(t.toLowerCase())) ?? OTHER;
}

/** The status word of the Status column, else Proposed (the server's default). */
export function ruleStatus(e: MasterEntry): string {
  const word = /^\s*([A-Za-z]+)/.exec(cellText(e.payload.record_status))?.[1]?.toLowerCase();
  return RULE_STATUSES.find((s) => s.toLowerCase() === word) ?? "Proposed";
}

const ids = (text: string) => (text.match(/\bSK-\d+\b/gi) ?? []).map((s) => s.toUpperCase());

/** The skill ids a rule is linked to: its Skill ref and its mapping text. */
export const ruleSkills = (r: MasterEntry): string[] => [
  ...new Set([
    ...ids(cellText(r.payload.skill_ref)),
    ...ids(cellText(r.payload.mapping_enforcement)),
  ]),
];

export type MapNode = { id: string; name: string; rules: number };

/** Skills in workflow order: follow each `N · Next` chain from the skills nothing points to. */
export function workflowMap(skills: MasterEntry[], rules: MasterEntry[]): MapNode[] {
  const byId = new Map(skills.map((s) => [s.stable_id.toUpperCase(), s]));
  const pointedTo = new Set(skills.flatMap((s) => ids(cellText(s.payload.next))));
  const order: MasterEntry[] = [];
  const seen = new Set<string>();
  const walk = (s: MasterEntry) => {
    const key = s.stable_id.toUpperCase();
    if (seen.has(key)) return; // a cycle in the Next chain stops here
    seen.add(key);
    order.push(s);
    for (const n of ids(cellText(s.payload.next))) {
      const next = byId.get(n);
      if (next) walk(next);
    }
  };
  skills.filter((s) => !pointedTo.has(s.stable_id.toUpperCase())).forEach(walk);
  skills.forEach(walk); // a loop with no entry point, or skills with no chain
  return order.map((s) => ({
    id: s.stable_id,
    name: cellText(s.payload.skill),
    rules: rules.filter((r) => ruleSkills(r).includes(s.stable_id.toUpperCase())).length,
  }));
}

export function promptsOf(e?: MasterEntry): { system: string; micro: MicroPrompt[] } {
  const p = (e?.payload.prompts ?? {}) as RulePrompts;
  return { system: p.system ?? "", micro: p.micro?.map((m) => ({ ...m })) ?? [] };
}

/** What a save sends: micro prompts with neither a name nor a text are dropped. */
export const cleanPrompts = (system: string, micro: MicroPrompt[] = []): RulePrompts => ({
  system,
  micro: micro.filter((m) => (m.name ?? "").trim() || (m.text ?? "").trim()).slice(0, MAX_MICRO),
});
