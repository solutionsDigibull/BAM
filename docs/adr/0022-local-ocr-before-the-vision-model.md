# 0022 — Local OCR before the vision model: not built now

- Status: Proposed
- Date: 2026-10-30
- Related: issues #318 (E4.8c), #74 (E4.8), #324; refines [C-10](../plan/README.md) ("MVP: basic extraction plus LLM vision ... better OCR later")

## Context

#74 reads a scanned page with the `sbam-ocr` vision model only. #318 asks whether a local OCR
engine should read the page first, with the vision model as the fallback, so the LLM cost drops.
The issue's open questions are how many real scans a local engine reads correctly, what a vision
page costs, and whether the memory cap of the ingest worker (1 GiB) allows it.

## Measurement

**What was measured, and its limits.** Five images of two synthetic RFQ pages (typed text, the
fixture generator's own text, so the truth is known exactly): the clean page (`scan.png`), the same
page as a JPEG at quality 80 (`photo.jpg`), a **simulated** phone photo (rotated 2.5 degrees, blurred,
speckled, unevenly lit, JPEG quality 60), the page at half resolution, and page 2. **No real scan
was available, so none of this is a real scan.** It is five pages of clean typed text, which is the
easy case: no tables, handwriting, stamps or rotated originals.

Engines: `sbam-ocr` (`openai/gpt-4o-mini` through Bifrost and OpenRouter, the real `ocr-page` prompt
and `read_page` path) and RapidOCR (`rapidocr-onnxruntime`, CPU, default models). The score is the
count of key identifiers (`ADX-2026-014`, `NAV-9`, `IPC-A-610`, `AS9100D`, `2026-12-01`, the e-mail
address, `2,500`, `1.5`; page 2: `U-210`, `14 days`, `net 30`) found exactly, because a wrong
character in an identifier is what harms the knowledge base. Word recall is not reported: RapidOCR
drops spaces between words, which makes a word metric misleading.

| Image | Vision model: identifiers, seconds, cost | RapidOCR: identifiers, seconds |
|---|---|---|
| clean page 1 | 8/8, 5.5 s, $0.0057 | 7/8 (missed `AS9100D`), 3.4 s |
| page 1 as JPEG | 8/8, 3.8 s, $0.0057 | 6/8 (`ADX-2026-014`, `AS9100D`), 2.5 s |
| simulated phone photo | 8/8, 8.1 s, $0.0057 | 5/8 (`ADX-2026-014`, `NAV-9`, `AS9100D`), 3.6 s |
| half resolution | 8/8, 3.9 s, $0.0023 | 7/8 (`NAV-9`), 3.8 s |
| page 2 | 3/3, 3.4 s, $0.0056 | 3/3, 2.0 s |
| **Total** | **35/35, $0.0250 for 5 pages** | **28/35** |

The local engine misread identifiers with no warning (`ADX` came back as `ADx`, `NAV-9` as `NAv-9`),
so a high confidence score is not proof, as the issue feared. One vision page of 827 x 1169 pixels
was 37,403 input tokens; the same page at half resolution was 14,735 and read just as well.

## Decision

**Do not build the local-first pass now.** Recommended; the maintainer decides.

- The vision model read every identifier on every page, including the degraded ones, for about
  $0.006 a page ($6 per 1,000 pages). The saving of a local pass is small next to what a wrong
  identifier costs, and a local result cannot be trusted without a rule for when to fall back, which
  cannot be tuned without real scans.
- A cheaper lever is already visible: sending the page at half resolution cost 40 percent as much and
  read just as well on this page. Test that on real scans (and `detail: "low"`, which is untested here)
  before building anything.

## Consequences

- #318's first box (a measured comparison on real scans) stays open: this is a measurement on
  synthetic and simulated pages. The decision should be confirmed on a few real scans (a PDF scan and
  a phone photo) before it is accepted.
- If the real scans show the vision model failing where a local engine would not, or the page volume
  makes $6 per 1,000 pages matter, reopen this: record the OCR source (`local` or `llm`) per block, fall
  back to the vision model on a low-confidence or short page, and never let the local engine raise the
  share of wrong text that reaches the knowledge base.
- Not measured: tables, handwriting, stamps, rotated originals, `detail: "low"`, the memory use of
  RapidOCR in the 1 GiB ingest worker, and any engine other than RapidOCR (Tesseract, docTR, EasyOCR).
