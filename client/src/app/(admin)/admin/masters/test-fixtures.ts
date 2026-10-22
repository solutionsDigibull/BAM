// Test-only data for the masters screen, following the MSC reference workbook.
import type {
  FiletypeInfo,
  MasterEntry,
  MasterType,
  MasterTypeInfo,
  MasterTypes,
} from "@/contracts";

export const info = (
  master_type: MasterType,
  tab: string,
  columns: [string, string, string][],
): MasterTypeInfo => ({
  master_type,
  tab,
  editable: master_type !== "FILETYPE",
  row_label_prefix: tab,
  columns: columns.map(([header, field, description]) => ({ header, field, description })),
  payload_schema: {},
});

export const LAYOUT: MasterTypes = {
  types: [
    info("ORGANIZATION", "Organization", [["Org Name", "org_name", "Company Name"]]),
    info("RULES", "Rules", [
      ["Domain Rule", "domain_rule", "Live Costing & Margin Price Break Sheet"],
      ["PCB Panelization", "pcb_panelization", "Base PCB Dimensions & Yields"],
      ["Misc", "misc", "Anything else"],
      ["RAG Reference", "rag_reference", "JSON pointer for the RAG"],
    ]),
    info("AGENTS", "Agents", [["Agent Name", "agent_name", "Name of the Agent"]]),
    info("SKILLS", "Skills", [["Skill", "skill", "Skills required for the project"]]),
    info("TOOLING", "Tooling", [["Category", "category", "Add-on category"]]),
    info("FILETYPE", "Filetype", [["File Format", "file_format", "Input Filetype"]]),
  ],
  readme: {
    title: "BuLLM - Admin Masters (BAM) - version 0.1",
    rows: [
      ["1. STRUCTURE", null, null, null],
      ["Tab", "Rows", "What it is", "Source"],
    ],
  },
};

export const entry = (over: Partial<MasterEntry> = {}): MasterEntry => ({
  id: "0190f3a2-7b1c-7d4e-8f00-00000000e001",
  tenant_id: "0190f3a2-7b1c-7d4e-8f00-0000000000aa",
  project_id: null,
  master_type: "RULES",
  stable_id: "RULE-BOM",
  row_label: "Rule #2",
  status: "PROPOSED",
  source_kind: "ADMIN_ENTRY",
  version: 1,
  payload: {
    domain_rule: "RULE-BOM: EMS; Scrub BOM / CBOM. Policy: compare source and costed BOM",
    pcb_panelization: "",
    misc: "Status: Proposed",
  },
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
  ...over,
});

export const filetype = (n: number, tier: number, mvp: boolean): FiletypeInfo => ({
  row_label: `Filetype #${n}`,
  file_format: [".cpp", ".pdf", ".msg"][n - 1] ?? ".txt",
  tier,
  purpose: "Expose native application logic",
  rag_ingestion: "Prebundle a parser",
  example: "https://example.test/file",
  mvp_enabled: mvp,
  loader: mvp ? "pdf-text" : null,
  extensions: [],
  intent_class: "code",
  default_route: "knowledge",
});
