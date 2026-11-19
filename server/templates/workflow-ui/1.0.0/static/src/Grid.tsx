// Tables use ag-grid, the same grid library as BAM_UI (client-side, no remote data).
import { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry, themeQuartz, colorSchemeDark, colorSchemeLight } from "ag-grid-community";

ModuleRegistry.registerModules([AllCommunityModule]);

export function DataGrid({ dark, rows }: { dark: boolean; rows: Record<string, unknown>[] }) {
  const theme = useMemo(
    () => themeQuartz.withPart(dark ? colorSchemeDark : colorSchemeLight).withParams({
      fontFamily: "inherit", fontSize: 13, headerFontSize: 12, headerFontWeight: 600,
      rowVerticalPaddingScale: 1.2, wrapperBorderRadius: 8, cellHorizontalPadding: 8,
    }),
    [dark],
  );
  const columns = useMemo(() => {
    const keys: string[] = [];
    for (const row of rows) for (const key of Object.keys(row)) if (!keys.includes(key)) keys.push(key);
    return keys.map(key => ({ field: key, headerName: key.replace(/_/g, " "), flex: 1, minWidth: 96, sortable: true, filter: false, resizable: false }));
  }, [rows]);
  const data = useMemo(
    () => rows.map(row => Object.fromEntries(Object.entries(row).map(([k, v]) => [k, typeof v === "object" && v !== null ? JSON.stringify(v) : v]))),
    [rows],
  );
  return (
    <div className="bom-grid">
      <AgGridReact theme={theme} columnDefs={columns} rowData={data} domLayout="autoHeight" headerHeight={30} rowHeight={28} suppressCellFocus />
    </div>
  );
}
