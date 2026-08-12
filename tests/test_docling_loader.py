"""Tests for the Docling layout/table backend (ADR-007).

The Docling path is gated behind `ParserConfig.layout_backend == "docling"`.
Docling is an optional dependency, so tests that exercise the real backend skip
when it is not installed (matching the OCR lazy-engine pattern); the
degradation-to-native test runs on every environment.
"""
from __future__ import annotations

import pytest


def _extractor(tmp_path, config):
    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.storage import FilesystemStore

    store = FilesystemStore(str(tmp_path / "store"))
    pub = EventPublisher(sink=lambda name, payload: None)
    return Extractor(config, store, events=pub), store


def _docling_config():
    from app.parser.config import ParserConfig

    return ParserConfig(layout_backend="docling")


def _pdf_bytes():
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Clinical Report", fontsize=20)
    page.insert_text((72, 130), "The patient has stable diabetes on metformin.", fontsize=11)
    # a tiny bordered table so table-structure has something to find (API-version
    # dependent; skip if the installed PyMuPDF has no table-insertion support)
    try:
        tab = page.new_table()
        tab.add_row(["patient_id", "age"])
        tab.add_row(["P0001", "62"])
        tab.add_row(["P0002", "31"])
        page.insert_table((72, 200), tab, border=0.5)
    except Exception:
        pass
    return doc.tobytes()


def test_docling_mapping_logic(tmp_path):
    """White-box: DoclingDocument item → RecoveredDocument mapping works without
    Docling installed (fake items exercise _map_item/_map_table/_map_image)."""
    from PIL import Image

    from app.parser.loaders import docling_loader
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        def __init__(self, v):
            self.value = v

    class FakeBBox:
        def __init__(self, l, t, r, b):
            self.l, self.t, self.r, self.b = l, t, r, b

    class FakeProv:
        def __init__(self, page, bbox):
            self.page_no = page
            self.bbox = FakeBBox(*bbox)

    class FakeItem:
        def __init__(self, label, text="", page=0, bbox=(0, 0, 10, 10)):
            self.label = FakeLabel(label)
            self.text = text
            self.prov = [FakeProv(page, bbox)]

    class FakeCell:
        def __init__(self, r, c, text):
            self.start_row_offset_idx = r
            self.start_col_offset_idx = c
            self.text = text

    class FakeTable:
        table_cells = [FakeCell(0, 0, "h1"), FakeCell(0, 1, "h2"),
                       FakeCell(1, 0, "a"), FakeCell(1, 1, "b")]

    class FakeTableItem:
        label = FakeLabel("table")
        prov = [FakeProv(0, (0, 0, 50, 50))]
        table = FakeTable()

        def export_to_dataframe(self):
            raise AttributeError("force the cell-grid fallback")

    class FakePictureItem:
        label = FakeLabel("picture")
        prov = [FakeProv(1, (10, 10, 20, 20))]
        image = Image.new("RGB", (4, 4))

    rec = RecoveredDocument()
    for item in [FakeItem("section_header", "Intro", 0, (0, 0, 100, 20)),
                 FakeItem("text", "Body text here", 0, (0, 30, 200, 50)),
                 FakeItem("list_item", "- item", 0, (0, 60, 50, 80)),
                 FakeItem("code", "x=1", 0, (0, 90, 50, 100)),
                 FakeTableItem(),
                 FakePictureItem()]:
        docling_loader._map_item(item, rec)

    kinds = [b.kind for b in rec.blocks]
    assert kinds == ["heading", "paragraph", "list_item", "code"]
    assert rec.tables and rec.tables[0].header == ["h1", "h2"]
    assert rec.tables[0].rows and rec.tables[0].rows[0] == ["a", "b"]
    assert rec.images and rec.images[0].mime == "image/png"
    assert rec.images[0].page == 1


