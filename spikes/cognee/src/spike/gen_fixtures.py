"""Synthetic fixtures for spike E0.9 (#26). No real data.

Writes, per document, the normalized derivative the product loaders will produce (E4.5, TD §5.2):
  fixtures/<project>/<document_version_id>/doc.md    normalized Markdown text
  fixtures/<project>/<document_version_id>/doc.json  blocks with provenance (page / sheet+cell / line)
Project A carries marker ZEBRA-7731, project B carries QUOKKA-4419 (isolation test T01).
Deterministic: same input -> same ids and bytes.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

NS = uuid.UUID("5b0f6c1e-0000-4000-8000-000000000026")
TENANT_ID = "11111111-1111-4111-8111-111111111111"
PROJECTS = {
    "A": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "B": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
}
SEP = "\n\n"


def doc(project: str, name: str, title: str, blocks: list[tuple[str, dict]]) -> dict:
    return {"project": project, "name": name, "title": title, "blocks": blocks,
            "document_version_id": str(uuid.uuid5(NS, f"{project}/{name}"))}


def project_docs() -> list[dict]:
    a = "A"
    b = "B"
    return [
        doc(a, "rfq_alpha", "RFQ Alpha Dynamics", [
            ("# RFQ ADX-2026-014 from Alpha Dynamics", {"page": 1, "line": 1}),
            ("Alpha Dynamics Ltd requests a quotation for 2,500 units of the NAV-9 flight controller board. "
             "Internal tracking reference ZEBRA-7731. Required delivery: 2026-12-01 to the Austin, TX facility.",
             {"page": 1, "line": 3}),
            ("The assembly must meet IPC-A-610 Class 3 and AS9100D. Rocketreach Pvt. Ltd. will act as the "
             "Tier-1 EMS contract manufacturer for the programme.", {"page": 1, "line": 6}),
            ("Pricing basis: live costing with FX as-of the quotation date; scrap rate 1.5 percent.",
             {"page": 2, "line": 2}),
        ]),
        doc(a, "bom_alpha", "BOM Alpha NAV-9", [
            ("## Sheet: BOM", {"sheet": "BOM", "cell": "A1"}),
            ("| CPN | MPN | Qty | Supplier |\n| --- | --- | --- | --- |\n| C-100 | GRM188R71H104KA93 | 120 | DigiKey |\n"
             "| U-210 | STM32H743VIT6 | 1 | Mouser |\n| R-330 | RC0603FR-0710KL | 85 | DigiKey |",
             {"sheet": "BOM", "cell": "A2:D5"}),
            ("Note: U-210 is single-source; flag for sourcing review. Total lines = 3.", {"sheet": "BOM", "cell": "A7"}),
        ]),
        doc(a, "supplier_terms", "Supplier terms Alpha", [
            ("# Supplier terms", {"page": 1, "line": 1}),
            ("DigiKey offers net-30 payment with a minimum order value of USD 250. Mouser offers net-45 for "
             "approved accounts. Quotes are valid for 14 days.", {"page": 1, "line": 3}),
        ]),
        doc(a, "boilerplate_numbers", "Boilerplate numbers only", [
            ("0 0 0 0 0 0 0 0", {"page": 1, "line": 1}),
            ("00 00 00 00 00 00", {"page": 1, "line": 2}),
        ]),
        doc(b, "rfq_beta", "RFQ Beta Marine", [
            ("# RFQ BMX-2026-077 from Beta Marine", {"page": 1, "line": 1}),
            ("Beta Marine GmbH requests 800 units of the TIDE-4 sonar interface board. Internal tracking "
             "reference QUOKKA-4419. Required delivery: 2027-01-15 to the Hamburg warehouse.",
             {"page": 1, "line": 3}),
            ("The board requires conformal coating and ISO 9001 certified assembly.", {"page": 1, "line": 5}),
        ]),
        doc(b, "bom_beta", "BOM Beta TIDE-4", [
            ("## Sheet: BOM", {"sheet": "BOM", "cell": "A1"}),
            ("| CPN | MPN | Qty | Supplier |\n| --- | --- | --- | --- |\n| C-900 | CL10A106KP8NNNC | 60 | Farnell |\n"
             "| U-020 | TPS54331DR | 2 | Farnell |", {"sheet": "BOM", "cell": "A2:D4"}),
        ]),
        doc(b, "notes_unicode", "Multilingual notes", [
            ("# Notes", {"page": 1, "line": 1}),
            ("تم الاتفاق على تسليم لوحات TIDE-4 إلى ميناء هامبورغ. 部品番号 U-020 は在庫切れ。Ünïcödé ✓ — QUOKKA-4419.",
             {"page": 1, "line": 3}),
        ]),
    ]


def write_all(root: Path) -> list[dict]:
    docs = project_docs()
    for d in docs:
        out = root / f"project_{d['project'].lower()}" / d["document_version_id"]
        out.mkdir(parents=True, exist_ok=True)
        md = SEP.join(text for text, _ in d["blocks"]) + "\n"  # trailing newline: repo end-of-file-fixer hook
        (out / "doc.md").write_text(md, encoding="utf-8", newline="")
        blocks = [{"id": f"{d['document_version_id']}#b{i}", "text": text, "location": loc}
                  for i, (text, loc) in enumerate(d["blocks"])]
        (out / "doc.json").write_text(json.dumps(
            {"document_version_id": d["document_version_id"], "name": d["name"], "title": d["title"],
             "blocks": blocks}, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return docs


def main() -> None:
    root = Path("/spike/fixtures")
    docs = write_all(root)
    for p in ("A", "B"):
        print(f"project {p}:", [d["name"] for d in docs if d["project"] == p])


if __name__ == "__main__":
    main()
