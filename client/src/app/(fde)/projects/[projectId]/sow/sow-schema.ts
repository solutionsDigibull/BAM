// The SOW editor's schema (E6.5): the SBAM TipTap shape of ADR 0019, so what the editor saves is what the
// API converts to a SowDocument. Only these nodes exist, so pasted <script>, <iframe>, <style> or <img> have
// nowhere to go and are dropped (E6.5-T07). Links are off too: a SOW cites sources, it does not link out.
//
//   doc > sowSection{section_id} x9 > heading(2) + sowField{field} | sowItem{id,module_id} | sowCitations
//   sowItem > heading(3) + sowField + sowCitations;  sowField > paragraph | list | sowField | sowRecord
//   sowCitations > citation{okf_ref_id, document_version_id, location}   (inline atoms, numbered by CSS)
//
// `templateLock` refuses any change to the template: the nine sections (ids, order, headings), item ids and
// module ids. Everything inside them stays editable (E6.5-T04).
import DOMPurify from "dompurify";
import { Extension, Node, type AnyExtension, type JSONContent } from "@tiptap/react";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import type { Node as PmNode } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";

export const SECTION_IDS = [
  "overview",
  "users_authority",
  "scope",
  "modules",
  "features",
  "data_integrations",
  "rules_constraints",
  "deliverables_acceptance",
  "assumptions_open_points",
] as const;

const label = (field: string) => field.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const SowDoc = Node.create({ name: "doc", topNode: true, content: "sowSection+" });

const SowSection = Node.create({
  name: "sowSection",
  content: "heading (sowField | sowItem | sowCitations)*",
  defining: true,
  isolating: true,
  addAttributes: () => ({ section_id: { default: null } }),
  parseHTML: () => [{ tag: "section[data-section]" }],
  renderHTML: ({ node }) => [
    "section",
    { "data-section": node.attrs.section_id, class: "sow-section" },
    0,
  ],
});

const SowField = Node.create({
  name: "sowField",
  content: "(paragraph | bulletList | orderedList | sowField | sowRecord)*",
  defining: true,
  addAttributes: () => ({ field: { default: null } }),
  parseHTML: () => [{ tag: "div[data-field]" }],
  renderHTML: ({ node }) => [
    "div",
    {
      "data-field": node.attrs.field,
      "data-label": label(String(node.attrs.field ?? "")),
      class: "sow-field",
    },
    0,
  ],
});

const SowRecord = Node.create({
  name: "sowRecord",
  content: "sowField*",
  parseHTML: () => [{ tag: "div[data-record]" }],
  renderHTML: () => ["div", { "data-record": "", class: "sow-record" }, 0],
});

const SowItem = Node.create({
  name: "sowItem",
  content: "heading (sowField | sowCitations)*",
  defining: true,
  isolating: true,
  addAttributes: () => ({ id: { default: null }, module_id: { default: null } }),
  parseHTML: () => [{ tag: "article[data-item]" }],
  renderHTML: ({ node }) => ["article", { "data-item": node.attrs.id, class: "sow-item" }, 0],
});

const SowCitations = Node.create({
  name: "sowCitations",
  content: "citation*",
  parseHTML: () => [{ tag: "p[data-citations]" }],
  renderHTML: () => ["p", { "data-citations": "", class: "sow-citations" }, 0],
});

const Citation = Node.create({
  name: "citation",
  inline: true,
  group: "inline",
  atom: true,
  selectable: true,
  addAttributes: () => ({
    kind: { default: null }, // "fde" for a citation the FDE wrote; absent for an OKF source
    okf_ref_id: { default: null },
    document_version_id: { default: null },
    location: { default: {} },
    author: { default: null },
    date: { default: null },
    note: { default: null },
  }),
  parseHTML: () => [{ tag: "span[data-cite]" }],
  renderHTML: ({ node }) => [
    "span",
    {
      "data-cite": node.attrs.kind === "fde" ? "fde" : node.attrs.okf_ref_id,
      class: node.attrs.kind === "fde" ? "sow-cite sow-cite-fde" : "sow-cite",
      role: "button",
      tabindex: "0",
    },
  ],
});

