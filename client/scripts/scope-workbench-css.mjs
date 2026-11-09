// Scopes every stylesheet under src/workbench to the `.bam-wb` wrapper element, so the workbench's
// `:root`, `html`, `body` and `*` rules cannot restyle the Admin screens. Safe to re-run: a file that
// already carries the marker is skipped. Run from client/:  node scripts/scope-workbench-css.mjs
import fs from "node:fs";
import path from "node:path";

const SCOPE = ".bam-wb";
const MARKER = `/* scoped to ${SCOPE} by scripts/scope-workbench-css.mjs */`;
const ROOT = path.join(process.cwd(), "src", "workbench");
// At-rules whose body holds style rules; every other at-rule (keyframes, font-face, ...) is copied as is.
const NESTED = new Set(["media", "supports", "layer", "container"]);

/** Index of the character after the matching close of the bracket opened at `open`, skipping strings and comments. */
function skipBlock(css, open) {
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    const c = css[i];
    if (c === '"' || c === "'") i = skipString(css, i);
    else if (c === "/" && css[i + 1] === "*") i = css.indexOf("*/", i + 2) + 1;
    else if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return i + 1;
  }
  return css.length;
}

function skipString(css, start) {
  const q = css[start];
  for (let i = start + 1; i < css.length; i++) {
    if (css[i] === "\\") i++;
    else if (css[i] === q) return i;
  }
  return css.length;
}

/** Splits a selector list at the commas outside parentheses, brackets and strings. */
function splitSelectors(list) {
  const parts = [];
  let depth = 0;
  let from = 0;
  for (let i = 0; i < list.length; i++) {
    const c = list[i];
    if (c === '"' || c === "'") i = skipString(list, i);
    else if (c === "(" || c === "[") depth++;
    else if (c === ")" || c === "]") depth--;
    else if (c === "," && depth === 0) {
      parts.push(list.slice(from, i));
      from = i + 1;
    }
  }
  parts.push(list.slice(from));
  return parts;
}

function scopeSelector(raw) {
  const sel = raw.trim();
  if (!sel) return sel;
  if (sel.startsWith(SCOPE)) return sel;
  // The page-level selectors become the wrapper itself.
  let m = sel.match(/^(:root|html|body|#root)(?![\w-])/);
  if (m) return SCOPE + sel.slice(m[0].length);
  // `.app` is the class of the wrapper element, so it stays on the same element.
  m = sel.match(/^\.app(?![\w-])/);
  if (m) return `${SCOPE}.app${sel.slice(m[0].length)}`;
  return `${SCOPE} ${sel}`;
}

function scope(css) {
  let out = "";
  let i = 0;
  while (i < css.length) {
    // whitespace and comments pass through
    const ws = css.slice(i).match(/^\s+/);
    if (ws) {
      out += ws[0];
      i += ws[0].length;
      continue;
    }
    if (css.startsWith("/*", i)) {
      const end = css.indexOf("*/", i + 2) + 2 || css.length;
      out += css.slice(i, end);
      i = end;
      continue;
    }
    // prelude: up to `{` or `;`
    let j = i;
    for (; j < css.length; j++) {
      const c = css[j];
      if (c === '"' || c === "'") j = skipString(css, j);
      else if (c === "{" || c === ";") break;
    }
    const prelude = css.slice(i, j);
    if (css[j] === ";" || j >= css.length) {
      out += css.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    const end = skipBlock(css, j);
    const body = css.slice(j + 1, end - 1);
    const at = prelude.trim().match(/^@([a-z-]+)/i);
    if (at) {
      out += NESTED.has(at[1].toLowerCase()) ? `${prelude}{${scope(body)}}` : `${prelude}{${body}}`;
    } else {
      out += `${splitSelectors(prelude).map(scopeSelector).join(", ")} {${body}}`;
    }
    i = end;
  }
  return out;
}

function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith(".css")) {
      const css = fs.readFileSync(p, "utf8");
      if (css.startsWith(MARKER)) continue;
      fs.writeFileSync(p, `${MARKER}\n${scope(css)}`);
      console.log("scoped", path.relative(ROOT, p));
    }
  }
}

walk(ROOT);
