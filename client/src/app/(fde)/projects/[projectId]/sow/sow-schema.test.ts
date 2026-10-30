import { Editor, type JSONContent } from "@tiptap/react";
import { afterEach, describe, expect, test } from "vitest";

import fixture from "../../../../../../e2e/fixtures/sow-ms01.json";
import { SECTION_IDS, citationsOf, cleanPastedHtml, isSowDoc, sowExtensions } from "./sow-schema";

const SOW = fixture.content_tiptap as JSONContent;
let editor: Editor | undefined;

/** JSON without null attributes or empty content: TipTap fills every attribute's default and keeps
 *  `content: []`; the server omits both. Neither changes the SOW the server reads. */
const plain = (doc: unknown): unknown =>
  JSON.parse(
    JSON.stringify(doc, (_k, v: unknown) =>
      v === null || (Array.isArray(v) && v.length === 0) ? undefined : v,
    ),
  );

// jsdom has no ClipboardEvent; ProseMirror's pasteHTML only needs one to exist.
globalThis.ClipboardEvent ??= class extends Event {
  clipboardData = null;
} as unknown as typeof ClipboardEvent;

const open = (content: JSONContent = SOW) => {
  editor = new Editor({ extensions: sowExtensions, content });
  return editor;
};

afterEach(() => editor?.destroy());

/** Position just inside the heading of the n-th section. */
function headingPos(e: Editor, n: number): { from: number; to: number } {
  let from = 0;
  let index = 0;
  e.state.doc.forEach((section, offset) => {
    if (index++ === n) from = offset + 2; // section open + heading open
  });
  const heading = e.state.doc.child(n).firstChild;
  return { from, to: from + (heading?.content.size ?? 0) };
}