def test_table_structural_confidence_detects_row_collapse():
    """Faithful & fallible: Docling's grid can collapse several logical rows
    into ONE body row of space-joined concatenations (e.g. Table 5's
    'WANLI GPT3Mix ...' cell under the 'Dataset' header). The row boundaries
    are not present in the extraction evidence, so we must NOT fabricate rows —
    we surface the uncertainty via `Table.confidence` instead."""
    from app.parser.loaders import docling_loader as dl

    # well-segmented: header + >=2 body rows -> trustworthy
    assert dl._table_structural_confidence(["A", "B"], [["a1", "b1"], ["a2", "b2"]]) == 1.0
    # legitimate single-data-row table (short cells) -> trustworthy
    assert dl._table_structural_confidence(["Dataset", "Ref"], [["WANLI", "[36]"]]) == 1.0
    # collapsed: single body row whose cell is a long concatenation
    collapsed = dl._table_structural_confidence(
        ["Dataset", "Reference"],
        [["WANLI GPT3Mix Unnatural Instructions Self-Instruct AugGPT Code Alpaca WizardCoder AlphaCode",
          "[36] [58] [23] [53] [9] [4]"]],
    )
    assert collapsed < 1.0
    # single-column tables have no column structure to collapse -> not flagged
    assert dl._table_structural_confidence(["Notes"], [["a b c d e f g h i j k l m n o p"]]) == 1.0


def test_map_table_preserves_segmented_rows(tmp_path):
    """Regression (the observed failure mode): a table Docling segments into N
    logical body rows must land as N DOM rows with the correct cell mapping —
    verifying row/cell relationships, not just that the text exists somewhere."""
    import pandas as pd

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        value = "table"

    class FakeBBox:
        l = t = 0
        r = b = 10

    class FakeProv:
        page_no = 0
        bbox = FakeBBox()

    class FakeTableItem:
        label = FakeLabel()
        prov = [FakeProv()]
        table = object()  # non-None so _map_table's guard passes (df path)

        def export_to_dataframe(self):
            return pd.DataFrame({"Dataset": ["WANLI", "GPT3Mix"], "Domain": ["Text", "Text"]})

    rec = RecoveredDocument()
    dl._map_table(FakeTableItem(), rec, 0, (0, 0, 10, 10))
    t = rec.tables[0]
    assert t.header == ["Dataset", "Domain"]
    assert len(t.rows) == 2                 # both logical rows preserved
    assert t.rows[0] == ["WANLI", "Text"]   # row-1 cell mapping
    assert t.rows[1] == ["GPT3Mix", "Text"]
    assert t.confidence == 1.0


def test_map_table_flags_collapsed_rows_without_fabrication(tmp_path):
    """Faithful & fallible: when Docling emits a single concatenated body row
    (the row-collapse failure), we keep the text intact but flag the structure
    as low-confidence — we do NOT invent rows the evidence cannot support."""
    import pandas as pd

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        value = "table"

    class FakeBBox:
        l = t = 0
        r = b = 10

    class FakeProv:
        page_no = 0
        bbox = FakeBBox()

    class FakeTableItem:
        label = FakeLabel()
        prov = [FakeProv()]
        table = object()  # non-None so _map_table's guard passes (df path)

        def export_to_dataframe(self):
            return pd.DataFrame({
                "Dataset": ["WANLI GPT3Mix Unnatural Instructions AugGPT Code Alpaca"],
                "Reference": ["[36] [58] [23] [9] [4]"],
            })

    rec = RecoveredDocument()
    dl._map_table(FakeTableItem(), rec, 0, (0, 0, 10, 10))
    t = rec.tables[0]
    assert t.confidence < 1.0                         # structure flagged uncertain
    assert len(t.rows) == 1                           # nothing fabricated
    assert "WANLI" in t.rows[0][0] and "Code Alpaca" in t.rows[0][0]  # text preserved


