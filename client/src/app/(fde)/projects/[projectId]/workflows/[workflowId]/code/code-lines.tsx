"use client";

// The code as plain text lines, with the same shading and markers as the Monaco view. The editor
// itself needs a real browser, so tests render this in its place. Code goes in as text nodes, never HTML.
import { Tooltip } from "antd";

import { findingText, type CodeLine } from "./code-api";

export const PROTECTED_BG = "rgba(128, 128, 128, 0.14)";
export const MONO = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 13 };

export default function CodeLines({ path, lines }: { path: string; lines: CodeLine[] }) {
  return (
    <div role="region" aria-label={`Code of ${path}`} style={{ ...MONO, overflowX: "auto" }}>
      {lines.map((l) => (
        <div
          key={l.n}
          data-line={l.n}
          data-protected={!l.editable}
          style={{ display: "flex", background: l.editable ? undefined : PROTECTED_BG }}
        >
          <span style={{ width: 48, textAlign: "right", paddingRight: 8, opacity: 0.6 }}>
            {l.n}
          </span>
          <span style={{ width: 20 }}>
            {l.findings.length > 0 && (
              <Tooltip title={l.findings.map(findingText).join("\n")}>
                <span role="img" aria-label="Finding" style={{ color: "#d4380d" }}>
                  ●
                </span>
              </Tooltip>
            )}
          </span>
          <pre style={{ margin: 0, whiteSpace: "pre" }}>{l.text}</pre>
        </div>
      ))}
    </div>
  );
}