describe("SOW editor schema (E6.5, ADR 0019)", () => {
  test("loads the server's document unchanged", () => {
    const e = open();
    expect(plain(e.getJSON())).toEqual(plain(SOW));
    expect(isSowDoc(e.getJSON())).toBe(true);
    expect(e.state.doc.childCount).toBe(SECTION_IDS.length);
  });

  test("e6_5_t04 a section heading cannot be deleted or renamed, its content can", () => {
    const e = open();
    const before = e.getJSON();
    const { from, to } = headingPos(e, 2); // Scope
    e.chain().setTextSelection({ from, to }).deleteSelection().run();
    expect(e.getJSON()).toEqual(before);
    e.chain()
      .setTextSelection(from + 1)
      .insertContent("X")
      .run();
    expect(e.getJSON()).toEqual(before);
    e.chain().setTextSelection({ from: 0, to: e.state.doc.content.size }).deleteSelection().run();
    expect(e.getJSON()).toEqual(before);

    // the first paragraph after the Scope heading is editable
    const scope = e.state.doc.child(2);
    const paragraphAt = headingPos(e, 2).to + 1 + 2 + 1; // close heading, open field, open list…
    expect(scope.childCount).toBeGreaterThan(1);
    e.commands.setTextSelection(paragraphAt + 2);
    e.commands.insertContent("Edited ");
    expect(JSON.stringify(e.getJSON())).toContain("Edited ");
  });

  test("an item id cannot change and a section cannot move", () => {
    const e = open();
    const before = e.getJSON();
    const items: number[] = [];
    e.state.doc.descendants((node, pos) => {
      if (node.type.name === "sowItem") items.push(pos);
    });
    e.view.dispatch(e.state.tr.setNodeMarkup(items[0] ?? 0, undefined, { id: "F-HACK-1" }));
    expect(e.getJSON()).toEqual(before);
  });

  test("e6_5_t07 pasted script, iframe, style and img never reach the document", () => {
    const e = open();
    const { to } = headingPos(e, 0);
    e.commands.setTextSelection(to + 4);
    e.view.pasteHTML(
      '<img src=x onerror="alert(1)"><script>alert(2)</script><iframe src="http://evil"></iframe>' +
        "<style>body{}</style><p>plain text</p>",
    );
    expect(JSON.stringify(e.getJSON())).toContain("plain text");
    const saved = JSON.stringify(e.getJSON());
    for (const bad of ["script", "iframe", "style", '"image"', "onerror", "alert(", "evil"]) {
      expect(saved).not.toContain(bad);
    }
    expect(e.getHTML()).not.toMatch(/<(script|iframe|img|style)/);
  });

  describe("a11 pasted HTML is reduced to an allowlist (DOMPurify)", () => {
    const hostile = [
      '<p onclick="alert(1)" onmouseover="alert(2)">x</p>',
      '<a href="javascript:alert(1)">click</a>',
      '<p><a href="data:text/html,<script>alert(1)</script>">d</a></p>',
      "<svg><script>alert(1)</script></svg>",
      '<svg onload="alert(1)"></svg>',
      "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
      '<form action="http://evil"><input name=a></form>',
      '<p style="background:url(http://evil/x)">s</p>',
      '<div data-x="1" id="a" class="c" aria-label="l" contenteditable>t</div>',
      '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
      '<base href="http://evil/"><link rel="stylesheet" href="http://evil/c.css">',
      '<object data="http://evil"></object><embed src="http://evil">',
      "<video src=x onerror=alert(1)><audio src=x onerror=alert(1)>",
      "<details open ontoggle=alert(1)>t</details>",
    ];

    test.each(hostile)("%s leaves no script, handler, link or style behind", (html) => {
      const out = cleanPastedHtml(html);
      expect(out).not.toMatch(
        /<\s*(script|iframe|img|svg|math|style|link|base|form|input|object|embed|a|video|audio|details)\b/i,
      );
      expect(out).not.toMatch(
        /\son[a-z]+\s*=|javascript:|data:text|evil|style=|id=|class=|aria-|contenteditable/i,
      );
    });

    test("scripts and styles are dropped with their contents, other tags keep their text", () => {
      const out = cleanPastedHtml(
        "<script>alert(1)</script><style>b{}</style><u>kept</u><p>text</p>",
      );
      expect(out).toContain("kept");
      expect(out).toContain("<p>text</p>");
      expect(out).not.toMatch(/alert|b\{\}/);
    });

    test("the SOW's own hooks and plain formatting survive a paste", () => {
      const html =
        '<section data-section="overview"><div data-field="objective" data-label="Objective">' +
        "<p>Goal <strong>one</strong> <em>two</em></p><ul><li>a</li></ul></div></section>" +
        '<span data-cite="ref-1">1</span><h2>Title</h2>';
      const out = cleanPastedHtml(html);
      for (const keep of [
        "data-section",
        "data-field",
        "data-label",
        "data-cite",
        "<strong>",
        "<li>",
        "<h2>",
      ]) {
        expect(out).toContain(keep);
      }
    });

    test("a mutation-prone payload is stable: sanitizing twice changes nothing", () => {
      for (const html of hostile) {
        const once = cleanPastedHtml(html);
        expect(cleanPastedHtml(once)).toBe(once);
      }
    });

    test("through the editor: handlers and javascript: links never reach the document", () => {
      const e = open();
      const { to } = headingPos(e, 0);
      e.commands.setTextSelection(to + 4);
      e.view.pasteHTML('<p onclick="alert(1)">safe</p><a href="javascript:alert(2)">link</a>');
      const saved = JSON.stringify(e.getJSON());
      expect(saved).toContain("safe");
      expect(saved).not.toMatch(/onclick|javascript|alert\(|"link"/);
      expect(e.getHTML()).not.toMatch(/onclick|javascript:|<a\b/);
    });
  });

  test("citations are listed in reading order with their ids", () => {
    const cites = citationsOf(SOW);
    expect(cites.length).toBeGreaterThan(5);
    expect(cites[0]?.okf_ref_id).toBe(fixture.structured.overview.citations[0]?.okf_ref_id);
  });

  test("isSowDoc refuses a document that is not the template", () => {
    expect(isSowDoc({ type: "doc", content: [] })).toBe(false);
    expect(isSowDoc({ type: "doc", content: (SOW.content ?? []).slice(1) })).toBe(false);
    expect(isSowDoc(null)).toBe(false);
  });
});
