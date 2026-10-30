# OCR comparison: the vision model against a local engine (#318, ADR 0022)

Measures, on a folder of page images or PDFs, how many **key identifiers** each engine reads exactly,
and what the vision model costs. It prints and records **counts, tokens, cost and time only**: never
the text of a page, so it is safe to run on confidential documents (the images do go to the vision
model, as in the product, so leave out anything that must not).

## The folder

Each document is `name.png|jpg|jpeg|tif|tiff|pdf` (a PDF is read at its first page), and beside it
`name.ids.txt`: the exact strings printed on that page that matter (ids, amounts, dates), one per
line. That list is the answer key. A page with no `.ids.txt` is read but not scored.

`demo_docs.py` makes six **invented** documents with their answer keys (an invoice table, a stamp over
text, a perspective photo, small print, a handwriting-style font, a sideways page). It uses Windows
fonts. None of it is a real scan.

```
uv run python demo_docs.py <folder>
```

## Run it

The local engine (RapidOCR, installed for the run only), in the repo's environment:

```
cd server
uv run --with rapidocr-onnxruntime python ../spikes/ocr-compare/compare_local.py <folder> local.json
```

The vision model, through the production `ocr.read_page` and Bifrost, inside the worker container
(it has the stack's provider key; a throwaway in-memory call store is used, so nothing is written to
your `llm_calls`):

```
docker exec -u root sbam-worker-llm-1 sh -c 'mkdir -p /tmp/real/scans && chmod -R a+rwX /tmp/real'
docker cp spikes/ocr-compare/common.py        sbam-worker-llm-1:/tmp/real/
docker cp spikes/ocr-compare/compare_llm.py   sbam-worker-llm-1:/tmp/real/
docker cp <folder>/.                          sbam-worker-llm-1:/tmp/real/scans/
docker exec -u root sbam-worker-llm-1 chmod -R a+rwX /tmp/real
docker exec sbam-worker-llm-1 python /tmp/real/compare_llm.py /tmp/real/scans /tmp/real/out.json
docker cp sbam-worker-llm-1:/tmp/real/out.json vision.json
docker exec -u root sbam-worker-llm-1 rm -rf /tmp/real        # leave nothing behind
```

On Git Bash set `MSYS_NO_PATHCONV=1` first, or it rewrites the `/tmp/...` paths.

Every page is read at full size and at half size. One run per case, so a single miss is not shown to
repeat. Cost is about $0.004 to $0.006 a page.
