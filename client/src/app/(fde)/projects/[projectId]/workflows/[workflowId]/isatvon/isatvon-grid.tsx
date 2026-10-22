"use client";

// AG Grid Community (MIT). The Macro → Sub-Skill tree is ours (full-width Macro rows that expand), since
// AG Grid's treeData is Enterprise-only. Loaded with next/dynamic + ssr:false (TD §9.1).

import { DownOutlined, RightOutlined, WarningOutlined } from "@ant-design/icons";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ColGroupDef,
  type ICellRendererParams,
  type IsFullWidthRowParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Tag, Typography } from "antd";
import { useMemo } from "react";

import type { GridGroup, SubSkillState } from "@/contracts";

import {
  cellErrors,
  cellText,
  cellValue,
  STATE_COLOR,
  type GridRow,
  type MacroRow,
} from "./workbench-api";

ModuleRegistry.registerModules([AllCommunityModule]);

const INVALID_BG = "#fff1f0";

// Text nodes only: a skill_name of '<img src=x onerror=…>' is shown as those characters (E8.7-T07).
function TextCell({ value }: { value: unknown }) {
  return <span>{cellText(value)}</span>;
}

function StateCell({ value }: { value: unknown }) {
  const state = typeof value === "string" ? (value as SubSkillState) : undefined;
  return state ? <Tag color={STATE_COLOR[state] ?? "default"}>{state}</Tag> : null;
}

function FullWidthRow({
  data,
  onToggle,
}: ICellRendererParams<GridRow> & { onToggle: (macroId: string) => void }) {
  if (!data) return null;
  if (data.kind === "invalid")
    return (
      <div
        role="row"
        className="isatvon-invalid-row"
        style={{ padding: "8px 16px", background: INVALID_BG }}
      >
        <WarningOutlined style={{ color: "#cf1322", marginRight: 8 }} />
        <Typography.Text type="danger">Invalid data</Typography.Text>
        <Typography.Text type="secondary"> · {data.reason}</Typography.Text>
      </div>
    );
  const m = data as MacroRow;
  return (
    <button
      type="button"
      aria-expanded={m.expanded}
      aria-label={`${m.expanded ? "Collapse" : "Expand"} ${m.macro.macro_category} ${m.macro.name}`}
      onClick={() => onToggle(m.macro.id)}
      style={{
        width: "100%",
        height: "100%",
        textAlign: "left",
        padding: "0 12px",
        border: 0,
        background: "#f8fafc",
        cursor: "pointer",
        fontWeight: 600,
      }}
    >
      {m.expanded ? <DownOutlined /> : <RightOutlined />} <span>{m.macro.macro_category}</span> ·{" "}
      <span>{m.macro.name}</span>
      <Typography.Text type="secondary" style={{ fontWeight: 400, marginLeft: 8 }}>
        {m.count} Sub-Skill{m.count === 1 ? "" : "s"}
      </Typography.Text>
    </button>
  );
}

/** Column groups from the published grid metadata only; a key on a row never becomes a column. */
function columnDefs(groups: GridGroup[]): ColGroupDef<GridRow>[] {
  return groups.map((g) => ({
    headerName: g.header,
    headerTooltip: g.description,
    groupId: g.id,
    marryChildren: true,
    children: (g.children ?? []).map((c): ColDef<GridRow> => ({
      colId: c.field_path,
      headerName: c.header,
      headerTooltip: c.field_path,
      width: c.width,
      pinned: g.id === "metadata" ? "left" : undefined,
      editable: false, // edits arrive in part 3 (#99)
      valueGetter: (p) => (p.data?.kind === "sub" ? cellValue(p.data.row, c) : undefined),
      cellRenderer: c.field_path === "state" ? StateCell : TextCell,
      tooltipValueGetter: (p) => {
        if (p.data?.kind !== "sub") return undefined;
        const errors = cellErrors(p.data.row, c.field_path);
        return errors.length
          ? errors.map((e) => e.message).join("\n")
          : cellText(p.value) || undefined;
      },
      cellStyle: (p) =>
        p.data?.kind === "sub" && cellErrors(p.data.row, c.field_path).length
          ? { background: INVALID_BG, boxShadow: "inset 0 0 0 1px #ff4d4f" }
          : undefined,
    })),
  }));
}

export default function IsatvonGrid({
  groups,
  rows,
  onToggle,
  height = 640,
}: {
  groups: GridGroup[];
  rows: GridRow[];
  onToggle: (macroId: string) => void;
  height?: number;
}) {
  const columns = useMemo(() => columnDefs(groups), [groups]);
  return (
    <div style={{ height }} data-testid="isatvon-grid">
      <AgGridReact<GridRow>
        theme={themeQuartz}
        rowData={rows}
        columnDefs={columns}
        getRowId={(p) => p.data.id}
        isFullWidthRow={(p: IsFullWidthRowParams<GridRow>) => p.rowNode.data?.kind !== "sub"}
        fullWidthCellRenderer={FullWidthRow}
        fullWidthCellRendererParams={{ onToggle }}
        tooltipShowDelay={200}
        suppressCellFocus={false}
        rowBuffer={20}
        animateRows={false}
      />
    </div>
  );
}