def test_normalize_merges_marker_prefixed_continuation():
    """General: a continuation fragment whose header embeds the parent header
    (a caption prefix + the repeated real header) is ONE logical table. The
    parent's header is canonical; the caption prefix is not kept as header text."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    parent = RecoveredTable(page=7, header=["Approach / Study", "Key Idea"],
                            rows=[["A", "B"]], source="docling")
    frag = RecoveredTable(page=8,
                          header=["Continuation of Table 3.Approach / Study",
                                  "Continuation of Table 3.Key Idea"],
                          rows=[["C", "D"], ["E", "F"]], source="docling")
    out = dl.normalize_tables([parent, frag])
    assert len(out) == 1
    assert out[0].header == ["Approach / Study", "Key Idea"]
    assert out[0].rows == [["A", "B"], ["C", "D"], ["E", "F"]]


def test_normalize_merges_degenerate_marker_continuation_and_drops_markers():
    """General: a continuation fragment whose header is a degenerate marker
    (identical cells) and whose first row repeats the real header, plus an
    all-identical 'End of Table' row — all resolved structurally, no text
    matching, no hardcoded phrase."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    parent = RecoveredTable(page=11, header=["Approach / System", "Code Task", "Key Idea"],
                            rows=[["CodeRL [31]", "gen", "uses RL"]], source="docling")
    frag = RecoveredTable(page=12, header=["Continuation of Table 4"] * 3,
                          rows=[["Approach / System", "Code Task", "Key Idea"],  # repeated header
                                ["WizardCoder [40]", "complex", "builds on"],
                                ["End of Table", "End of Table", "End of Table"]], source="docling")
    out = dl.normalize_tables([parent, frag])
    assert len(out) == 1
    assert out[0].header == ["Approach / System", "Code Task", "Key Idea"]
    assert out[0].rows == [["CodeRL [31]", "gen", "uses RL"],
                           ["WizardCoder [40]", "complex", "builds on"]]  # header-repeat + marker dropped


def test_normalize_does_not_merge_unrelated_tables():
    """General: different column counts or unrelated headers are NOT merged."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    a = RecoveredTable(page=3, header=["X", "Y"], rows=[["1", "2"]], source="docling")
    b = RecoveredTable(page=3, header=["A", "B", "C"], rows=[["3", "4", "5"]], source="docling")
    c = RecoveredTable(page=6, header=["P", "Q"], rows=[["6", "7"]], source="docling")
    out = dl.normalize_tables([a, b, c])
    assert len(out) == 3


def test_normalize_chain_of_three_fragments():
    """General: a table spanning three pages folds into one logical table."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    f1 = RecoveredTable(page=5, header=["H1", "H2"], rows=[["a", "b"]], source="docling")
    f2 = RecoveredTable(page=6, header=["Continuation of Table 9.H1", "Continuation of Table 9.H2"],
                        rows=[["c", "d"]], source="docling")
    f3 = RecoveredTable(page=7, header=["Continuation of Table 9.H1", "Continuation of Table 9.H2"],
                        rows=[["e", "f"]], source="docling")
    out = dl.normalize_tables([f1, f2, f3])
    assert len(out) == 1
    assert out[0].rows == [["a", "b"], ["c", "d"], ["e", "f"]]


def test_evidence_reconstruct_recovers_collapsed_rows(tmp_path):
    """A borderless multi-row table that Docling collapsed into one concatenated
    row is recovered from page geometry: rows are lines whose words start at
    each column position. The header line is not kept as a data row."""
    import fitz

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    col0, col1 = 72, 300
    y = 100
    page.insert_text((col0, y), "Col A", fontsize=12)
    page.insert_text((col1, y), "Col B", fontsize=12)
    for a, b in [("one", "alpha"), ("two", "beta"), ("three", "gamma")]:
        y += 22
        page.insert_text((col0, y), a, fontsize=11)
        page.insert_text((col1, y), b, fontsize=11)
    pdf_bytes = doc.tobytes()

    t = RecoveredTable(page=1, header=["Col A", "Col B"],
                       rows=[["one two three", "alpha beta gamma"]],  # collapsed body
                       source="docling", confidence=0.3,
                       column_starts=[72.0, 300.0])
    dl._evidence_reconstruct(pdf_bytes, t)
    assert len(t.rows) == 3                       # header line dropped, 3 data rows
    assert t.rows[0] == ["one", "alpha"]
    assert t.rows[2] == ["three", "gamma"]
    assert t.confidence == 0.9
    assert t.source == "docling+evidence"


def test_evidence_reconstruct_insufficient_evidence_keeps_collapsed():
    """Faithful & fallible: when the page evidence does not establish >=2
    aligned rows, the collapsed table stands unchanged (no fabrication)."""
    import fitz

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Header One", fontsize=12)
    page.insert_text((300, 100), "Header Two", fontsize=12)
    # only one data line -> not enough evidence for multiple rows
    page.insert_text((72, 122), "solo", fontsize=11)
    pdf_bytes = doc.tobytes()

    t = RecoveredTable(page=1, header=["H1", "H2"], rows=[["solo", ""]],
                       source="docling", confidence=0.3, column_starts=[72.0, 300.0])
    dl._evidence_reconstruct(pdf_bytes, t)
    assert t.rows == [["solo", ""]]               # unchanged
    assert t.confidence == 0.3


