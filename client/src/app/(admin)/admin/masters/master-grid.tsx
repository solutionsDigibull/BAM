"use client";

// AG Grid Community (MIT) for the four approved masters (#783). Loaded with next/dynamic + ssr:false
// (TD §9.1), like the ISATVON grid. The grid never writes a value itself (readOnlyEdit): a cell edit
// is handed up, saved as a new version, and the row changes only when the server answers.

import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  SafetyCertificateFilled,
} from "@ant-design/icons";
import {
  AllCommunityModule,
  ModuleRegistry,
  colorSchemeDark,
  themeQuartz,
  type CellEditRequestEvent,
  type ColDef,
  type GetRowIdParams,
  type ICellRendererParams,
  type SuppressKeyboardEventParams,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { Button, Flex, Popconfirm, Tag, Tooltip } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAdminTheme } from "@/components/shell/admin-theme";
import type { MasterEntry, MasterTypeInfo } from "@/contracts";

import { CHIPS, isUrl, LINK_ONLY, chips, suppressEnter } from "./grid-tools";
import { cellText, STATUS_COLOR } from "./masters-api";

ModuleRegistry.registerModules([AllCommunityModule]);

export type GridActions = {
  onEdit: (e: MasterEntry, field: string, value: string) => void;
  onOpen: (e: MasterEntry) => void;
  onRetire: (e: MasterEntry) => void;
  onDuplicate?: (e: MasterEntry) => void;
};

const light = themeQuartz.withParams({
  headerFontSize: 12,
  headerFontWeight: 600,
  fontSize: 13,
  borderRadius: 10,
});
// The admin dark palette of components/shell/portal-shell.css, so the grid sits in the navy shell.
const dark = light.withPart(colorSchemeDark).withParams({
  backgroundColor: "#101e32",
  foregroundColor: "#e6edf7",
  headerBackgroundColor: "#142540",
  borderColor: "#22395c",
  chromeBackgroundColor: "#142540",
  oddRowBackgroundColor: "#101e32",
  rowHoverColor: "#1a2e4d",
});

// Pinned id and actions columns leave no room on a phone: below this width nothing is pinned.
const NARROW = 640;
function useNarrow() {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const on = () => setNarrow(window.innerWidth < NARROW);
    on();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return narrow;
}

// Enter inside the open editor adds a line (B2); Ctrl/Cmd+Enter saves, which AG Grid does not do itself (B4).
function editKey(p: SuppressKeyboardEventParams<MasterEntry>): boolean {
  if (suppressEnter(p)) return true;
  if (p.editing && p.event.key === "Enter" && (p.event.ctrlKey || p.event.metaKey)) {
    setTimeout(() => p.api.stopEditing(false)); // after this key event, so the editor value is read intact
    return true;
  }
  return false;
}

const CENTER = { display: "flex", alignItems: "center" };

const rowId = (p: GetRowIdParams<MasterEntry>) => p.data.id;

function TextCell({ value }: ICellRendererParams<MasterEntry>) {
  const text = cellText(value);
  return text ? <span>{text}</span> : <span style={{ opacity: 0.5 }}>—</span>;
}

function ChipsCell({ value }: ICellRendererParams<MasterEntry>) {
  const items = chips(cellText(value));
  if (!items.length) return <span style={{ opacity: 0.5 }}>—</span>;
  return (
    <Flex gap={4} align="center" style={{ height: "100%" }}>
      {items.map((c) => (
        <Tag key={c} style={{ margin: 0 }}>
          {c}
        </Tag>
      ))}
    </Flex>
  );
}

// A cell is text, never HTML: only a plain http(s) line becomes a link (E3.10-T08).
function LinkCell({ value }: ICellRendererParams<MasterEntry>) {
  const lines = cellText(value).split("\n").filter(Boolean);
  if (!lines.length) return <span style={{ opacity: 0.5 }}>—</span>;
  return (
    <span>
      {lines.map((l, i) => (
        <span key={`${i}-${l}`}>
          {i > 0 && ", "}
          {isUrl(l) ? (
            <a href={l} target="_blank" rel="noopener noreferrer">
              {l}
            </a>
          ) : (
            l
          )}
        </span>
      ))}
    </span>
  );
}

function IdCell({ data, value }: ICellRendererParams<MasterEntry>) {
  const badge = data?.payload.bullm_badge;
  return (
    <Flex gap={6} align="center" style={{ height: "100%" }}>
      <Tag style={{ margin: 0 }}>{cellText(value) || "—"}</Tag>
      {badge ? (
        <Tooltip title={`BuLLM: ${cellText(badge)}`}>
          <SafetyCertificateFilled
            aria-label={`BuLLM ${cellText(badge)}`}
            style={{ color: "#0a7d5a" }}
          />
        </Tooltip>
      ) : null}
    </Flex>
  );
}

function StatusCell({ value }: ICellRendererParams<MasterEntry>) {
  const status = cellText(value) as keyof typeof STATUS_COLOR;
  return <Tag color={STATUS_COLOR[status]}>{status}</Tag>;
}

