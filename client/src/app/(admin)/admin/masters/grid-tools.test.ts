import { describe, expect, test } from "vitest";

import {
  chips,
  facetValues,
  matchesFacets,
  matchesSearch,
  pushHistory,
  readHistory,
  suppressEnter,
  toCsv,
} from "./grid-tools";
import { entry, LAYOUT } from "./test-fixtures";

const rows = [
  entry({ id: "a", stable_id: "ORG-1", payload: { location: "Pune; Hosur", org_name: "Acme" } }),
  entry({ id: "b", stable_id: "ORG-2", payload: { location: "Pune", org_name: "Beta; Co" } }),
  entry({ id: "c", stable_id: "ORG-3", payload: { location: "Delhi", org_name: "=cmd()" } }),
];

describe("MUI-4 grid helpers", () => {
  test("mui4_t01 search needs every word; facets are OR inside and AND across", () => {
    expect(matchesSearch(rows[0]!, "acme pune")).toBe(true);
    expect(matchesSearch(rows[0]!, "acme delhi")).toBe(false);
    expect(rows.filter((r) => matchesFacets(r, { location: ["Hosur", "Delhi"] })).length).toBe(2);
    expect(matchesFacets(rows[0]!, { location: ["Pune"], org_name: ["Beta"] })).toBe(false);
    expect(facetValues(rows, "location")).toEqual(["Delhi", "Hosur", "Pune"]);
    expect(chips("a; b,\n c ")).toEqual(["a", "b", "c"]);
  });

  test("mui4_t01 search history keeps 12 distinct terms, newest first", () => {
    window.localStorage.clear();
    for (let i = 0; i < 15; i++) pushHistory("SKILLS", `term ${i}`);
    pushHistory("SKILLS", "term 7");
    const h = readHistory("SKILLS");
    expect(h).toHaveLength(12);
    expect(h[0]).toBe("term 7");
    expect(new Set(h).size).toBe(12);
  });

  test("mui4_t02 CSV has the header text, quotes commas and defuses formulas", () => {
    const info = {
      ...LAYOUT.types[0]!,
      columns: [{ header: "Org Name", field: "org_name", description: "" }],
    };
    const csv = toCsv(info, rows.slice(1));
    expect(csv.split("\r\n")).toEqual(["Org Name,Status", "Beta; Co,PROPOSED", "'=cmd(),PROPOSED"]);
  });

  test("mui4_t03 Enter in an open editor adds a line; Ctrl+Enter is left to the grid (B2)", () => {
    const ev = (key: string, ctrlKey = false) => ({ key, ctrlKey, metaKey: false });
    expect(suppressEnter({ editing: true, event: ev("Enter") })).toBe(true);
    expect(suppressEnter({ editing: true, event: ev("Enter", true) })).toBe(false);
    expect(suppressEnter({ editing: false, event: ev("Enter") })).toBe(false);
    expect(suppressEnter({ editing: true, event: ev("a") })).toBe(false);
  });
});
