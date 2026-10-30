"""Six demo documents for the #318 OCR comparison. ALL DATA IS INVENTED; none of this is a real scan.

Harder than the earlier clean pages: a table, a rubber stamp over text, a perspective-warped phone
photo, small print, handwriting, and a page scanned sideways. Each file has `<name>.ids.txt`, the
exact strings printed on it (the answer key).

    python demo_docs.py <output folder>
"""
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path("C:/Windows/Fonts")
W, H = 1240, 1754  # A4 at 150 dpi
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
rng = random.Random(318)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def page(color: str = "white") -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), color)
    return image, ImageDraw.Draw(image)


def grain(image: Image.Image, amount: int = 14) -> Image.Image:
    """Scanner noise: a little brightness jitter on every pixel (kept cheap with a noise layer)."""
    noise = Image.effect_noise(image.size, amount).convert("RGB")
    return Image.blend(image, noise, 0.06)


def keys(name: str, items: list[str]) -> None:
    (out / f"{name}.ids.txt").write_text("\n".join(items) + "\n", encoding="utf-8")


# 1. An invoice with a table and small print, as an image-only PDF, slightly crooked ----------
img, d = page()
d.text((90, 90), "INVOICE", font=font("arialbd.ttf", 64), fill="black")
d.text((90, 190), "Invoice no. INV-2026-17342        Date 2026-10-12", font=font("arial.ttf", 30), fill="black")
d.text((90, 240), "Customer account AC-55120        Order ref SO-991847", font=font("arial.ttf", 30), fill="black")
cols = [90, 250, 760, 880, 1010, 1150]
head = ["Code", "Description", "Qty", "Unit", "Total"]
y = 330
for i, h in enumerate(head):
    d.text((cols[i] + 8, y), h, font=font("arialbd.ttf", 28), fill="black")
rows = [
    ("HX-4410", "Connector, 12 pin, gold plated", "250", "1.85", "462.50"),
    ("RL-0072-B", "Relay, 24 V coil, 10 A", "40", "6.20", "248.00"),
    ("CB-3391", "Cable assembly 1.5 m shielded", "120", "4.75", "570.00"),
    ("FS-0815", "Fuse holder panel mount", "300", "0.62", "186.00"),
]
for r in rows:
    y += 70
    for i, cell in enumerate(r):
        d.text((cols[i] + 8, y), cell, font=font("arial.ttf", 27), fill="black")
for yy in range(318, y + 60, 70):
    d.line([(cols[0], yy), (cols[-1], yy)], fill="black", width=2)
for x in cols:
    d.line([(x, 318), (x, y + 52)], fill="black", width=2)
d.text((820, y + 100), "Total due: EUR 1,466.50", font=font("arialbd.ttf", 34), fill="black")
d.text((90, 1560), "Bank: IBAN DE44 5001 0517 5407 3249 31   BIC INGDDEFFXXX   VAT ID DE298763410", font=font("arial.ttf", 17), fill="black")
d.text((90, 1590), "Payment within 30 days. Late payment interest 9 percent. Goods remain our property until paid in full.", font=font("arial.ttf", 17), fill="black")
img = grain(img.rotate(0.9, expand=False, fillcolor="white", resample=Image.BICUBIC))
img.save(out / "invoice_table.pdf", "PDF", resolution=150.0)
keys("invoice_table", ["INV-2026-17342", "AC-55120", "SO-991847", "HX-4410", "RL-0072-B", "CB-3391",
                       "570.00", "EUR 1,466.50", "DE44 5001 0517 5407 3249 31", "DE298763410"])

# 2. A purchase order with a RECEIVED stamp across part of the text ---------------------------
img, d = page()
d.text((90, 90), "PURCHASE ORDER", font=font("arialbd.ttf", 58), fill="black")
body = ["PO number PO-2026-60418", "Supplier code SUP-77031", "Deliver to Dock 7, Plant 3, Example Road 12",
        "Required by 2026-11-20", "Item 10: Sensor module SM-2209-R x 75 at USD 18.40",
        "Item 20: Bracket BR-5530 x 150 at USD 2.15", "Approved by: procurement desk, ref APR-33812"]
y = 220
for line in body:
    d.text((90, y), line, font=font("arial.ttf", 34), fill="black")
    y += 80
stamp = Image.new("RGBA", (620, 220), (0, 0, 0, 0))
sd = ImageDraw.Draw(stamp)
sd.rectangle([6, 6, 612, 212], outline=(190, 30, 30, 200), width=9)
sd.text((40, 30), "RECEIVED", font=font("arialbd.ttf", 84), fill=(190, 30, 30, 200))
sd.text((60, 140), "2026-10-14  DOCK 7", font=font("arialbd.ttf", 40), fill=(190, 30, 30, 200))
stamp = stamp.rotate(11, expand=True, resample=Image.BICUBIC)
img.paste(stamp, (520, 330), stamp)  # lands across lines three to five
img = grain(img.rotate(-0.6, fillcolor="white", resample=Image.BICUBIC))
img.save(out / "purchase_order_stamp.png")
keys("purchase_order_stamp", ["PO-2026-60418", "SUP-77031", "2026-11-20", "SM-2209-R", "BR-5530", "APR-33812", "USD 18.40"])

# 3. A delivery note photographed on a desk: perspective, shadow, blur, JPEG --------------------
img, d = page()
d.text((90, 90), "DELIVERY NOTE", font=font("arialbd.ttf", 58), fill="black")
body = ["Delivery note DN-2026-30571", "Carrier TRK-9920-441 collected 24 cartons", "Gross weight 318.4 kg",
        "Lot L-4410-C   Serial range SN-700100 to SN-700123", "Customer PO PO-2026-60418", "Signature required on arrival"]
