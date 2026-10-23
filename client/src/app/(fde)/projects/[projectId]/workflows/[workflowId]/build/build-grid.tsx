"use client";

// AG Grid Community (MIT): Sub-Skills × gates, virtualised so 50 × 19 stays light (E12.6-T06).
// Loaded with next/dynamic + ssr:false (TD §9.1). Cells render text and icons only.

import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ColGroupDef,
  type GetRowIdParams,
  type ICellRendererParams,
  type RowClassRules,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Button, Tag } from "antd";
import { useEffect, useMemo, useRef } from "react";

import type { BuildRow, GateName, GateStatus } from "@/contracts";

import { canRetry, GATES, gateStatus, MAX_ATTEMPT, type GateFamily } from "./build-api";

ModuleRegistry.registerModules([AllCommunityModule]);

export type GridActions = {
  onFindings: (row: BuildRow, gate: GateName) => void;
  onRetry: (row: BuildRow) => void;
};

// Plain glyphs styled by CSS, not icon components: ~450 gate cells are on screen at once (T06).
const STATUS: Record<GateStatus, { glyph: string; text: string }> = {
  PASSED: { glyph: "✓", text: "Passed" },
  FAILED: { glyph: "✕", text: "Failed" },
  RUNNING: { glyph: "", text: "Running" }, // a CSS spinner
  SKIPPED: { glyph: "–", text: "Skipped" },
  PENDING: { glyph: "•", text: "Pending" },
};
const GRID_CSS = `
[data-testid="build-grid"] .build-row-review { background-color: #fffbeb; }
[data-testid="build-grid"] .gate { display: inline-flex; width: 20px; height: 20px; border-radius: 50%;
  align-items: center; justify-content: center; font-size: 12px; font-weight: 700; line-height: 1; }
[data-testid="build-grid"] .gate-PASSED { background: #dcfce7; color: #15803d; }
[data-testid="build-grid"] .gate-FAILED { background: #fee2e2; color: #b91c1c; }
[data-testid="build-grid"] .gate-SKIPPED { color: #94a3b8; }
[data-testid="build-grid"] .gate-PENDING { color: #cbd5e1; font-size: 18px; }
[data-testid="build-grid"] .gate-RUNNING { box-sizing: border-box; width: 16px; height: 16px;
  border: 2px solid #bfdbfe; border-top-color: #2563eb; animation: gate-spin 0.8s linear infinite; }
[data-testid="build-grid"] button.gate { border: 0; cursor: pointer; padding: 0; }
[data-testid="build-grid"] button.gate:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
@keyframes gate-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { [data-testid="build-grid"] .gate-RUNNING { animation: none; } }
`;

const STATE_COLOR: Record<string, string> = {
  BUILDING: "processing",
  CORRECTING: "processing",
  TESTING: "processing",
  NEEDS_FDE_REVIEW: "warning",
  TESTING_BLOCKED: "warning",
  BUILD_ERROR: "error",
  DEPENDENCY_FAILED: "error",
  CERTIFIED: "purple",
  FROZEN: "purple",
  VALID: "green",
};

type Ctx = { actions: GridActions };

function GateCell({
  data,
  colDef,
  context,
}: ICellRendererParams<BuildRow, unknown, Ctx> & { colDef?: ColDef<BuildRow> }) {
  if (!data || !colDef?.colId) return null;
  const gate = colDef.colId as GateName;
  const status = gateStatus(data, gate);
  const { glyph, text } = STATUS[status];
  const label = `${data.skill_id} ${colDef.headerName ?? gate}: ${text}`;
  if (status !== "FAILED")
    return (
      <span role="img" aria-label={label} title={text} className={`gate gate-${status}`}>
        {glyph}
      </span>
    );
  return (
    <button
      type="button"
      aria-label={`Findings for ${label}`}
      title="Show findings"
      className="gate gate-FAILED"
      onClick={() => context.actions.onFindings(data, gate)}
    >
      {glyph}
    </button>
  );
}

function StateCell({ data }: ICellRendererParams<BuildRow>) {
  return data ? <Tag color={STATE_COLOR[data.state] ?? "default"}>{data.state}</Tag> : null;
}