def test_evidence_reconstruct_does_not_split_wrapped_single_row():
    """A single logical row whose cells WRAP across several visual lines must
    NOT be split into several rows: each wrap line carries content in 2-3
    columns, so per-line alignment alone cannot tell a wrap from a row. Only
    columns that do NOT wrap prove row boundaries — here the three columns wrap
    to different line counts (2/3/3), so the evidence supports ONE row and the
    faithful collapsed row stands unchanged."""
    import fitz

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    c0, c1, c2 = 72, 250, 400
    # header line
    page.insert_text((c0, 100), "Approach", fontsize=9)
    page.insert_text((c1, 100), "Task", fontsize=9)
    page.insert_text((c2, 100), "Idea", fontsize=9)
    # ONE data row whose cells interleave across 3 visual lines (col0=2 lines,
    # col1=3, col2=3) — every line touches >= 2 columns.
    page.insert_text((c0, 122), "Alpha Beta", fontsize=8)
    page.insert_text((c1, 122), "Task A", fontsize=8)
    page.insert_text((c2, 122), "one two three four five six", fontsize=8)
    page.insert_text((c0, 134), "Gamma", fontsize=8)
    page.insert_text((c1, 134), "Task B more", fontsize=8)
    page.insert_text((c2, 134), "seven eight nine ten eleven twelve", fontsize=8)
    page.insert_text((c1, 146), "Task C", fontsize=8)
    page.insert_text((c2, 146), "thirteen fourteen fifteen sixteen", fontsize=8)
    pdf_bytes = doc.tobytes()

    t = RecoveredTable(page=1, header=["Approach", "Task", "Idea"],
                       rows=[["Alpha Beta Gamma", "Task A Task B more Task C",
                              "one two three four five six seven eight nine ten "
                              "eleven twelve thirteen fourteen fifteen sixteen"]],
                       source="docling", confidence=0.3,
                       column_starts=[float(c0), float(c1), float(c2)])
    dl._evidence_reconstruct(pdf_bytes, t)
    assert len(t.rows) == 1               # not over-segmented into 3 rows
    assert t.confidence == 0.3            # evidence did not establish >1 row
    assert t.rows[0][0] == "Alpha Beta Gamma"  # text preserved intact


def test_evidence_reconstruct_folds_sibling_line_before_anchor():
    """A cell whose words sit ~0.5px ABOVE the rest of its row (a sub-pixel
    baseline split, e.g. 'WizardCoder' at 232.49 vs the rest at 232.58) must be
    folded INTO that row — the first cell of the row must not come back empty."""
    import fitz

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    c0, c1, c2, c3, c4 = 72, 140, 220, 330, 470
    page.insert_text((c0, 100), "WANLI", fontsize=9)
    page.insert_text((c1, 100), "Text", fontsize=9)
    page.insert_text((c2, 100), "Natural", fontsize=9)
    page.insert_text((c3, 100), "AccF1", fontsize=9)
    page.insert_text((c4, 100), "[36]", fontsize=9)
    # row 2: first word 0.6px ABOVE the rest of its row (one rendered line split)
    page.insert_text((c0, 132.4), "WizardCoder", fontsize=9)
    page.insert_text((c1, 133.0), "Code", fontsize=9)
    page.insert_text((c2, 133.0), "Complex", fontsize=9)
    page.insert_text((c3, 133.0), "PassK", fontsize=9)
    page.insert_text((c4, 133.0), "[40]", fontsize=9)
    pdf_bytes = doc.tobytes()

    t = RecoveredTable(page=1, header=["Dataset", "Domain", "Type", "Metrics", "Ref"],
                       rows=[["WANLI WizardCoder", "Text Code", "Natural Complex",
                              "AccF1 PassK", "[36] [40]"]],
                       source="docling", confidence=0.3,
                       column_starts=[float(v) for v in (c0, c1, c2, c3, c4)])
    dl._evidence_reconstruct(pdf_bytes, t)
    assert len(t.rows) == 2
    row2 = t.rows[1]
    assert row2[0] == "WizardCoder"       # first cell populated, not empty
    assert row2[1] == "Code"
    assert row2[4] == "[40]"


