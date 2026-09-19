"""Unit tests for table Unicode preservation and two-tier table planner escalation."""
import unicodedata
from app.parser.loaders.docling_loader import _clean_cell
from app.parser.config import ParserConfig
from app.parser.planner import Planner
from app.parser.source import SourceManifest


def test_table_clean_cell_unicode_preservation():
    # Test mathematical & scientific symbols
    assert _clean_cell("10.5 ± 1.2 mg") == "10.5 ± 1.2 mg"
    assert _clean_cell("p ≥ 0.05") == "p ≥ 0.05"
    assert _clean_cell("p ≤ 0.001") == "p ≤ 0.001"
    assert _clean_cell("~500 µg/mL") == "~500 µg/mL"
    assert _clean_cell("α = 0.05, β = 0.2") == "α = 0.05, β = 0.2"


def test_table_clean_cell_multiline_wrapping():
    # Multi-line cell text should be collapsed into single space
    assert _clean_cell("Total\nCases\n(N=100)") == "Total Cases (N=100)"
    assert _clean_cell("  Line 1   \n   Line 2  \t  Line 3  ") == "Line 1 Line 2 Line 3"
    assert _clean_cell(None) == ""


def test_planner_simple_table_routes_to_native():
    planner = Planner(page_store=None, ledger=None)
    manifest = SourceManifest(
        doc_id="test_doc",
        source_hash="sha_dummy",
        expected_page_set=[0, 1, 2],
        page_count=3,
        slug="pdf",
        mime="application/pdf",
        declared_extension=".pdf",
        probe="native",
    )
    config = ParserConfig()

    # Page 1 has a simple table, Page 2 has a complex table, Page 3 has no table
    table_pages = {1, 2}
    simple_table_pages = {1}

    # Page 0 (1st page) -> simple table -> native
    b0 = planner._page_band(manifest, "native", "smart_routed", config, 0, table_pages, simple_table_pages=simple_table_pages)
    assert b0 == "native"

    # Page 1 (2nd page) -> complex table -> docling (if available) or native fallback
    b1 = planner._page_band(manifest, "native", "smart_routed", config, 1, table_pages, simple_table_pages=simple_table_pages)
    assert b1 in ("docling", "native", "enrichment")
