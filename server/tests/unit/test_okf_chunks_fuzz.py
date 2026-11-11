"""Property tests for `build_chunks` and `anchor_check`: whatever doc.json holds, the chunks tile
doc.md exactly, never crash and never lose text; the identifier check never raises."""

import hashlib
import uuid
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from sbam_workers.knowledge import okf

WORDS = st.text(alphabet="abc XY12-|\n#", min_size=0, max_size=12)


@st.composite
def documents(draw: st.DrawFn) -> okf.Doc:
    """doc.md built from pieces; blocks are some of the pieces, in order, of mixed kinds."""
    pieces = draw(st.lists(WORDS, min_size=0, max_size=40))
    text = "".join(pieces)
    blocks: list[Any] = []
    for piece in pieces:
        kind = draw(st.sampled_from(["cell", "paragraph", "table", "skip", "junk"]))
        if kind == "skip":
            continue
        if kind == "junk":
            blocks.append(draw(st.sampled_from([None, 3, "x", {"text": 5}, {"type": "cell"}])))
            continue
        blocks.append(
            {
                "type": kind,
                "text": "" if kind == "table" else piece,
                "sheet": draw(st.sampled_from(["S1", "S2", None])),
                "cell": "A1",
                "unreadable": draw(st.booleans()),
            }
        )
    return okf.Doc(uuid.uuid4(), text, hashlib.sha256(text.encode()).hexdigest(), blocks)


@settings(max_examples=300, deadline=None)
@given(documents())
def test_chunks_always_tile_the_document_exactly(doc: okf.Doc) -> None:
    chunks, warnings = okf.build_chunks(doc)
    assert chunks, "a document always has at least one chunk"
    assert "".join(c.text for c in chunks) == doc.text  # nothing lost, nothing repeated
    assert len({c.id for c in chunks}) == len(chunks)
    assert all(w.startswith("BLOCKS_NOT_IN_TEXT") for w in warnings)


@settings(max_examples=200, deadline=None)
@given(st.text(max_size=3000), st.lists(st.text(max_size=30), max_size=20))
def test_anchor_check_never_raises_and_found_never_exceeds_total(
    text: str, names: list[str]
) -> None:
    check = okf.anchor_check([okf.Doc(uuid.uuid4(), text, "0" * 64)], names)
    assert 0 <= check.found <= check.total <= okf.MAX_ANCHORS
    assert len(check.missing) <= okf.MISSING_SAMPLE
