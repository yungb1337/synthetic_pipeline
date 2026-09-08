"""Tests for LLM Judge findings improvements:
1. Column-aware reading order (multi-column geometric partitioning).
2. Metadata entity unescaping and Unicode NFKC normalization.
3. Reference extractor continuation line merging and multi-entry block splitting.
"""
import pytest
from app.parser.dom.models import Block, BBox, Page, Reference
from app.parser.dom.reading_order import recover_per_page, recover_reading_order
from app.parser._pdfmeta import clean_meta_string
from app.parser.dom.reference_extractor import extract_references


class DummyBlock:
    def __init__(self, id: str, text: str, x0: float, y0: float, x1: float, y1: float, page: int = 1, seq: int = 0, kind: str = "paragraph"):
        self.id = id
        self.text = text
        self.bbox = (x0, y0, x1, y1)
        self.page = page
        self.seq = seq
        self.kind = kind


def test_column_aware_reading_order_two_columns():
    """Verify that parallel columns are read top-to-bottom within column 1, then column 2,
    rather than alternating horizontally across rows.
    """
    # 2 columns with 3 lines each
    l1 = DummyBlock("l1", "Left Col Line 1", 50, 100, 250, 120, seq=1)
    r1 = DummyBlock("r1", "Right Col Line 1", 300, 100, 500, 120, seq=2)
    l2 = DummyBlock("l2", "Left Col Line 2", 50, 130, 250, 150, seq=3)
    r2 = DummyBlock("r2", "Right Col Line 2", 300, 130, 500, 150, seq=4)
    l3 = DummyBlock("l3", "Left Col Line 3", 50, 160, 250, 180, seq=5)
    r3 = DummyBlock("r3", "Right Col Line 3", 300, 160, 500, 180, seq=6)

    # Input in arbitrary / interleaved order
    blocks = [r1, l2, r3, l1, r2, l3]
    ordered = recover_per_page(blocks)
    ordered_ids = [b.id for b in ordered]

    # Expected: all left column blocks first (l1, l2, l3), then right column blocks (r1, r2, r3)
    assert ordered_ids == ["l1", "l2", "l3", "r1", "r2", "r3"]


def test_column_aware_reading_order_with_spanning_elements():
    """Verify that full-width titles and spanning section headers act as natural
    vertical boundaries between multi-column regions.
    """
    title = DummyBlock("title", "Document Title", 50, 20, 550, 60, seq=0)
    l1 = DummyBlock("l1", "Intro left", 50, 80, 260, 120, seq=1)
    r1 = DummyBlock("r1", "Intro right", 300, 80, 550, 120, seq=2)
    span_hdr = DummyBlock("span_hdr", "Methods & Materials", 50, 140, 550, 170, seq=3)
    l2 = DummyBlock("l2", "Methods left", 50, 190, 260, 230, seq=4)
    r2 = DummyBlock("r2", "Methods right", 300, 190, 550, 230, seq=5)

    blocks = [r1, l2, span_hdr, title, r2, l1]
    ordered = recover_per_page(blocks)
    ordered_ids = [b.id for b in ordered]

    assert ordered_ids == ["title", "l1", "r1", "span_hdr", "l2", "r2"]


def test_reading_order_supports_both_bbox_models():
    """Verify that recover_per_page works seamlessly with Pydantic BBox objects as well as tuples."""
    b1 = Block(id="b1", page=1, text="Text 1", bbox=BBox(x0=50, y0=50, x1=500, y1=80))
    b2 = Block(id="b2", page=1, text="Text 2", bbox=BBox(x0=50, y0=90, x1=500, y1=120))

    ordered = recover_per_page([b2, b1])
    assert [b.id for b in ordered] == ["b1", "b2"]


def test_clean_meta_string():
    """Verify unescaping of XML/HTML entities and NFKC normalization."""
    assert clean_meta_string("18F&#x02010;Florbetaben") == "18F‐Florbetaben"
    assert clean_meta_string("Clinical Trials &amp; Results") == "Clinical Trials & Results"
    assert clean_meta_string("Author&#39;s Note") == "Author's Note"
    assert clean_meta_string("Title with \x00 null bytes") == "Title with  null bytes"
    assert clean_meta_string(None) == ""
    assert clean_meta_string(12345) == "12345"


def test_reference_extractor_continuation_merging():
    """Verify that multi-block / wrap-around reference continuation lines are merged."""
    class MockBlock:
        def __init__(self, id, text, kind="paragraph", page=1):
            self.id = id
            self.text = text
            self.kind = kind
            self.page = page
            self.bbox = None

    class MockPage:
        def __init__(self, index, blocks):
            self.index = index
            self.blocks = blocks

    p1 = MockPage(1, [
        MockBlock("h1", "References", kind="heading"),
        MockBlock("b1", "[1] First Author, Second Author. Title of the Medical Study.", kind="paragraph"),
        MockBlock("b2", "Journal of Clinical Investigation, 2024; 12(3): 45-56.", kind="paragraph"),  # continuation
        MockBlock("b3", "[2] Third Author. Another Landmark Paper.", kind="paragraph"),
        MockBlock("b4", "Lancet 2023.", kind="paragraph"),  # continuation
    ])

    refs, citation_index = extract_references([p1], doc_id="doc1")

    assert len(refs) == 2
    assert refs[0].label == "[1]"
    assert "Title of the Medical Study. Journal of Clinical Investigation, 2024" in refs[0].text
    assert refs[1].label == "[2]"
    assert "Another Landmark Paper. Lancet 2023." in refs[1].text
    assert citation_index["1"] == "doc1/ref-1"
    assert citation_index["2"] == "doc1/ref-2"
