"use client";

// The gate-expression editor (E8.8, TD §9.3): CodeMirror 6 with the SBAM gate grammar highlighted,
// parsed live (200 ms after typing stops) with the error marked where it is, unknown names warned
// and the scope's names offered as completions. Parse only: nothing is ever evaluated here, and the
// server's validation_errors stay the authority. Client-only (dynamic import, ssr: false).

import { autocompletion, type CompletionContext } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { StreamLanguage, defaultHighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { linter, lintGutter } from "@codemirror/lint";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, placeholder } from "@codemirror/view";
import { useEffect, useRef } from "react";

import { KEYWORDS, gateDiagnostics } from "./gate-expr";

export const LINT_DELAY_MS = 200;

// Highlighting only; the parser in gate-expr.ts decides what is valid.
const gateLanguage = StreamLanguage.define({
  token(stream) {
    if (stream.eatSpace()) return null;
    if (stream.match(/^'[^'\n]*'|^"[^"\n]*"/)) return "string";
    if (stream.match(/^-?\d+(\.\d+)?/)) return "number";
    if (stream.match(/^(==|!=|<=|>=|<|>)/)) return "operator";
    const word = stream.match(/^[A-Za-z_][A-Za-z0-9_]*/) as RegExpMatchArray | null;
    if (word) return KEYWORDS.has(word[0]) ? "keyword" : "variableName";
    stream.next();
    return null;
  },
});

export type GateEditorProps = {
  value: string;
  scope: readonly string[];
  readOnly?: boolean;
  label: string;
  onChange: (value: string) => void;
};

export default function GateEditor({ value, scope, readOnly, label, onChange }: GateEditorProps) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const latest = useRef({ scope, onChange });
  useEffect(() => {
    latest.current = { scope, onChange };
  }, [scope, onChange]);

  useEffect(() => {
    if (!host.current) return;
    const complete = (ctx: CompletionContext) => {
      const word = ctx.matchBefore(/[A-Za-z_][A-Za-z0-9_]*/);
      if (!word && !ctx.explicit) return null;
      const options = [
        ...latest.current.scope.map((n) => ({ label: n, type: "variable" })),
        ...[...KEYWORDS].map((k) => ({ label: k, type: "keyword" })),
      ];
      return { from: word?.from ?? ctx.pos, options };
    };
    view.current = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          gateLanguage,
          syntaxHighlighting(defaultHighlightStyle),
          linter((v) => gateDiagnostics(v.state.doc.toString(), latest.current.scope), {
            delay: LINT_DELAY_MS,
          }),
          lintGutter(),
          autocompletion({ override: [complete] }),
          placeholder("e.g. retries >= max_iterations OR status == 'FAILED'"),
          EditorState.readOnly.of(!!readOnly),
          EditorView.editable.of(!readOnly),
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) latest.current.onChange(u.state.doc.toString());
          }),
        ],
      }),
    });
    return () => view.current?.destroy();
    // created once per mount; `value` changes from outside are applied below
  }, [readOnly, label]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value)
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  return (
    <div
      ref={host}
      data-testid={`gate-editor-${label}`}
      style={{ border: "1px solid #d9d9d9", borderRadius: 6, minHeight: 36 }}
    />
  );
}
