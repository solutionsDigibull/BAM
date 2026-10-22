"use client";

// AG Grid Community (MIT). The Macro → Sub-Skill tree is ours (full-width Macro rows that expand), since
// AG Grid's treeData is Enterprise-only. Loaded with next/dynamic + ssr:false (TD §9.1).

import { DownOutlined, RightOutlined, WarningOutlined } from "@ant-design/icons";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type CellEditRequestEvent,
  type ColDef,
  type ColGroupDef,
  type GetRowIdParams,
  type ICellRendererParams,
  type IsFullWidthRowParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type {
  Editor,
  GridColumn,
  GridGroup,
  SubSkillState,
  WorkbenchSubSkill,
  WorkflowState,
} from "@/contracts";

import {
  canEdit,
  cellErrors,
  cellText,
  cellValue,
  STATE_COLOR,
  toEditText,
  type GridRow,
  type MacroRow,
} from "./workbench-api";

ModuleRegistry.registerModules([AllCommunityModule]);

const INVALID_BG = "#fff1f0";

// All AG Grid Community editors; the long-form ones open as a popup.
const CELL_EDITOR: Partial<Record<Editor, string>> = {
  text: "agTextCellEditor",
  number: "agNumberCellEditor",
  boolean: "agCheckboxCellEditor",
  select: "agSelectCellEditor",
  largeText: "agLargeTextCellEditor",
  json: "agLargeTextCellEditor",
  expression: "agLargeTextCellEditor",
  stringList: "agLargeTextCellEditor",
};
const POPUP: ReadonlySet<Editor> = new Set(["largeText", "json", "expression", "stringList"]);

export type EditRequest = { row: WorkbenchSubSkill; column: GridColumn; value: unknown };

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
function columnDefs(
  groups: GridGroup[],
  state: WorkflowState | undefined,
  locked: boolean,
): ColGroupDef<GridRow>[] {
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
      editable: (p) => p.data?.kind === "sub" && canEdit(c, state, locked),
      cellEditor: CELL_EDITOR[c.editor],
      cellEditorPopup: POPUP.has(c.editor),
      // No min/max/precision on the number editor: it would clamp or round silently; parseEdit decides.
      cellEditorParams:
        c.editor === "select"
          ? { values: c.enum ?? [] }
          : POPUP.has(c.editor)
            ? { maxLength: 100_000, rows: 8, cols: 60 }
            : undefined,
      context: c,
      // json / stringList cells hold their edit text, so the popup editor opens on what the cell shows.
      valueGetter: (p) =>
        p.data?.kind === "sub" ? toEditText(c, cellValue(p.data.row, c)) : undefined,
      cellRenderer: c.field_path === "state" ? StateCell : TextCell,
      tooltipValueGetter: (p) => {
        if (p.data?.kind !== "sub") return undefined;
        const errors = cellErrors(p.data.row, c.field_path);
        return errors.length
          ? errors.map((e) => e.message).join("\n")
          : cellText(p.value) || undefined;
      },
      // A class, not cellStyle: AG Grid removes a class rule that stops matching, never a style.
      cellClassRules: {
        "isatvon-cell-invalid": (p) =>
          p.data?.kind === "sub" && cellErrors(p.data.row, c.field_path).length > 0,
      },
    })),
  }));
}

const rowId = (p: GetRowIdParams<GridRow>) => p.data.id;
const fullWidth = (p: IsFullWidthRowParams<GridRow>) => p.rowNode.data?.kind !== "sub";

export default function IsatvonGrid({
  groups,
  rows,
  onToggle,
  onEdit,
  state,
  locked = false,
  height = 640,
}: {
  groups: GridGroup[];
  rows: GridRow[];
  onToggle: (macroId: string) => void;
  onEdit: (edit: EditRequest) => void;
  state: WorkflowState | undefined;
  locked?: boolean;
  height?: number;
}) {
  const columns = useMemo(() => columnDefs(groups, state, locked), [groups, state, locked]);
  // Grid options must keep their identity across renders, or AG Grid redraws every row on each edit.
  const handlers = useRef({ onToggle, onEdit });
  useEffect(() => {
    handlers.current = { onToggle, onEdit };
  }, [onToggle, onEdit]);
  const fullWidthParams = useMemo(
    () => ({ onToggle: (id: string) => handlers.current.onToggle(id) }),
    [],
  );
  // readOnlyEdit: the grid never writes the value itself; the row changes only when the server answers.
  const onCellEditRequest = useCallback((e: CellEditRequestEvent<GridRow>) => {
    const column = e.colDef.context as GridColumn | undefined;
    if (e.data?.kind !== "sub" || !column || e.newValue === e.oldValue) return;
    handlers.current.onEdit({ row: e.data.row, column, value: e.newValue });
  }, []);
  // AG Grid refreshes a cell only when its value changes; a row that gained or lost an error with the
  // same values needs a forced refresh. Only rows whose object changed are touched.
  const grid = useRef<AgGridReact<GridRow>>(null);
  const previous = useRef(new Map<string, GridRow>());
  useEffect(() => {
    const api = grid.current?.api;
    const before = previous.current;
    previous.current = new Map(rows.map((r) => [r.id, r]));
    if (!api || before.size === 0) return; // first load: nothing drawn yet to refresh
    const changed = rows.filter(
      (r) => r.kind === "sub" && before.has(r.id) && before.get(r.id) !== r,
    );
    const nodes = changed.flatMap((r) => api.getRowNode(r.id) ?? []);
    if (nodes.length) api.refreshCells({ rowNodes: nodes, force: true });
  }, [rows]);
  return (
    <div style={{ height }} data-testid="isatvon-grid">
      <style>{`[data-testid="isatvon-grid"] .isatvon-cell-invalid { background-color: ${INVALID_BG}; box-shadow: inset 0 0 0 1px #ff4d4f; }`}</style>
      <AgGridReact<GridRow>
        ref={grid}
        theme={themeQuartz}
        rowData={rows}
        columnDefs={columns}
        getRowId={rowId}
        isFullWidthRow={fullWidth}
        fullWidthCellRenderer={FullWidthRow}
        fullWidthCellRendererParams={fullWidthParams}
        tooltipShowDelay={200}
        suppressCellFocus={false}
        readOnlyEdit
        onCellEditRequest={onCellEditRequest}
        singleClickEdit={false}
        stopEditingWhenCellsLoseFocus
        rowBuffer={20}
        animateRows={false}
      />
    </div>
  );
}