type Ctx = GridActions;

function ActionsCell({ data, context }: ICellRendererParams<MasterEntry, unknown, Ctx>) {
  if (!data) return null;
  const label = data.stable_id || data.row_label;
  return (
    <Flex gap={2} align="center" style={{ height: "100%" }}>
      <Tooltip title="Edit in the drawer">
        <Button
          size="small"
          type="text"
          icon={<EditOutlined />}
          aria-label={`Edit ${label}`}
          onClick={() => context.onOpen(data)}
        />
      </Tooltip>
      {context.onDuplicate && (
        <Tooltip title="Duplicate">
          <Button
            size="small"
            type="text"
            icon={<CopyOutlined />}
            aria-label={`Duplicate ${label}`}
            onClick={() => context.onDuplicate?.(data)}
          />
        </Tooltip>
      )}
      <Popconfirm
        title={`Retire ${label}?`}
        description="It leaves the list; every earlier version is kept."
        okText="Retire"
        okButtonProps={{ danger: true }}
        onConfirm={() => context.onRetire(data)}
      >
        <Button
          size="small"
          type="text"
          danger
          icon={<DeleteOutlined />}
          aria-label={`Retire ${label}`}
        />
      </Popconfirm>
    </Flex>
  );
}

function columnDefs(info: MasterTypeInfo, narrow: boolean): ColDef<MasterEntry>[] {
  const cols = info.columns.map((c, i): ColDef<MasterEntry> => {
    const isId = i === 0;
    const linkOnly = LINK_ONLY.has(c.field);
    return {
      colId: c.field,
      headerName: c.header,
      headerTooltip: c.description || undefined,
      width: isId ? 150 : 220,
      pinned: isId && !narrow ? "left" : undefined,
      sortable: true, // B3
      filter: true,
      resizable: true,
      editable: info.editable && !isId && !linkOnly, // the id is the stable key
      cellEditor: "agLargeTextCellEditor",
      cellEditorPopup: true,
      cellEditorParams: { maxLength: 100_000, rows: 8, cols: 50 },
      suppressKeyboardEvent: editKey, // B2, B4
      cellStyle: isId || CHIPS.has(c.field) ? CENTER : undefined,
      cellRenderer: isId ? IdCell : linkOnly ? LinkCell : CHIPS.has(c.field) ? ChipsCell : TextCell,
      valueGetter: (p) => cellText(p.data?.payload[c.field]),
      tooltipValueGetter: (p) => cellText(p.value) || undefined,
    };
  });
  const tail: ColDef<MasterEntry>[] = [
    {
      colId: "status",
      headerName: "Status",
      field: "status",
      width: 150,
      sortable: true,
      filter: true,
      cellStyle: CENTER,
      cellRenderer: StatusCell,
    },
    ...(info.editable
      ? [
          {
            colId: "actions",
            headerName: "Actions",
            width: 130,
            pinned: narrow ? undefined : ("right" as const),
            sortable: false,
            filter: false,
            cellStyle: CENTER,
            cellRenderer: ActionsCell,
          },
        ]
      : []),
  ];
  return [...cols, ...tail];
}

export default function MasterGrid({
  info,
  rows,
  actions,
  singleClick = false,
}: {
  info: MasterTypeInfo;
  rows: MasterEntry[];
  actions: GridActions;
  singleClick?: boolean;
}) {
  // Handlers keep their identity across renders, or AG Grid redraws every row on each edit.
  const handlers = useRef(actions);
  useEffect(() => {
    handlers.current = actions;
  }, [actions]);
  const context = useMemo<Ctx>(
    () => ({
      onEdit: (e, f, v) => handlers.current.onEdit(e, f, v),
      onOpen: (e) => handlers.current.onOpen(e),
      onRetire: (e) => handlers.current.onRetire(e),
      onDuplicate: actions.onDuplicate ? (e) => handlers.current.onDuplicate?.(e) : undefined,
    }),
    [actions.onDuplicate],
  );
  const narrow = useNarrow();
  const columns = useMemo(() => columnDefs(info, narrow), [info, narrow]);
  const onEditRequest = (e: CellEditRequestEvent<MasterEntry>) => {
    const field = e.column.getColId();
    if (e.data && cellText(e.oldValue) !== cellText(e.newValue))
      handlers.current.onEdit(e.data, field, cellText(e.newValue));
  };
  const dark_ = useAdminTheme() === "dark";
  return (
    <div style={{ height: 560, width: "100%" }} data-testid={`master-grid-${info.master_type}`}>
      <AgGridReact<MasterEntry>
        theme={dark_ ? dark : light}
        rowData={rows}
        columnDefs={columns}
        context={context}
        getRowId={rowId}
        readOnlyEdit
        onCellEditRequest={onEditRequest}
        singleClickEdit={singleClick}
        stopEditingWhenCellsLoseFocus
        tooltipShowDelay={400}
        enableCellTextSelection
        ensureDomOrder
      />
    </div>
  );
}
