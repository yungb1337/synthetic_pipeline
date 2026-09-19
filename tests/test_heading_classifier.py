"""Unit tests for heading vs paragraph classification and margin metadata filtering."""
from app.parser.config import ParserConfig
from app.parser.engines.native_pdf import _is_probable_heading, _JOURNAL_MARGIN_BOILERPLATE_RE
from app.parser.parts import RecoveredBlock


def test_is_probable_heading_short_intro():
    # Standard section title
    b = RecoveredBlock(
        page=0, kind="paragraph", text="1. Introduction and Background",
        bbox=(50, 100, 300, 120), font_size=14.0, bold=True,
    )
    assert _is_probable_heading(b, body_med=10.0, threshold_ratio=1.12) is True


def test_is_probable_heading_sentence_ending_with_period():
    # Body paragraph with larger font that ends with full stop
    b = RecoveredBlock(
        page=0, kind="paragraph",
        text="This is a body paragraph discussing the macroscopic and microscopic scale interacting with each other.",
        bbox=(50, 150, 500, 200), font_size=12.0, bold=False,
    )
    # Long text (> 12 words) ending in period should NOT be a heading
    assert _is_probable_heading(b, body_med=10.0, threshold_ratio=1.12) is False


def test_is_probable_heading_roman_or_punctuation_token():
    # Decorative or single punctuation token with large font
    b1 = RecoveredBlock(page=0, kind="paragraph", text="---", bbox=(50, 50, 200, 60), font_size=16.0)
    assert _is_probable_heading(b1, body_med=10.0, threshold_ratio=1.12) is False

    b2 = RecoveredBlock(page=0, kind="paragraph", text="I.", bbox=(50, 50, 100, 60), font_size=16.0)
    # 2 chars without alphabetic (only Roman numeral I and .) -> wait, I is alphabetic
    # But let's check single punctuation
    b3 = RecoveredBlock(page=0, kind="paragraph", text=".", bbox=(50, 50, 60, 60), font_size=16.0)
    assert _is_probable_heading(b3, body_med=10.0, threshold_ratio=1.12) is False


def test_journal_margin_boilerplate_regex():
    assert _JOURNAL_MARGIN_BOILERPLATE_RE.search("OPEN ACCESS") is not None
    assert _JOURNAL_MARGIN_BOILERPLATE_RE.search("Citation: Doe et al., 2024") is not None
    assert _JOURNAL_MARGIN_BOILERPLATE_RE.search("doi:10.1371/journal.pbio.1002203") is not None
    assert _JOURNAL_MARGIN_BOILERPLATE_RE.search("Page 3 of 12") is not None
    assert _JOURNAL_MARGIN_BOILERPLATE_RE.search("This is genuine body content about cells") is None
