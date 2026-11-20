// The admin grid pattern from the UI team's reference (docs/reference/ui/README.md, "Shared patterns"):
// small upper-case headers, tab count badges, muted meta lines and the bordered table theme.
import { theme, Typography } from "antd";
import type { ReactNode } from "react";

// Colours are CSS variables whose fallbacks are the light values, so light mode is unchanged and the admin
// dark theme (components/shell/portal-shell.css) can restyle every table at once (#785).
export const MUTED = "var(--sbam-muted, #64748b)";
const LINE = "var(--sbam-grid-line, #e5e7eb)";

export const GRID_THEME = {
  components: {
    Table: {
      headerBg: "var(--sbam-grid-head, #f8fafc)",
      headerSplitColor: "transparent",
      borderColor: LINE,
      rowHoverBg: "var(--sbam-grid-hover, #f5f9ff)",
      cellPaddingBlock: 14,
    },
  },
};

export const GRID_STYLE = { border: `1px solid ${LINE}`, borderRadius: 10, overflow: "hidden" };

export const head = (label: string) => (
  <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: 0.3, color: MUTED }}>
    {label.toUpperCase()}
  </span>
);

export function CountBadge({ n, active }: { n: number; active: boolean }) {
  const { token } = theme.useToken(); // follow the app theme, like the tab underline
  return (
    <span
      style={{
        marginLeft: 6,
        padding: "0 7px",
        borderRadius: 10,
        fontSize: 11,
        lineHeight: "18px",
        display: "inline-block",
        background: active ? token.colorPrimaryBg : "var(--sbam-badge-bg, #f1f5f9)",
        color: active ? token.colorPrimary : MUTED,
      }}
    >
      {n}
    </span>
  );
}

export function Meta({ children }: { children: ReactNode }) {
  return <Typography.Text style={{ fontSize: 12, color: MUTED }}>{children}</Typography.Text>;
}
