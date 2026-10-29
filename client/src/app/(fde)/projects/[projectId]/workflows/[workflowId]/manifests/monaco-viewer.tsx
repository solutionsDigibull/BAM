"use client";

// Monaco, read only, for a manifest (JSON) or the schema.sql (SQL), or a diff of two manifests.
// Loaded only in the browser (`dynamic` with ssr: false in manifest-screen). Monaco is the bundled
// `monaco-editor` package, as in the code view; its editor and JSON workers come from the bundle too,
// so the JSON Schema hover runs off the main thread. Content goes in only as a Monaco model's text,
// never as HTML.
import { DiffEditor, Editor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import { useEffect } from "react";

loader.config({ monaco });

if (typeof self !== "undefined") {
  (self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
    getWorker: (_id: string, label: string) =>
      label === "json"
        ? new Worker(new URL("monaco-editor/language/json/json.worker", import.meta.url), {
            type: "module",
          })
        : new Worker(new URL("monaco-editor/editor/editor.worker", import.meta.url), {
            type: "module",
          }),
  };
}

const OPTIONS: monaco.editor.IStandaloneEditorConstructionOptions = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false }, // a 2 MB manifest scrolls without painting a second copy
  scrollBeyondLastLine: false,
  contextmenu: false,
  folding: true,
  renderLineHighlight: "none",
};
const HEIGHT = 640;

export type ViewerProps = {
  path: string; // the model's URI path, e.g. "manifest.json"
  language: "json" | "sql";
  value: string;
  /** A JSON Schema for hover (JSON only). */
  schema?: { uri: string; schema: unknown };
  /** Set: a side-by-side diff, `original` left and `value` right. */
  original?: string;
};

function useSchema(path: string, schema: ViewerProps["schema"]) {
  useEffect(() => {
    if (!schema) return;
    monaco.json.jsonDefaults.setDiagnosticsOptions({
      validate: false, // a draft may hold nulls the frozen schema forbids; hover only
      enableSchemaRequest: false,
      schemas: [{ uri: schema.uri, fileMatch: [path], schema: schema.schema as object }],
    });
  }, [path, schema]);
}

export default function MonacoViewer({ path, language, value, schema, original }: ViewerProps) {
  useSchema(path, schema);
  return (
    <div
      role="region"
      aria-label={original === undefined ? `${path} (read only)` : `Diff of ${path}`}
    >
      {original === undefined ? (
        <Editor
          height={HEIGHT}
          path={path}
          language={language}
          value={value}
          theme="vs"
          options={OPTIONS}
        />
      ) : (
        <DiffEditor
          height={HEIGHT}
          language={language}
          original={original}
          modified={value}
          originalModelPath={`original/${path}`}
          modifiedModelPath={path}
          theme="vs"
          options={{ ...OPTIONS, renderSideBySide: true, originalEditable: false }}
        />
      )}
    </div>
  );
}
