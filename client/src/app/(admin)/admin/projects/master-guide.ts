// Project-context guidance from the admin reference workbook "BuLLM - Manufacturing Masters v0.2"
// (README §7 guideline layer per master, §10 intake contract, build rule 6; Organization "Domain / Applies to").

export type MasterGuide = { master: string; docs: string; intent: string; route: string };

export const MASTER_GUIDE: MasterGuide[] = [
  {
    master: "Organization",
    docs: "Org profile, certifications, systems and device inventory",
    intent: "Organization context (who / where / access)",
    route:
      "Docs about the company, sites, certs, systems, customers. Tenant scope and graph root node.",
  },
  {
    master: "Rules",
    docs: "Costing standards, DFM / sourcing rules, SOP policy docs",
    intent: "Rule / policy",
    route: "Policy, formula, validation and DFM docs. Deterministic policy and stop conditions.",
  },
  {
    master: "Agents",
    docs: "Agent specs, OpenAPI / MCP specs, scripts, prompts",
    intent: "Agent / tool definition",
    route: "Which agent may read what and what it returns.",
  },
  {
    master: "Skills",
    docs: "RFQ, BOM, supplier quote, FX, stock files, report templates",
    intent: "Instruction / workflow step",
    route:
      "SOPs, RFQ / BOM / quote / stock inputs and templates. Maps loaded content to a workflow stage.",
  },
];

export const INTAKE_RULES = [
  "The file type selects the parser, not the master it lands in. Mixed files are split by content.",
  "Store pointers (S3 / Postgres) to source files; never duplicate the files themselves.",
  "Low confidence or missing keys go to human review.",
];

/** Context notes skeleton in the workbook's own terms; the admin replaces the examples. */
export const CONTEXT_NOTES_TEMPLATE = `## Domain
Electronics / EMS — e.g. RFQ costing for high-reliability PCBA

## Applies to
RFQ ID + revision + scenario + FG

## Organization
Org unit code (e.g. ORG-EMS), sites, certifications (e.g. ISO 9001, AS9100D)

## Rules
Policies that apply (e.g. RULE-BOM, RULE-OFFER) and their stop conditions

## Source references
RFQ / BOM / supplier offer / FX / stock locations — pointers only, no file copies

## Review gates
Who approves (estimator, QA) and when work halts for review
`;
