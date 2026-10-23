"use client";

// AG Grid Community (MIT). The Macro → Sub-Skill tree is ours (full-width Macro rows that expand), since
// AG Grid's treeData is Enterprise-only. Loaded with next/dynamic + ssr:false (TD §9.1).

import {
  DeleteOutlined,
  DownOutlined,
  ReloadOutlined,
  RightOutlined,
  WarningOutlined,
} from "@ant-design/icons";
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
import { Button, Flex, Tag, Tooltip, Typography } from "antd";
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
  isEditable,
  STATE_COLOR,
  toEditText,
  valueAt,
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
export type RowAction =
  | { action: "regenerate"; kind: "macro" | "sub"; id: string; label: string }
  | { action: "delete"; kind: "sub"; id: string; label: string; row: WorkbenchSubSkill };
type OnAction = (a: RowAction) => void;
type GridContext = { onAction: OnAction };

// Row actions are UI chrome, not data: this column is fixed, never built from a row's keys (T06).
// The handler comes through the grid context (stable), so the column defs never hold a callback.
function ActionsCell({ data, context }: ICellRendererParams<GridRow, unknown, GridContext>) {
  if (data?.kind !== "sub") return null;
  const onAction = context.onAction;
  const label = String(valueAt(data.row.sub_skill, "skill_metadata.skill_id"));
  return (
    <Flex gap={4} align="center" style={{ height: "100%" }}>
      <Tooltip title="Regenerate with a hint">
        <Button
          size="small"
          type="text"
          icon={<ReloadOutlined />}
          aria-label={`Regenerate ${label}`}
          onClick={() => onAction({ action: "regenerate", kind: "sub", id: data.id, label })}
        />
      </Tooltip>
      <Tooltip title="Delete">
        <Button
          size="small"
          type="text"
          danger
          icon={<DeleteOutlined />}
          aria-label={`Delete ${label}`}
          onClick={() =>
            onAction({ action: "delete", kind: "sub", id: data.id, label, row: data.row })
          }
        />
      </Tooltip>
    </Flex>
  );
}

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
  onAction,
}: ICellRendererParams<GridRow> & { onToggle: (macroId: string) => void; onAction: OnAction }) {
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
  const label = `${m.macro.macro_category} ${m.macro.name}`;
  return (
    <Flex align="center" style={{ height: "100%", background: "#f8fafc", paddingRight: 12 }}>
      <button
        type="button"
        aria-expanded={m.expanded}
        aria-label={`${m.expanded ? "Collapse" : "Expand"} ${label}`}
        onClick={() => onToggle(m.macro.id)}
        style={{
          flex: 1,
          height: "100%",
          textAlign: "left",
          padding: "0 12px",
          border: 0,
          background: "transparent",
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
      {m.actions && (
        <Button
          size="small"
          icon={<ReloadOutlined />}
          aria-label={`Regenerate ${label}`}
          onClick={() => onAction({ action: "regenerate", kind: "macro", id: m.macro.id, label })}
        >
          Regenerate
        </Button>
      )}
    </Flex>
  );
}

/** Column groups from the published grid metadata only; a key on a row never becomes a column. */
function columnDefs(
  groups: GridGroup[],
  state: WorkflowState | undefined,
  locked: boolean,
): ColGroupDef<GridRow>[] {
  const actions: ColGroupDef<GridRow> = {
    headerName: "",
    groupId: "actions",
    children: [
      {
        colId: "actions",
        headerName: "Actions",
        width: 96,
        pinned: "right",
        sortable: false,
        cellRenderer: ActionsCell,
      },
    ],
  };
  const data = groups.map((g): ColGroupDef<GridRow> => ({
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
  return isEditable(state) && !locked ? [...data, actions] : data;
}

const rowId = (p: GetRowIdParams<GridRow>) => p.data.id;
const fullWidth = (p: IsFullWidthRowParams<GridRow>) => p.rowNode.data?.kind !== "sub";

export default function IsatvonGrid({
  groups,
  rows,
  onToggle,
  onEdit,
  onAction,
  state,
  locked = false,
  height = 640,
}: {
  groups: GridGroup[];
  rows: GridRow[];
  onToggle: (macroId: string) => void;
  onEdit: (edit: EditRequest) => void;
  onAction: OnAction;
  state: WorkflowState | undefined;
  locked?: boolean;
  height?: number;
}) {
  // Grid options must keep their identity across renders, or AG Grid redraws every row on each edit.
  const handlers = useRef({ onToggle, onEdit, onAction });
  useEffect(() => {
    handlers.current = { onToggle, onEdit, onAction };
  }, [onToggle, onEdit, onAction]);
  const columns = useMemo(() => columnDefs(groups, state, locked), [groups, state, locked]);
  const context = useMemo<GridContext>(
    () => ({ onAction: (a) => handlers.current.onAction(a) }),
    [],
  );
  const fullWidthParams = useMemo(
    () => ({
      onToggle: (id: string) => handlers.current.onToggle(id),
      onAction: (a: RowAction) => handlers.current.onAction(a),
    }),
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
        context={context}
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
