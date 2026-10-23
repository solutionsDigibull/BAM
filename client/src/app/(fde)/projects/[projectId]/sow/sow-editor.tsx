"use client";

// The SOW editor (E6.5): TipTap over the SBAM schema (sow-schema.ts). Section headings are fixed, the
// text under them is editable, citations are numbered chips that open the citation panel. Read-only for an
// approved, superseded or older version. The parent holds the draft; nothing is saved from here.
import { EditorContent, useEditor, type JSONContent } from "@tiptap/react";
import { useEffect, useRef } from "react";

import { sowExtensions } from "./sow-schema";

export type CiteClick = { okfRefId: string; number: number };

/** The document as the server reads it: null attributes and empty `content` arrays carry no meaning, and
 *  TipTap adds or drops them freely, so they must not count as an edit. */
export const canonical = (doc: JSONContent): string =>
  JSON.stringify(doc, (_k, v: unknown) =>
    v === null || (Array.isArray(v) && v.length === 0) ? undefined : v,
  );

const STYLE = `
.sow-editor .ProseMirror { counter-reset: cite; outline: none; padding: 8px 4px; line-height: 1.55; }
.sow-editor .sow-section { margin-bottom: 20px; }
.sow-editor .sow-section > h2 { font-size: 18px; margin: 16px 0 8px; border-bottom: 1px solid #f0f0f0;
  user-select: none; }
.sow-editor .sow-item { border-left: 3px solid #e6f4ff; padding-left: 12px; margin: 10px 0; }
.sow-editor .sow-item > h3 { font-size: 15px; margin: 8px 0 4px; }
.sow-editor .sow-field::before { content: attr(data-label); display: block; font-size: 12px;
  font-weight: 600; color: #8c8c8c; text-transform: uppercase; margin-top: 6px; }
.sow-editor .sow-field .sow-field { margin-left: 12px; }
.sow-editor .sow-record { border-top: 1px dashed #f0f0f0; padding-top: 4px; }
.sow-editor .sow-field p { margin: 2px 0; }
.sow-editor .sow-citations { font-size: 12px; color: #8c8c8c; margin: 4px 0; }
.sow-editor .sow-citations::before { content: "Sources "; }
.sow-editor .sow-cite { counter-increment: cite; cursor: pointer; color: #1677ff; margin-right: 4px; }
.sow-editor .sow-cite::after { content: "[" counter(cite) "]"; }
.sow-editor .sow-cite.ProseMirror-selectednode { outline: 2px solid #91caff; border-radius: 2px; }
`;

export function SowEditor({
  content,
  editable,
  onChange,
  onCite,
}: {
  content: JSONContent;
  editable: boolean;
  /** The edited document, or null when it is back to what was loaded (nothing to save). */
  onChange: (doc: JSONContent | null) => void;
  onCite: (cite: CiteClick) => void;
}) {
  const loaded = useRef<string | null>(null);
  const editor = useEditor({
    extensions: sowExtensions,
    content,
    editable,
    immediatelyRender: false, // rendered in the browser only (dynamic import, ssr: false)
    // TipTap reports updates that change nothing (e.g. a refused edit of a locked heading): compare.
    onUpdate: ({ editor: e }) => {
      const doc = e.getJSON();
      onChange(canonical(doc) === loaded.current ? null : doc);
    },
    editorProps: {
      attributes: { "aria-label": "SOW editor", "data-testid": "sow-editor" },
      handleClickOn: (view, pos, node) => {
        if (node.type.name !== "citation") return false;
        let number = 0;
        view.state.doc.nodesBetween(0, pos + 1, (n) => {
          if (n.type.name === "citation") number += 1;
        });
        onCite({ okfRefId: String(node.attrs.okf_ref_id), number });
        return true;
      },
    },
  });

  // What was loaded, as the editor holds it: the baseline for "unsaved changes". Taken once the editor
  // exists (TipTap's onCreate can come after the first update).
  useEffect(() => {
    if (editor && loaded.current === null) loaded.current = canonical(editor.getJSON());
  }, [editor]);

  useEffect(() => {
    editor?.setEditable(editable);
  }, [editor, editable]);

  return (
    <div className="sow-editor">
      <style>{STYLE}</style>
      <EditorContent editor={editor} />
    </div>
  );
}
