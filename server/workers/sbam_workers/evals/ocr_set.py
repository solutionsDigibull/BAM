"""The OCR golden set (E15.5): five synthetic business pages, each drawn four ways.

The text is made up (example domains, invented ids) and holds no real data. The four ways are the
conditions a scan arrives in: a clean page, a JPEG, a simulated phone photo (crooked, blurred,
speckled, unevenly lit) and half resolution. An item passes when every identifier comes back exactly
as printed; a wrong character in an id is what harms the knowledge base.

`server/evals/golden/ocr.json` is this module's output, and a test checks the two agree:

    uv run python -m sbam_workers.evals.ocr_set
"""

from pathlib import Path
from typing import Final

from sbam_workers.evals.golden import GOLDEN_DIR, OcrItem, OcrSet, RenderSpec, Task

DOCUMENTS: Final[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]] = (
    (
        "quotation request",
        (
            "Request for Quotation RFQ-7741-ZA",
            "Vendor code VND-30458 requests 1,250 units of part HX-220-B, delivered by 2027-02-14.",
            "Standard: ISO 9001:2015. The unit price must not exceed USD 18.75.",
            "Contact: buyer.desk@example.org",
        ),
        ("RFQ-7741-ZA", "VND-30458", "HX-220-B", "2027-02-14", "ISO 9001:2015", "18.75"),
    ),
    (
        "purchase order",
        (
            "Purchase Order PO-2026-88213",
            "Ship to: Dock 4, Plant 12, 77 Example Road, Lakeside.",
            "Line 1: Connector C-5521 x 400 at 0.63 each. Line 2: Relay K-908 x 75 at 4.20 each.",
            "Payment: net 45 days. Reference: INV-55-0192.",
        ),
        ("PO-2026-88213", "C-5521", "K-908", "0.63", "4.20", "INV-55-0192"),
    ),
    (
        "invoice",
        (
            "Invoice INV-2026-04417 dated 2026-09-30",
            "Customer account AC-90017. VAT ID EX123456789.",
            "Total due: EUR 12,480.50 by 2026-10-30.",
            "Bank reference: REF/2026/0930/17",
        ),
        (
            "INV-2026-04417",
            "2026-09-30",
            "AC-90017",
            "EX123456789",
            "12,480.50",
            "REF/2026/0930/17",
        ),
    ),
    (
        "delivery note",
        (
            "Delivery Note DN-19-6620",
            "Carrier TRK-4450-118 collected 36 cartons, gross weight 412.5 kg.",
            "Lot L-7781-A, serial range SN-100200 to SN-100235.",
            "Receiver signature required on arrival.",
        ),
        ("DN-19-6620", "TRK-4450-118", "412.5", "L-7781-A", "SN-100200", "SN-100235"),
    ),
    (
        "test report",
        (
            "Test Report TR-2026-0315, unit under test PCB-NAV9-REV-C",
            "Supply 3.3 V +/- 2 percent, measured 3.31 V. Torque 0.45 Nm.",
            "Result: PASS. Tested on 2026-08-19 by station ST-07.",
            "Checksum 9F3A-22C1-07BE.",
        ),
        (
            "TR-2026-0315",
            "PCB-NAV9-REV-C",
            "3.31 V",
            "0.45",
            "2026-08-19",
            "ST-07",
            "9F3A-22C1-07BE",
        ),
    ),
)

CONDITIONS: Final[tuple[tuple[str, dict[str, float | int | None]], ...]] = (
    ("clean page", {}),
    ("JPEG, quality 80", {"jpeg_quality": 80}),
    (
        "simulated phone photo",
        {"rotate": 2.5, "blur": 1.1, "speckle": 55, "shade": 0.28, "jpeg_quality": 60, "seed": 7},
    ),
    ("half resolution", {"scale": 0.5}),
)


def build_ocr_set() -> OcrSet:
    items: list[OcrItem] = []
    for name, lines, identifiers in DOCUMENTS:
        for condition, settings in CONDITIONS:
            items.append(
                OcrItem(
                    id=f"ocr-{len(items) + 1:02d}",
                    description=f"{name}, {condition}",
                    synthetic=True,
                    render=RenderSpec.model_validate({"lines": list(lines), **settings}),
                    identifiers=list(identifiers),
                )
            )
    return OcrSet(task=Task.OCR, version=1, items=items)


def render_json() -> str:
    return build_ocr_set().model_dump_json(indent=2) + "\n"


if __name__ == "__main__":
    target = Path(GOLDEN_DIR) / "ocr.json"
    target.write_text(render_json(), encoding="utf-8", newline="\n")
    print(f"wrote {target}")