/** What the template fixes: section ids and headings in order, then every item's id and module id. */
export function skeleton(doc: PmNode): string {
  const parts: string[] = [];
  doc.forEach((section) => {
    parts.push(`S:${String(section.attrs.section_id)}:${section.firstChild?.textContent ?? ""}`);
    section.forEach((child) => {
      if (child.type.name === "sowItem") {
        parts.push(`I:${String(child.attrs.id)}:${String(child.attrs.module_id)}`);
      }
    });
  });
  return parts.join("|");
}

export const templateLockKey = new PluginKey("sowTemplateLock");

const TemplateLock = Extension.create({
  name: "templateLock",
  addProseMirrorPlugins: () => [
    new Plugin({
      key: templateLockKey,
      filterTransaction: (tr, state) => !tr.docChanged || skeleton(tr.doc) === skeleton(state.doc),
    }),
  ],
});

// Pasted HTML is untrusted (it comes from other pages and documents): an allowlist, not a blocklist.
// Only what the SOW schema can hold survives; every other tag is unwrapped to its text, and a
// script's, style's or template's contents are dropped with it. No link, image or style attribute.
const PASTE_TAGS = [
  "p", "br", "h1", "h2", "h3", "h4", "ul", "ol", "li", "strong", "b", "em", "i", "blockquote",
  "section", "div", "article", "span",
]; // prettier-ignore
// The data-* hooks the SOW nodes parse from (copy and paste inside the editor), and nothing else.
const PASTE_ATTRS = [
  "data-section", "data-field", "data-label", "data-record", "data-item", "data-citations",
  "data-cite",
]; // prettier-ignore

/** Pasted HTML reduced to the SOW's own vocabulary (DOMPurify, tag and attribute allowlist). */
export function cleanPastedHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: PASTE_TAGS,
    ALLOWED_ATTR: PASTE_ATTRS,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    KEEP_CONTENT: true,
    FORBID_CONTENTS: [
      "script",
      "style",
      "template",
      "iframe",
      "object",
      "embed",
      "noscript",
      "svg",
      "math",
      "title",
    ],
    USE_PROFILES: undefined,
  });
}

const PasteGuard = Extension.create({
  name: "pasteGuard",
  addProseMirrorPlugins: () => [
    new Plugin({
      key: new PluginKey("sowPasteGuard"),
      props: { transformPastedHTML: cleanPastedHtml },
    }),
  ],
});

export const sowExtensions: AnyExtension[] = [
  StarterKit.configure({
    document: false,
    heading: { levels: [2, 3] },
    codeBlock: false,
    code: false,
    horizontalRule: false,
    link: false,
    underline: false,
  }),
  SowDoc,
  SowSection,
  SowField,
  SowRecord,
  SowItem,
  SowCitations,
  Citation,
  TemplateLock,
  PasteGuard,
];

/** A loadable SOW document: nine sections in template order, each starting with its heading. */
export function isSowDoc(json: unknown): json is JSONContent {
  const doc = json as JSONContent | null;
  if (!doc || doc.type !== "doc" || !Array.isArray(doc.content)) return false;
  const ids = doc.content.map((s) => s.attrs?.section_id);
  return (
    doc.content.every((s) => s.type === "sowSection" && s.content?.[0]?.type === "heading") &&
    ids.length === SECTION_IDS.length &&
    ids.every((id, i) => id === SECTION_IDS[i])
  );
}

/** Citations of a document, in reading order (their on-screen number is the index + 1). */
export function citationsOf(doc: JSONContent | undefined): JSONContent["attrs"][] {
  const out: JSONContent["attrs"][] = [];
  const walk = (node: JSONContent | undefined) => {
    if (!node) return;
    if (node.type === "citation") out.push(node.attrs);
    node.content?.forEach(walk);
  };
  walk(doc);
  return out;
}
