"""Tests for Module #2 — Text Normalization (deterministic, conservative)."""
from __future__ import annotations

from app.normalizer import pipeline, rules
from app.normalizer.config import NormalizerConfig
from app.normalizer.normalizer import Normalizer
from app.parser.dom import Block, Document, Metadata, Page, Provenance

RULE_IDS = NormalizerConfig().enabled_rule_ids


def _b(seq: int, text: str) -> Block:
    return Block(id=f"d1/b00_{seq:04d}", text=text, page=0)


def _doc(blocks) -> Document:
    return Document(
        version="dom-schema-v0.1.0",
        document_id="d-test",
        source_hash="00",
        metadata=Metadata(),
        provenance=Provenance(parser_version="p", dom_schema_version="dom-schema-v0.1.0"),
        reading_order=[b.id for b in blocks],
        pages=[Page(index=0, blocks=blocks)],
    )


# ---- rule-level ---------------------------------------------------------
def test_rule_dehyphenate():
    out, changed = rules.dehyphenate("para-\ngraph break")
    assert out == "paragraph break"
    assert changed is True


def test_rule_dehyphenate_preserves_real_hyphen():
    out, _ = rules.dehyphenate("well-known drug")
    assert out == "well-known drug"


def test_rule_collapse():
    out, changed = rules.collapse_whitespace("  BP   :120/80  ")
    assert out == "BP :120/80"
    assert changed is True


def test_rule_nfkc():
    out, changed = rules.nfkc("É  text")  # É + nbsp
    assert " " not in out


def test_rule_typography():
    out, changed = rules.typography("heart – risk — and “quotes”")
    assert out == 'heart - risk - and "quotes"'
    assert changed is True


# ---- pipeline ------------------------------------------------------------
def test_pipeline_idempotent():
    text = "  Messy– texté  \n\npara-\ngraph  "
    once, _ = pipeline.apply(text, RULE_IDS)
    twice, _ = pipeline.apply(once, RULE_IDS)
    assert once == twice


# ---- integration ----------------------------------------------------------
def test_normalize_cleans_blocks():
    doc = _doc([_b(0, "The patient has  stable diabetes.\n\nFollow–up in 2 weeks.")])
    out = Normalizer(NormalizerConfig()).normalize(doc)
    text = out.pages[0].blocks[0].text
    assert "Follow-up" in text


def test_normalize_idempotent():
    n = Normalizer(NormalizerConfig())
    doc = _doc([_b(0, "  Spacedé  \n\npara-\ngraph  ")])
    once = n.normalize(doc)
    twice = n.normalize(once)
    assert [b.text for b in once.pages[0].blocks] == [b.text for b in twice.pages[0].blocks]


def test_normalize_preserves_structure():
    blocks = [_b(0, "A  B"), _b(1, "C  D")]
    doc = _doc(blocks)
    out = Normalizer(NormalizerConfig()).normalize(doc)
    assert out.reading_order == doc.reading_order
    assert [b.id for b in out.pages[0].blocks] == [b.id for b in blocks]
    assert out.metadata == doc.metadata


def test_report_and_version_attached():
    out = Normalizer(NormalizerConfig()).normalize(_doc([_b(0, "  spaced    text")]))
    assert out.provenance.normalizer_version == "normalizer-v0.1.0"
    rep = out.provenance.normalization_report
    assert rep and rep["blocks_seen"] == 1


def test_already_clean_is_noop():
    out = Normalizer(NormalizerConfig()).normalize(_doc([_b(0, "Clean text here.")]))
    assert out.pages[0].blocks[0].text == "Clean text here."