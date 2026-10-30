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

## Second measurement: six harder demo documents

The first five pages were clean typed text, the easy case. So six more were made, again **invented
and rendered by a script, not scanned**: an invoice with a table (an image-only PDF, slightly
crooked), a purchase order with a RECEIVED stamp across two lines of data, a delivery note photographed
at an angle (perspective, a shadow, blur, JPEG), a test report in small print with a measurement
table, a note in a handwriting-style font on lined paper, and a memo scanned sideways (rotated 90
degrees). The same method: the identifiers printed on each page, scored exactly, one run per case.
Each page was read at full size (1240 x 1754) and at half size.

| Document | Vision model, full | Vision, half | RapidOCR, full | RapidOCR, half |
|---|---|---|---|---|
| invoice with a table (PDF) | 10/10, $0.0058 | 10/10, $0.0058 | 10/10 | 10/10 |
| purchase order, stamp over data | 7/7, $0.0057 | 6/7, $0.0040 | 5/7 | 5/7 |
| phone photo, perspective and shadow | 7/7, $0.0040 | 7/7, $0.0040 | 7/7 | 7/7 |
| test report, small print and table | 10/10, $0.0058 | 8/10, $0.0041 | 9/10 | 10/10 |
| handwriting-style note | 7/7, $0.0057 | 7/7, $0.0040 | 4/7 | 4/7 |
| memo scanned sideways | 7/8, $0.0057 | 6/8, $0.0040 | 8/8 | 8/8 |
| **Total** | **48/49, $0.0327** | **44/49, $0.0259** | **43/49** | **44/49** |

Time per page: the vision model 4.5 to 16 s (68 s for the six at full size), RapidOCR 3 to 7 s
(27 s). What the table shows:

- **Neither engine wins everywhere.** The vision model read through the stamp (7/7 against 5/7) and
  the handwriting-style text (7/7 against 4/7). RapidOCR read the sideways memo completely (8/8)
  where the model missed `QA-77310`, the last item of the second column, at full size and the
  title id too at half size.
- **Half size is not free.** It saved only 21 percent of the cost on these larger pages (the image is
  already scaled down before it is priced) and lost 4 of 49 identifiers, against saving 60 percent
  with no loss on the earlier 827 x 1169 page. The saving depends on the page size, so a blanket
  downscale is not supported by this data.
- **The local engine misses by omission here**, in the stamp and handwriting cases, rather than
  by reading a wrong character with confidence as in the first measurement.

**Limits, which matter:** still no real scan. A handwriting font on a rendered page is far easier than
real handwriting; the "photo" is a rendered page put through a perspective transform, with no lens or
lighting; one run per case, so a single miss (the `QA-77310` one) is not shown to repeat; 49
identifiers is a small sample; the vision model is a single model.

### The provider's cheap image mode (`detail: "low"`)

The same six documents were read again with `detail: "low"` added to every image in the request (the
comparison tool does this with `OCR_DETAIL=low` and changes nothing else on the production path; the
product itself sends no `detail`). Input tokens fell to 3,401 on every page and the cost to about
$0.0006 a page, $0.0037 for the six against $0.0327 at the default: about 9 times cheaper. Accuracy fell
to **32 of 49** identifiers at full size and at half size, against 48 of 49 at the default. At full
size, by document: the photo 7/7 (unchanged), the handwriting-style note 6/7, the stamp 5/7, the invoice
table 6/10, the small-print report 6/10 and the sideways memo 2/8. The small and dense pages lost the
most. Low mode and half size did not stack: both gave 32 of 49. One run per case, on invented pages.

## Decision

**Do not build the local-first pass now, for cost.** Recommended; the maintainer decides.

- The vision model read 48 of 49 identifiers on the harder documents and every identifier on the
  clean ones, for about $0.006 a page ($6 per 1,000 pages). The saving of a local pass is small next
  to what a wrong identifier costs, and nothing here tells us when a local result can be trusted
  enough to skip the model, so a fallback rule cannot be tuned without real scans.
- **Do not downscale pages, or use `detail: "low"`, to save money on this evidence.** Half size lost
  accuracy on the larger pages for a 21 percent saving. `detail: "low"` is about 9 times cheaper but
  lost a third of the identifiers (16 of 49), and most on tables, small print and a rotated page,
  which are the pages where OCR matters. At $0.006 a page the full-quality read is already cheap.
- **A different use of the local engine is worth testing on real scans: a second opinion, not a cost
  saver.** The two engines failed on different documents (stamp and handwriting against a sideways
  page). Running both and flagging an identifier that only one of them found would catch the model's
  misses at a cost of seconds of CPU and no extra model call. This is a hypothesis from six
  documents, not a finding.

## Consequences

- #318's first box (a measured comparison on real scans) stays open: both measurements are on
  invented pages, clean and harder, and none is a real scan. The decision should be confirmed on a few
  real scans (a PDF scan and a phone photo) before it is accepted. The comparison is a script that
  takes a folder of scans, each with a list of its key identifiers, and records only counts, tokens,
  cost and time, so it can be run on confidential documents without their content reaching a report.
- If the real scans show the vision model failing where a local engine would not, or the page volume
  makes $6 per 1,000 pages matter, reopen this: record the OCR source (`local` or `llm`) per block, fall
  back to the vision model on a low-confidence or short page, and never let the local engine raise the
  share of wrong text that reaches the knowledge base.
- Not measured: real handwriting, real photographs, multi-page documents, repeated runs of the same
  page, the memory use of RapidOCR in the 1 GiB ingest worker, and any engine other
  than RapidOCR (Tesseract, docTR, EasyOCR). Tables, a stamp over text, small print, a perspective
  photo and a sideways page were measured on invented documents only.
