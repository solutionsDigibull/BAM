// Shapes of src/workflow.generated.ts. The generator fills them from the Workflow's skills and from
// the input and output schemas of its tools; the screens never hard-code a workflow.
export type FieldType = "string" | "integer" | "number" | "boolean" | "array" | "object";
export type Widget = "text" | "textarea" | "number" | "checkbox" | "select" | "file" | "json";
export type OutputView = "grid" | "editor" | "kv";

export interface Field {
  name: string;
  label: string;
  type: FieldType;
  required: boolean;
  widget: Widget;
  enum?: (string | number)[];
  description?: string;
}
export interface OutField {
  name: string;
  label: string;
  type: FieldType;
  view: OutputView;
  description?: string;
}
export interface Step {
  name: string;
  note: string;
  mode: "human" | "auto";
}
export interface SubSkill {
  id: string;
  name: string;
  summary: string;
  actionTool: string;
  checkTool: string;
  steps: Step[];
  inputs: Field[];
  outputs: OutField[];
}
export interface Skill {
  n: number;
  id: string;
  name: string;
  icon: string;
  hook: string;
  subs: SubSkill[];
  review: { name: string; note: string };
}
export interface WorkflowSpec {
  name: string;
  serverName: string;
  version: string;
  skills: Skill[];
}