function RetryCell({ data, context }: ICellRendererParams<BuildRow, unknown, Ctx>) {
  if (!data) return null;
  const allowed = canRetry(data.state);
  return (
    // a native title, not a Tooltip per row; the wrapper keeps it on a disabled button
    <span title={allowed ? "Retry after your fix" : "Retry is offered in NEEDS_FDE_REVIEW only"}>
      <Button
        size="small"
        disabled={!allowed}
        aria-label={`Retry ${data.skill_id}`}
        onClick={() => context.actions.onRetry(data)}
      >
        Retry
      </Button>
    </span>
  );
}

const FAMILIES: GateFamily[] = ["Contract", "AST", "Static", "Tests"];

const COLUMNS: (ColDef<BuildRow> | ColGroupDef<BuildRow>)[] = [
  {
    headerName: "Sub-Skill",
    children: [
      { colId: "skill_id", field: "skill_id", headerName: "Skill ID", width: 170, pinned: "left" },
      { colId: "skill_name", field: "skill_name", headerName: "Name", width: 180, pinned: "left" },
    ],
  },
  {
    // its own group: a group split across pinned and scrolling columns shows its header twice
    headerName: "Build",
    children: [
      { colId: "state", field: "state", headerName: "State", width: 170, cellRenderer: StateCell },
      {
        colId: "attempt",
        headerName: "Attempt",
        headerTooltip: `Correction attempt, 0–${MAX_ATTEMPT}`,
        width: 96,
        valueGetter: (p) => `${(p.data?.attempt ?? 0) + 1} / ${MAX_ATTEMPT + 1}`,
      },
    ],
  },
  ...FAMILIES.map((family): ColGroupDef<BuildRow> => ({
    headerName: family === "AST" ? "AST gate" : family === "Static" ? "Static gate" : family,
    children: GATES.filter((g) => g.family === family).map((g): ColDef<BuildRow> => ({
      colId: g.name,
      headerName: g.label,
      headerTooltip: g.name,
      width: 92,
      cellRenderer: GateCell,
      cellStyle: { display: "flex", justifyContent: "center", alignItems: "center" },
      // the status, so AG Grid redraws a cell when it changes and leaves the others alone
      valueGetter: (p) => (p.data ? gateStatus(p.data, g.name) : undefined),
    })),
  })),
  // the state is the value, so the button follows it when a poll changes the row
  {
    colId: "retry",
    headerName: "",
    width: 92,
    pinned: "right",
    valueGetter: (p) => p.data?.state,
    cellRenderer: RetryCell,
  },
];

const rowId = (p: GetRowIdParams<BuildRow>) => p.data.sub_skill_id;
// E12.6-T06 timing: the container is committed before AG Grid builds anything; the grid marks the end
const markMounted = (el: HTMLDivElement | null) => {
  if (el) performance.mark("build-grid:mounted");
};
const ROW_CLASS: RowClassRules<BuildRow> = {
  "build-row-review": (p) => p.data?.state === "NEEDS_FDE_REVIEW",
};

export default function BuildGrid({
  rows,
  actions,
  height = 560,
}: {
  rows: BuildRow[];
  actions: GridActions;
  height?: number;
}) {
  // The grid context keeps its identity; the handlers behind it are always the latest.
  const latest = useRef(actions);
  useEffect(() => {
    latest.current = actions;
  }, [actions]);
  const context = useMemo<Ctx>(
    () => ({
      actions: {
        onFindings: (r, g) => latest.current.onFindings(r, g),
        onRetry: (r) => latest.current.onRetry(r),
      },
    }),
    [],
  );
  return (
    <div style={{ height }} data-testid="build-grid" ref={markMounted}>
      <style>{GRID_CSS}</style>
      <AgGridReact<BuildRow>
        theme={themeQuartz}
        rowData={rows}
        columnDefs={COLUMNS}
        getRowId={rowId}
        rowClassRules={ROW_CLASS}
        context={context}
        suppressCellFocus
        // ~24 narrow columns: keep them all in the DOM (rows stay virtualised), so a gate scrolled
        // out of view is still there to click and to read for a screen reader
        suppressColumnVirtualisation
        animateRows={false}
        // render timing for E12.6-T06, readable from the browser's performance timeline
        onFirstDataRendered={() => performance.mark("build-grid:rendered")}
        rowBuffer={2}
      />
    </div>
  );
}
