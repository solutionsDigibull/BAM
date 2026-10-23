import { Tag } from "antd";

// ponytail: hand-listed from TD §14 until #23/#33 generate the state enums into src/contracts;
// then type `status` with the generated union and import the list instead.
export const WORKFLOW_STATES = [
  "CREATED",
  "ISATVON_EXTRACTING",
  "ISATVON_REVIEW",
  "BUILDING",
  "CERTIFYING",
  "CERTIFIED",
  "FROZEN",
  "EXTRACTION_FAILED",
  "NEEDS_FDE_REVIEW",
  "WORKFLOW_VALIDATION_FAILED",
  "CANCELLED",
] as const;

export const PIPELINE_STATES = [
  "IDLE",
  "INGESTING",
  "KNOWLEDGE_BUILDING",
  "SOW_GENERATING",
  "SOW_REVIEW",
  "SOW_APPROVED",
  "WORKFLOW_CREATED",
  "INGESTION_FAILED",
  "KNOWLEDGE_FAILED",
  "SOW_GENERATION_FAILED",
] as const;

const COLOR: Record<string, string> = {
  CREATED: "default",
  IDLE: "default",
  CANCELLED: "default",
  ISATVON_EXTRACTING: "processing",
  BUILDING: "processing",
  CERTIFYING: "processing",
  INGESTING: "processing",
  KNOWLEDGE_BUILDING: "processing",
  SOW_GENERATING: "processing",
  ISATVON_REVIEW: "warning",
  NEEDS_FDE_REVIEW: "warning",
  SOW_REVIEW: "warning",
  CERTIFIED: "success",
  SOW_APPROVED: "success",
  WORKFLOW_CREATED: "success",
  FROZEN: "purple",
  EXTRACTION_FAILED: "error",
  WORKFLOW_VALIDATION_FAILED: "error",
  INGESTION_FAILED: "error",
  KNOWLEDGE_FAILED: "error",
  SOW_GENERATION_FAILED: "error",
  // knowledge versions and their documents (E5.6)
  READY: "success",
  FAILED: "error",
  OUTDATED: "default",
  COVERED: "success",
  CHUNKS_ONLY: "warning",
  ADDED: "processing",
  // SOW versions (E6.5 / E7.3)
  GENERATED: "processing",
  IN_REVIEW: "warning",
  APPROVED: "success",
  SUPERSEDED: "default",
  GENERATION_FAILED: "error",
};

const label = (status: string) =>
  status
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase())
    .replace(/\b(sow|fde|isatvon)\b/gi, (w) => w.toUpperCase());

export function StatusBadge({ status }: { status: string }) {
  const color = COLOR[status];
  if (!color) return <Tag data-status="UNKNOWN">UNKNOWN</Tag>;
  return (
    <Tag color={color} data-status={status}>
      {label(status)}
    </Tag>
  );
}
