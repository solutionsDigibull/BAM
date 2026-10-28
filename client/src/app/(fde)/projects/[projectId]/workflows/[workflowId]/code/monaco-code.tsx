"use client";

// Monaco, read only, Python. Loaded only in the browser (`dynamic` with ssr: false in code-screen).
// Monaco comes from the bundled `monaco-editor` package, not a CDN, so no third-party script runs.
// Protected template lines are shaded and each finding gets a glyph with its message on hover.
import { Editor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import { useMemo } from "react";

import { decorations, type CodeLine } from "./code-api";

loader.config({ monaco });

const CSS = `
.sbam-protected-line { background: rgba(128, 128, 128, 0.14); }
.sbam-finding-glyph { background: #d4380d; border-radius: 50%; margin-left: 4px; width: 8px !important; height: 8px !important; margin-top: 6px; }
`;

export default function MonacoCode({ path, lines }: { path: string; lines: CodeLine[] }) {
  const value = useMemo(() => lines.map((l) => l.text).join("\n"), [lines]);
  const marks = useMemo(() => decorations(lines), [lines]);
  return (
    <div role="region" aria-label={`Code of ${path}`}>
      <style>{CSS}</style>
      <Editor
        height={Math.min(Math.max(lines.length, 3) * 19 + 16, 640)}
        path={path}
        language="python"
        value={value}
        theme="vs"
        options={{
          readOnly: true,
          domReadOnly: true,
          minimap: { enabled: false },
          glyphMargin: true,
          scrollBeyondLastLine: false,
          renderLineHighlight: "none",
          contextmenu: false,
        }}
        onMount={(editor) => {
          editor.createDecorationsCollection(
            marks.map((m) => ({
              range: new monaco.Range(m.line, 1, m.line, 1),
              options:
                m.kind === "protected"
                  ? { isWholeLine: true, className: "sbam-protected-line" }
                  : {
                      glyphMarginClassName: "sbam-finding-glyph",
                      glyphMarginHoverMessage: { value: m.hover ?? "" },
                    },
            })),
          );
        }}
      />
    </div>
  );
}