y = 220
for line in body:
    d.text((90, y), line, font=font("arial.ttf", 36), fill="black")
    y += 90
desk = Image.new("RGB", (1500, 2000), (120, 100, 80))
coeffs = (1.0, 0.12, -40, 0.04, 1.08, -30, 0.00006, 0.00012)  # a tilted camera
warped = img.transform((1400, 1900), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(120, 100, 80))
desk.paste(warped, (50, 50))
shade = Image.linear_gradient("L").resize(desk.size).point(lambda v: 255 - v // 3)
desk = Image.composite(desk, Image.new("RGB", desk.size, (30, 30, 30)), shade)
desk = desk.filter(ImageFilter.GaussianBlur(1.2))
desk.save(out / "delivery_note_photo.jpg", quality=55)
keys("delivery_note_photo", ["DN-2026-30571", "TRK-9920-441", "318.4 kg", "L-4410-C", "SN-700100", "SN-700123", "PO-2026-60418"])

# 4. A test report in small print with a measurement table ------------------------------------
img, d = page()
d.text((90, 80), "Test report TR-2026-0722  unit PCB-NAV9-REV-D", font=font("arialbd.ttf", 36), fill="black")
d.text((90, 140), "Operator station ST-12   Date 2026-09-30   Checksum 7B1E-90C4-55AD", font=font("arial.ttf", 20), fill="black")
cols = [90, 330, 560, 760, 960, 1150]
y = 220
for i, h in enumerate(["Test", "Nominal", "Measured", "Limit", "Result"]):
    d.text((cols[i] + 6, y), h, font=font("arialbd.ttf", 19), fill="black")
data = [("Supply 3V3", "3.30 V", "3.312 V", "+/- 2 %", "PASS"), ("Supply 1V8", "1.80 V", "1.791 V", "+/- 3 %", "PASS"),
        ("Clock 25M", "25.000 MHz", "25.003 MHz", "+/- 50 ppm", "PASS"), ("Idle current", "85 mA", "91.4 mA", "< 100 mA", "PASS"),
        ("Pull-up R14", "10.0 kOhm", "10.37 kOhm", "+/- 5 %", "PASS"), ("Temp U7", "40 C", "58.2 C", "< 60 C", "PASS"),
        ("Leak I2C", "0 uA", "1.8 uA", "< 2 uA", "PASS"), ("Torque J3", "0.45 Nm", "0.52 Nm", "+/- 10 %", "FAIL")]
for r in data:
    y += 46
    for i, cell in enumerate(r):
        d.text((cols[i] + 6, y), cell, font=font("cour.ttf", 19), fill="black")
d.text((90, y + 90), "Remarks: connector J3 retorqued, see rework order RW-2026-0159. Re-test required.", font=font("arial.ttf", 17), fill="black")
img = grain(img.filter(ImageFilter.GaussianBlur(0.6)))
img.save(out / "test_report_smallprint.png")
keys("test_report_smallprint", ["TR-2026-0722", "PCB-NAV9-REV-D", "ST-12", "7B1E-90C4-55AD", "3.312 V", "25.003 MHz",
                                "91.4 mA", "10.37 kOhm", "58.2 C", "RW-2026-0159"])

# 5. Handwritten notes on lined paper -----------------------------------------------------------
img, d = page((250, 248, 238))
for yy in range(160, H - 100, 62):
    d.line([(70, yy), (W - 70, yy)], fill=(170, 190, 220), width=2)
hand = font("Inkfree.ttf", 46)
lines = ["Goods received OK - note DN-4471", "12 cartons, carton C-88 damaged corner", "Counted by J. Mendes 2026-10-14",
         "Call supplier ref SUP-2209 about the dent", "Stock location B-14-03", "Return label RMA-70155 attached"]
y = 105
for line in lines:
    d.text((95 + rng.randint(-6, 6), y + rng.randint(-4, 4)), line, font=hand, fill=(30, 40, 110))
    y += 124
img = grain(img.rotate(-1.4, fillcolor=(250, 248, 238), resample=Image.BICUBIC))
img.save(out / "handwritten_note.jpg", quality=70)
keys("handwritten_note", ["DN-4471", "C-88", "J. Mendes", "2026-10-14", "SUP-2209", "B-14-03", "RMA-70155"])

# 6. A two-column memo scanned sideways (rotated 90 degrees) ---------------------------------
img, d = page()
d.text((90, 90), "MEMO MEMO-2026-0457", font=font("arialbd.ttf", 48), fill="black")
d.text((90, 160), "To: build team    From: quality    Date 2026-11-03", font=font("arial.ttf", 26), fill="black")
left = ["Project Falcon enters build freeze on", "2026-11-17. Revision REV-C of the", "NAV-9 board replaces REV-B for all",
        "orders after serial SN-801000.", "Sample lot L-5531-A is held at QA."]
right = ["Action items:", "1. Update drawing DWG-90417 to REV-C.", "2. Re-run test plan TP-2026-044.",
         "3. Confirm supplier SUP-12098 stock.", "4. Report by Friday, ticket QA-77310."]
for i, line in enumerate(left):
    d.text((90, 300 + i * 70), line, font=font("arial.ttf", 30), fill="black")
for i, line in enumerate(right):
    d.text((660, 300 + i * 70), line, font=font("arial.ttf", 30), fill="black")
img = grain(img.rotate(90, expand=True, fillcolor="white"))
img.save(out / "memo_sideways.png")
keys("memo_sideways", ["MEMO-2026-0457", "2026-11-17", "REV-C", "SN-801000", "L-5531-A", "DWG-90417", "TP-2026-044", "QA-77310"])

for f in sorted(out.iterdir()):
    print(f.name, f.stat().st_size)