def test_normalize_strips_fused_trailing_marker():
    """A table-footer marker ('End of Table') that the upstream extractor fused
    into the LAST cell's text (after a sentence boundary) is removed — it must
    not become table content. Structurally detected; no text matched."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredTable

    t = RecoveredTable(page=1, header=["A", "B", "C"],
                       rows=[["CoT [13]", "Task", "Improves model performance. End of Table"]],
                       source="docling")
    out = dl.normalize_tables([t])
    assert out[0].rows[-1][-1] == "Improves model performance"


def test_strip_trailing_marker_preserves_real_trailing_text():
    """Only a short, sentence-punctuation-free fragment AFTER the last sentence
    boundary is a marker. A genuine final sentence keeps its period and stays."""
    from app.parser.loaders import docling_loader as dl

    assert dl._strip_trailing_marker_cell("X improves model performance. End of Table") == \
        "X improves model performance"
    # genuine trailing sentence ends with '.', so it is NOT stripped
    assert dl._strip_trailing_marker_cell("Found strong gains. This is a real trailing sentence.") == \
        "Found strong gains. This is a real trailing sentence."
    # no sentence boundary -> untouched
    assert dl._strip_trailing_marker_cell("Short cell value") == "Short cell value"
    # long fragment after the boundary is real text, not a marker
    assert dl._strip_trailing_marker_cell("Result A. Something much longer than a marker here.") == \
        "Result A. Something much longer than a marker here."


def test_map_table_strips_full_width_title_row_into_caption():
    """A leading full-width row spanning the whole table (a title, e.g. 'Adult
    Census Data (10K records)') flagged column_header must NOT fuse into every
    column name; it becomes the table caption and the LAST header row stays the
    real header. Structural rule (span == ncols), no table ids, no text match."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        value = "table"

    class FakeBBox:
        l = t = 0
        r = b = 10

    class FakeProv:
        page_no = 0
        bbox = FakeBBox()

    class Cell:
        def __init__(self, text, column_header=False, col_span=1, row_span=1, bbox=None):
            self.text = text
            self.column_header = column_header
            self.col_span = col_span
            self.row_span = row_span
            self.bbox = bbox or FakeBBox()

    title_row = [Cell("Adult Census Data (10K records)", column_header=True, col_span=2)]
    header_row = [Cell("SD Metrics", column_header=True), Cell("Labels", column_header=True)]
    body1 = [Cell("0.92"), Cell("0.87")]
    body2 = [Cell("0.88"), Cell("0.83")]

    class FakeTable:
        grid = [title_row, header_row, body1, body2]

    class FakeTableItem:
        label = FakeLabel()
        prov = [FakeProv()]
        table = FakeTable()

        def caption_text(self, doc=None):
            return ""

    rec = RecoveredDocument()
    dl._map_table(FakeTableItem(), rec)
    t = rec.tables[0]
    assert t.caption == "Adult Census Data (10K records)"   # title -> caption
    assert t.header == ["SD Metrics", "Labels"]             # clean header
    assert t.rows == [["0.92", "0.87"], ["0.88", "0.83"]]   # body intact
    assert t.column_starts  # geometry from the real header row, not the title


def test_map_table_keeps_real_caption_over_title_row():
    """When Docling ALSO provides an explicit caption ref, the title row is still
    stripped from the header block but the ref caption wins (never duplicated)."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        value = "table"

    class FakeBBox:
        l = t = 0
        r = b = 10

    class FakeProv:
        page_no = 0
        bbox = FakeBBox()

    class Cell:
        def __init__(self, text, column_header=False, col_span=1):
            self.text = text
            self.column_header = column_header
            self.col_span = col_span
            self.row_span = 1
            self.bbox = FakeBBox()

    class FakeTable:
        grid = [[Cell("Title Row", column_header=True, col_span=2)],
                [Cell("H1", column_header=True), Cell("H2", column_header=True)],
                [Cell("a"), Cell("b")]]

    class FakeTableItem:
        label = FakeLabel()
        prov = [FakeProv()]
        table = FakeTable()

        def caption_text(self, doc=None):
            return "Table 2: Real caption"

    rec = RecoveredDocument()
    dl._map_table(FakeTableItem(), rec)
    t = rec.tables[0]
    assert t.caption == "Table 2: Real caption"
    assert t.header == ["H1", "H2"]


def test_map_image_never_drops_and_extracts_ref_bytes():
    """Figures: (1) a PictureItem whose image is a lazy ImageRef still yields
    PNG bytes + a checksum; (2) a PictureItem with NO image (image generation
    unavailable) is preserved with empty bytes — the DOM sees the figure existed;
    (3) an explicit caption ref is attached."""
    from PIL import Image

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        value = "picture"

    class FakeBBox:
        l = t = 0
        r = b = 20

    class FakeProv:
        page_no = 1
        bbox = FakeBBox()

    class FakeImageRef:
        def __init__(self):
            self.pil_image = Image.new("RGB", (6, 6))

    class RefPicture:
        label = FakeLabel()
        prov = [FakeProv()]
        image = FakeImageRef()

        def caption_text(self, doc=None):
            return "Figure 1: Pipeline architecture"

    class NoImagePicture:
        label = FakeLabel()
        prov = [FakeProv()]
        image = None

    rec = RecoveredDocument()
    dl._map_image(RefPicture(), rec)
    dl._map_image(NoImagePicture(), rec)
    assert len(rec.images) == 2
    ref, none = rec.images
    assert ref.mime == "image/png" and ref.blob and ref.checksum
    assert ref.caption == "Figure 1: Pipeline architecture"
    assert none.mime == "" and none.blob == b"" and none.checksum == ""  # preserved, not dropped
    assert none.caption == ""


def test_map_item_preserves_empty_formula_block():
    """A formula whose transcription model produced no text still becomes a
    typed 'formula' block (presence + geometry survive), so `_recover_formula_text`
    can fill it later. Textless non-formula items are still dropped."""
    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        value = "formula"

    class FakeBBox:
        l = t = 0
        r = b = 100

    class FakeProv:
        page_no = 1
        bbox = FakeBBox()

    class FakeFormulaItem:
        label = FakeLabel()
        text = ""
        prov = [FakeProv()]

    rec = RecoveredDocument()
    dl._map_item(FakeFormulaItem(), rec)
    assert len(rec.blocks) == 1
    assert rec.blocks[0].kind == "formula"
    assert rec.blocks[0].page == 1
    assert rec.blocks[0].bbox == (0.0, 0.0, 100.0, 100.0)


def test_recover_formula_text_from_page_layer():
    """The equation IS selectable text in the page layer: _recover_formula_text
    fills an empty formula block from the words at its (top-left) bbox."""
    import fitz

    from app.parser.loaders import docling_loader as dl
    from app.parser.parts import RecoveredBlock, RecoveredDocument

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 300), "diff = df_real.corr - df_synth.corr", fontsize=11)
    pdf_bytes = doc.tobytes()

    rec = RecoveredDocument()
    rec.blocks.append(RecoveredBlock(page=1, kind="formula", text="",
                                     bbox=(72, 290, 400, 312), source="docling"))
    dl._recover_formula_text(pdf_bytes, rec)
    assert "diff" in rec.blocks[0].text
    assert "df_synth.corr" in rec.blocks[0].text


def test_bbox_normalizes_bottomleft_to_topleft():
    """Docling floating-item prov boxes use a bottom-left origin (y grows up);
    the DOM contract is PDF-point top-left (y grows down). _bbox must invert y
    using the page height, and leave top-left (or origin-less) boxes unchanged."""
    from app.parser.loaders import docling_loader as dl

    class Origin:
        value = "BOTTOMLEFT"

    class BBox:
        def __init__(self, l, t, r, b, origin):
            self.l, self.t, self.r, self.b = l, t, r, b
            self.coord_origin = origin

    class Prov:
        def __init__(self, bbox):
            self.bbox = bbox

    # Bottom-left: y grows UP, so the TOP edge has the LARGER y. A box near the
    # top of an 842pt page has t=720 (top), b=700 (bottom); it mirrors to
    # top-left y=122..142.
    bl = Prov(BBox(50, 720, 120, 700, Origin()))
    x0, y0, x1, y1 = dl._bbox(bl, page_h=842.0)
    assert (x0, y0, x1, y1) == (50.0, 122.0, 120.0, 142.0)  # y inverted
    # and a box near the BOTTOM (t=142, b=122) mirrors to y=700..720
    bl2 = Prov(BBox(50, 142, 120, 122, Origin()))
    assert dl._bbox(bl2, page_h=842.0) == (50.0, 700.0, 120.0, 720.0)
    # top-left origin (or absent origin) -> unchanged
    class NoOrigin:
        def __init__(self, l, t, r, b):
            self.l, self.t, self.r, self.b = l, t, r, b
            self.coord_origin = None

    tl = Prov(NoOrigin(50, 122, 120, 142))
    assert dl._bbox(tl, page_h=842.0) == (50.0, 122.0, 120.0, 142.0)


def test_fitz_metadata_maps_pdf_info_dict():
    """PDF info-dict metadata (title/author/...) maps into RecoveredDocument
    fields, with PDF 'D:' dates readable and missing keys empty."""
    import fitz

    from app.parser.loaders._pdfmeta import fitz_metadata

    doc = fitz.open()
    doc.set_metadata({
        "title": "A Clinical Study",
        "author": "Dr. Ada",
        "subject": "Retrospective",
        "creator": "LaTeX",
        "producer": "pdflatex",
        "creationDate": "D:20230814153012",
        "modDate": "D:20230814",
    })
    m = fitz_metadata(doc)
    assert m["title"] == "A Clinical Study"
    assert m["author"] == "Dr. Ada"
    assert m["created"] == "2023-08-14 15:30:12"
    assert m["modified"] == "2023-08-14"


def test_native_pdf_loader_carries_metadata(tmp_path):
    """Native PDF path: the extracted DOM's Metadata carries the PDF info dict
    (title/author/subject/creator), not empty strings."""
    import fitz

    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.storage import FilesystemStore

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Report body", fontsize=11)
    doc.set_metadata({"title": "Native Meta Doc", "author": "Claude", "subject": "Test"})
    pdf_bytes = doc.tobytes()

    from app.parser.config import ParserConfig

    store = FilesystemStore(str(tmp_path / "store"))
    ex = Extractor(ParserConfig(layout_backend="native"), store, events=EventPublisher(sink=lambda n, p: None))
    out = ex.extract(pdf_bytes, "meta.pdf")
    assert out.ok
    assert out.document.metadata.title == "Native Meta Doc"
    assert out.document.metadata.author == "Claude"
    assert out.document.metadata.subject == "Test"


def test_default_layout_backend_is_auto():
    """ADR-007 amendment: the default flips from "native" to "auto" so the
    intelligent router (ADR-011) picks the tier per document. Manual "native"/
    "docling" overrides remain valid (asserted elsewhere)."""
    from app.parser.config import default_config

    assert default_config().layout_backend == "auto"


def test_docling_missing_falls_back_to_native(tmp_path):
    """With layout_backend=docling but Docling absent, PDFs still parse via the
    cheap native path — deterministic degrade, never a crash."""
    from app.parser.loaders import docling_loader

    if docling_loader.engine_available():
        pytest.skip("Docling installed; the fallback path is not exercised here")

    ex, _ = _extractor(tmp_path, _docling_config())
    out = ex.extract(_pdf_bytes(), "report.pdf")
    assert out.ok
    assert out.detected.slug == "pdf"
    assert out.document.num_blocks() > 0
    assert out.document.provenance.docling_version is None


def test_docling_path_records_provenance_and_order(tmp_path):
    """When Docling is installed, the docling backend runs for layout_backend=docling:
    provenance records the engine, and reading order is authoritative (a strict chain)."""
    pytest.importorskip("docling")

    ex, _ = _extractor(tmp_path, _docling_config())
    raw = _pdf_bytes()  # generate once: PyMuPDF's /ID makes two calls differ
    out = ex.extract(raw, "complex.pdf")
    assert out.ok
    prov = out.document.provenance
    assert prov.docling_version  # docling package version recorded
    assert len(out.document.reading_order) == out.document.num_blocks()
    # source content-addressing still holds on the docling path
    out2 = ex.extract(raw, "complex2.pdf")
    assert out.document_id == out2.document_id
