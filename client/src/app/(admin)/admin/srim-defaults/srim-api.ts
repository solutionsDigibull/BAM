// Shared bits of the SRIM runtime defaults screen (E9.1b, O-19): the API resource, the six blocks the form
// edits and the copy. Shapes are the generated contracts; the API stays authoritative for validation.
import type { SrimRuntimeDefaults } from "@/contracts";

// `GET /admin/srim-defaults` read as record "srim-defaults" of "admin", so the data provider keeps its ETag.
export const RESOURCE = "admin";
export const ID = "srim-defaults";
export const PATH = `${RESOURCE}/${ID}`;
export const MAX_LEN = 200; // ShortText in the contract

export type Block = keyof SrimRuntimeDefaults;
export type FieldSpec = { block: Block; name: string; label: string; hint: string };

// Order and wording follow TD §11.1. `persistence.table` is not here: it is derived per Sub-Skill.
export const FIELDS: FieldSpec[] = [
  { block: "orchestration", name: "router", label: "Router", hint: "Dynamic master router" },
  { block: "orchestration", name: "trigger", label: "Trigger", hint: "How a skill is started" },
  { block: "execution", name: "engine", label: "Engine", hint: "Execution engine" },
  { block: "gateway", name: "route_via", label: "Route via", hint: "Gateway or reverse proxy" },
  { block: "persistence", name: "database", label: "Database", hint: "State and config store" },
  { block: "persistence", name: "action", label: "Action", hint: "How SQL is executed" },
  { block: "audit", name: "telemetry", label: "Telemetry", hint: "Where audit logs go" },
  { block: "ui", name: "target", label: "UI target", hint: "Canvas the widgets render in" },
  { block: "ui", name: "method", label: "UI method", hint: "How widgets are embedded" },
];
export const BLOCKS: Block[] = [
  "orchestration",
  "execution",
  "gateway",
  "persistence",
  "audit",
  "ui",
];
export const blockTitle = (b: Block) => b.charAt(0).toUpperCase() + b.slice(1);

export const DEFAULTS_MESSAGE = "Using the ISA14 defaults";
export const DEFAULTS_HELP =
  "This tenant has not set its own values, so every manifest uses the ISA14 sample values.";
export const STALE_MESSAGE = "These values were changed by someone else";
export const NOTE =
  "Applies to manifests built from now on. A manifest that is already built keeps the values it was built with. The persistence table is not set here: it comes from each Sub-Skill's own tables.";
