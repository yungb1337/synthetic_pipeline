"""Comprehensive test suite for Module #2 — Text Normalization (deterministic, production-grade)."""

from __future__ import annotations

from app.normalizer import Normalizer, NormalizerConfig, apply, is_idempotent, rules
from app.normalizer.rules.base import RuleContext
from app.parser.dom import (
    Annotation,
    Block,
    Cell,
    Document,
    ImageObject,
    Metadata,
    Page,
    Provenance,
    Reference,
    Row,
    Table,
)

RULE_IDS = NormalizerConfig().enabled_rule_ids


def _b(seq: int, text: str, kind: str = "paragraph") -> Block:
    return Block(id=f"d1/b00_{seq:04d}", text=text, page=0, kind=kind)


def _doc(
    blocks: list[Block] | None = None,
    tables: list[Table] | None = None,
    images: list[ImageObject] | None = None,
    annotations: list[Annotation] | None = None,
    references: list[Reference] | None = None,
    metadata: Metadata | None = None,
) -> Document:
    blocks = blocks or []
    tables = tables or []
    images = images or []
    annotations = annotations or []
    references = references or []
    metadata = metadata or Metadata()

    return Document(
        version="dom-schema-v0.1.0",
        document_id="d-test",
        source_hash="00",
        metadata=metadata,
        provenance=Provenance(
            parser_version="p", dom_schema_version="dom-schema-v0.1.0"
        ),
        reading_order=[b.id for b in blocks],
        pages=[
            Page(
                index=0,
                blocks=blocks,
                tables=tables,
                images=images,
                annotations=annotations,
            )
        ],
        references=references,
    )


# =========================================================================
# 1. Hygiene & Control Character Tests (Issue 1 & 6)
# =========================================================================


def test_hygiene_crlf_and_cr_normalized_to_lf():
    """CRLF and CR line endings must be standardized to LF."""
    raw = "Line 1\r\nLine 2\rLine 3\nLine 4"
    out, changed = rules.strip_controls(raw)
    assert out == "Line 1\nLine 2\nLine 3\nLine 4"
    assert changed is True


def test_hygiene_tab_converted_to_space_not_deleted():
    """Audit Fix: Tabs must convert to spaces, never deleted to empty string."""
    raw = "Patient:\tJohn Doe\tAge:\t45"
    out, changed = rules.strip_controls(raw)
    assert out == "Patient: John Doe Age: 45"
    assert "Patient:John" not in out
    assert changed is True


def test_hygiene_tab_in_code_block_expands_to_4_spaces():
    """In code blocks, tabs must expand to 4 spaces."""
    raw = "def foo():\n\treturn 42"
    ctx = RuleContext(kind="code", preserve_code_blocks=True)
    out, changed = rules.strip_controls(raw, context=ctx)
    assert out == "def foo():\n    return 42"
    assert changed is True


def test_hygiene_strips_c0_c1_controls_and_bom():
    """C0 controls (except tab/lf), DEL, and BOM must be removed."""
    raw = "\x00\x01\x08Header\x0b\x0c\x1f\x7f ﻿Text"
    out, changed = rules.strip_controls(raw)
    assert out == "Header Text"
    assert changed is True


def test_hygiene_strips_zero_width_space():
    """Zero-width space (U+200B) and Word Joiner (U+2060) must be stripped."""
    raw = "Zero\u200bWidth⁠Space"
    out, changed = rules.strip_controls(raw)
    assert out == "ZeroWidthSpace"
    assert changed is True


def test_hygiene_preserves_multilingual_zwnj_and_zwj():
    """Audit Fix: Persian/Arabic ZWNJ (U+200C) and Indic ZWJ (U+200D) must be preserved."""
    # Persian: "می‌خواهم" (I want) uses ZWNJ between "می" and "خواهم"
    persian_text = "می‌خواهم"
    ctx = RuleContext(preserve_multilingual_zwnj=True)
    out, changed = rules.strip_controls(persian_text, context=ctx)
    assert out == persian_text
    assert "‌" in out

    # If explicitly disabled, ZWNJ is stripped
    ctx_strip = RuleContext(preserve_multilingual_zwnj=False)
    out_strip, changed_strip = rules.strip_controls(persian_text, context=ctx_strip)
    assert "‌" not in out_strip
    assert changed_strip is True


# =========================================================================
# 2. Unicode Canonicalization & Ligature Tests (Issue 2)
# =========================================================================


def test_unicode_nfc_preserves_superscripts_and_exponents():
    """Audit Fix: NFC baseline preserves exponents and superscripts (preventing 100x dosage error)."""
    # 10² mg must NOT become 102 mg!
    raw = "Dose: 10² mg, Bacterial count: 5 × 10⁴ CFU/mL"
    out, _ = rules.unicode(raw)
    assert "10² mg" in out
    assert "102 mg" not in out
    assert "10⁴ CFU/mL" in out
    assert "104 CFU/mL" not in out


def test_unicode_nfc_preserves_chemistry_and_fractions():
    """NFC baseline preserves subscripts, chemical valences, and fractions."""
    raw = "Formula: H₂O + Ca²⁺ + CO₂, Dosage: ½ tablet at 37°C"
    out, _ = rules.unicode(raw)
    assert "H₂O" in out
    assert "Ca²⁺" in out
    assert "CO₂" in out
    assert "½" in out
    assert "37°C" in out


def test_unicode_nfc_composes_combining_diacritics():
    """Canonical decomposition followed by canonical composition (NFC)."""
    # 'e' + combining acute accent U+0301 -> 'é' U+00E9
    decomposed = "étude"
    out, changed = rules.unicode(decomposed)
    assert out == "étude"
    assert changed is True


def test_unicode_targeted_ligatures_expanded():
    """Audit Fix: Typographic ligatures expand cleanly without NFKC side effects."""
    raw = (
        "The patient suffered from severe ﬁbrosis and pulmonary inﬂammation (ﬀ, ﬃ, ﬄ)."
    )
    out, changed = rules.expand_ligatures(raw)
    assert "fibrosis" in out
    assert "inflammation" in out
    assert "ff, ffi, ffl" in out
    assert changed is True


def test_unicode_ligature_expansion_preserves_math():
    """Ligature expansion does not modify mathematical powers or fractions."""
    raw = "ﬂuid level: 10² mL, ½ cup"
    out, _ = rules.expand_ligatures(raw)
    assert "fluid level" in out
    assert "10² mL" in out
    assert "½ cup" in out


# =========================================================================
# 3. Context-Aware Dehyphenation Tests (Issue 4)
# =========================================================================


def test_dehyphenate_lowercase_word_break():
    """Standard lowercase word broken across line break."""
    out, changed = rules.dehyphenate("para-\ngraph break")
    assert out == "paragraph break"
    assert changed is True


def test_dehyphenate_titlecase_word_break():
    """Audit Fix: TitleCase words broken across line break."""
    out, changed = rules.dehyphenate("Cardio-\nvascular disease and Hyper-\ntension")
    assert out == "Cardiovascular disease and Hypertension"
    assert changed is True


def test_dehyphenate_uppercase_word_break():
    """Audit Fix: Uppercase words broken across line break."""
    out, changed = rules.dehyphenate("DIAG-\nNOSIS confirmed")
    assert out == "DIAGNOSIS confirmed"
    assert changed is True


def test_dehyphenate_latin_extended_and_diacritics():
    """Audit Fix: European / accented words broken across line break."""
    out, changed = rules.dehyphenate("télé-\nphone and Über-\ntragung")
    assert out == "téléphone and Übertragung"
    assert changed is True


def test_dehyphenate_soft_hyphen_removed_without_space_severing():
    """Audit Fix: Soft hyphens (U+00AD) removed cleanly without severing words."""
    raw = "para­\ngraph and multi­center"
    out, changed = rules.dehyphenate(raw)
    assert out == "paragraph and multicenter"
    assert "para graph" not in out
    assert changed is True


def test_dehyphenate_guards_legitimate_compound_words():
    """Audit Fix: Legitimate scientific/medical compounds are guarded from fusion."""
    compounds = [
        ("cost-\neffective treatment", "cost-effective treatment"),
        ("evidence-\nbased guidelines", "evidence-based guidelines"),
        ("beta-\nblocker therapy", "beta-blocker therapy"),
        ("follow-\nup visit", "follow-up visit"),
        ("well-\nknown side-effects", "well-known side-effects"),
        ("NON-\nINVASIVE procedure", "NON-INVASIVE procedure"),
        ("double-\nblind trial", "double-blind trial"),
        ("peer-\nreviewed article", "peer-reviewed article"),
        ("x-\nray imaging", "x-ray imaging"),
    ]
    for raw, expected in compounds:
        out, changed = rules.dehyphenate(raw)
        assert out == expected
        assert changed is True


def test_dehyphenate_preserves_inline_real_hyphens():
    """Inline hyphenated words (not broken by newlines) must remain intact."""
    raw = "well-known cost-effective beta-blockers"
    out, changed = rules.dehyphenate(raw)
    assert out == raw
    assert changed is False


# =========================================================================
# 4. Structure-Aware Whitespace Tests (Issue 3)
# =========================================================================


def test_whitespace_collapses_horizontal_space():
    """Multiple spaces and tabs collapse to single space."""
    out, changed = rules.collapse_whitespace("  BP   :120/80  mmHg  ")
    assert out == "BP :120/80 mmHg"
    assert changed is True


def test_whitespace_preserves_paragraph_breaks():
    """Audit Fix: Two or more newlines are preserved as paragraph breaks (\\n\\n)."""
    raw = "Paragraph 1 line a\nline b.\n\n\nParagraph 2 line c\nline d."
    out, changed = rules.collapse_whitespace(raw)
    assert out == "Paragraph 1 line a line b.\n\nParagraph 2 line c line d."
    assert changed is True


def test_whitespace_preserves_code_blocks():
    """Audit Fix: Code blocks preserve indentation, newlines, and structure."""
    code = "def calculate_bmi(weight, height):\n    bmi = weight / (height ** 2)\n    return bmi"
    ctx = RuleContext(kind="code", preserve_code_blocks=True)
    out, changed = rules.collapse_whitespace(code, context=ctx)
    assert out == code
    assert "\n    bmi =" in out


def test_whitespace_preserves_formulas():
    """Audit Fix: Formulas preserve internal mathematical spacing."""
    formula = "E = m * c^2 + \\int_0^\\infty f(x) dx"
    ctx = RuleContext(kind="formula", preserve_formulas=True)
    out, _ = rules.collapse_whitespace(formula, context=ctx)
    assert out == formula


def test_whitespace_flat_mode_when_paragraph_breaks_disabled():
    """When preserve_paragraph_breaks=False, legacy flat collapse occurs."""
    raw = "Para 1\n\nPara 2"
    ctx = RuleContext(preserve_paragraph_breaks=False)
    out, changed = rules.collapse_whitespace(raw, context=ctx)
    assert out == "Para 1 Para 2"
    assert changed is True


def test_whitespace_idempotent_multiple_runs():
    """Repeated whitespace collapsing must be completely idempotent."""
    samples = [
        "a \n \n b",
        "  BP   : 120/80  \n\n  Heart  rate: 72  ",
        "def test():\n    return True\n",
    ]
    for s in samples:
        once, _ = rules.collapse_whitespace(s)
        twice, _ = rules.collapse_whitespace(once)
        assert once == twice


# =========================================================================
# 5. Typography & Punctuation Tests (Issue 5)
# =========================================================================


def test_typography_smart_quotes_to_straight():
    """Smart single and double quotes convert to ASCII straight quotes."""
    raw = "‘Patient’s’ chart: “Normal sinus rhythm” and „quoted‟"
    out, changed = rules.typography(raw)
    assert out == '\'Patient\'s\' chart: "Normal sinus rhythm" and "quoted"'
    assert changed is True


def test_typography_en_dash_to_hyphen():
    """En-dashes convert to hyphens."""
    raw = "pages 10–25, dose 5–10 mg"
    out, changed = rules.typography(raw)
    assert out == "pages 10-25, dose 5-10 mg"
    assert changed is True


def test_typography_unspaced_em_dash_separated():
    """Audit Fix: Unspaced em-dashes separated with spaces so words do not fuse."""
    raw = "The symptom—dyspnea—occurred during exertion."
    out, changed = rules.typography(raw)
    assert out == "The symptom - dyspnea - occurred during exertion."
    assert "symptom-dyspnea" not in out
    assert changed is True


def test_typography_non_breaking_spaces_to_regular_space():
    """Non-breaking spaces (NBSP, narrow NBSP) convert to regular spaces."""
    raw = "10 mg/dL IV"
    out, changed = rules.typography(raw)
    assert out == "10 mg/dL IV"
    assert " " not in out
    assert " " not in out
    assert changed is True


# =========================================================================
# 6. Pipeline & Idempotency Tests
# =========================================================================


def test_pipeline_composition_order():
    """Full pipeline executes in canonical order and tracks per-rule changes."""
    raw = "  Patient:	John\r\n‘Severe’ ﬁbrosis—10² mg  \n\nCardio-\nvascular  "
    out, changed = apply(raw, RULE_IDS)

    assert "Patient: John" in out
    assert "'Severe'" in out
    assert "fibrosis - 10² mg" in out
    assert "Cardiovascular" in out
    assert changed["strip_controls"] is True
    assert changed["expand_ligatures"] is True
    assert changed["dehyphenate"] is True
    assert changed["collapse_whitespace"] is True
    assert changed["typography"] is True


def test_pipeline_idempotency_guarantee():
    """Applying pipeline twice produces identical output on complex inputs."""
    samples = [
        "  Messy– texté  \r\n\r\npara-\ngraph  ",
        "Dose:\t10² mg/dL—check ﬁbrosis\n\nFollow-\nup next week.",
        "def run():\n\treturn 'ok'\n",
    ]
    for s in samples:
        assert is_idempotent(s, RULE_IDS)


# =========================================================================
# 7. Full DOM Traversal & Coverage Tests (Issue 7)
# =========================================================================


def test_normalizer_covers_blocks_with_structure_awareness():
    """Normalizer normalizes blocks and respects block kind."""
    para_block = _b(
        0,
        "The patient has  stable diabetes.\r\n\r\nFollow–up in 2 weeks.",
        kind="paragraph",
    )
    code_block = _b(1, "def check_dose():\n\treturn 10²", kind="code")
    doc = _doc(blocks=[para_block, code_block])

    out = Normalizer(NormalizerConfig()).normalize(doc)

    norm_para = out.pages[0].blocks[0]
    norm_code = out.pages[0].blocks[1]

    assert norm_para.text == "The patient has stable diabetes.\n\nFollow-up in 2 weeks."
    assert "def check_dose():\n    return 10²" in norm_code.text


def test_normalizer_covers_table_headers_cells_and_captions():
    """Audit Fix: Normalizer processes table headers, body cells, and table captions."""
    table = Table(
        id="t1",
        caption="Table 1: Patient	Lab–Results",
        header=["Test	Name", "Result	Value", "Reference	Range"],
        rows=[
            Row(
                cells=[
                    Cell(text="Glucose	(Fasting)"),
                    Cell(text="105	mg/dL"),
                    Cell(text="70–99	mg/dL"),
                ]
            ),
            Row(
                cells=[
                    Cell(text="HbA1c"),
                    Cell(text="6.2%"),
                    Cell(text="<5.7%"),
                ]
            ),
        ],
    )
    doc = _doc(tables=[table])

    out = Normalizer(NormalizerConfig()).normalize(doc)
    norm_table = out.pages[0].tables[0]

    # Caption
    assert norm_table.caption == "Table 1: Patient Lab-Results"
    # Header cells
    assert norm_table.header == ["Test Name", "Result Value", "Reference Range"]
    # Body cells
    assert norm_table.rows[0].cells[0].text == "Glucose (Fasting)"
    assert norm_table.rows[0].cells[1].text == "105 mg/dL"
    assert norm_table.rows[0].cells[2].text == "70-99 mg/dL"

    # Report verification
    rep = out.provenance.normalization_report
    assert rep["tables_seen"] == 1
    assert rep["table_cells_seen"] == 9  # 3 headers + 6 cells
    assert rep["table_cells_changed"] == 6  # 3 headers + 3 cells in row 1
    assert rep["captions_seen"] == 1
    assert rep["captions_changed"] == 1


def test_normalizer_covers_image_captions_and_annotations():
    """Audit Fix: Normalizer processes image captions and annotations."""
    image = ImageObject(id="img1", caption="Figure 1: Cross–sectional CT	Scan")
    annot = Annotation(text="Note: Patient’s	heart–rate elevated")
    doc = _doc(images=[image], annotations=[annot])

    out = Normalizer(NormalizerConfig()).normalize(doc)

    assert out.pages[0].images[0].caption == "Figure 1: Cross-sectional CT Scan"
    assert out.pages[0].annotations[0].text == "Note: Patient's heart-rate elevated"


def test_normalizer_covers_references():
    """Audit Fix: Normalizer processes references and citation targets."""
    ref = Reference(
        id="ref1",
        label="[1]–[3]",
        text="Smith et al. Cardio-\nvascular outcomes in 10² patients.",
    )
    doc = _doc(references=[ref])

    out = Normalizer(NormalizerConfig()).normalize(doc)
    norm_ref = out.references[0]

    assert norm_ref.label == "[1]-[3]"
    assert norm_ref.text == "Smith et al. Cardiovascular outcomes in 10² patients."


def test_normalizer_covers_metadata_fields():
    """Audit Fix: Normalizer canonicalizes metadata fields."""
    meta = Metadata(
        title="Clinical	Study: COVID–19",
        author="Dr.	Jane	Doe",
        subject="Non–invasive	Treatments",
    )
    doc = _doc(metadata=meta)

    out = Normalizer(NormalizerConfig()).normalize(doc)
    norm_meta = out.metadata

    assert norm_meta.title == "Clinical Study: COVID-19"
    assert norm_meta.author == "Dr. Jane Doe"
    assert norm_meta.subject == "Non-invasive Treatments"


# =========================================================================
# 8. Provenance, Extensibility & Architecture Tests
# =========================================================================


def test_provenance_and_version_attached():
    """Provenance carries normalizer version and comprehensive statistics."""
    doc = _doc(blocks=[_b(0, "  spaced	text  ")])
    out = Normalizer(NormalizerConfig()).normalize(doc)

    assert out.provenance.normalizer_version == "normalizer-v0.2.0"
    rep = out.provenance.normalization_report
    assert rep is not None
    assert rep["normalizer_version"] == "normalizer-v0.2.0"
    assert rep["blocks_seen"] == 1
    assert rep["blocks_changed"] == 1
    assert "rules" in rep
    assert rep["rule_counts"]["strip_controls"] >= 1


def test_runtime_custom_rule_registration():
    """Audit Fix: Extensible architecture allows adding custom rules at runtime."""

    def custom_redact_rule(
        text: str, context: RuleContext | None = None
    ) -> tuple[str, bool]:
        new = text.replace("SECRET", "[REDACTED]")
        return new, new != text

    rules.register_rule("custom_redact", custom_redact_rule)

    out, changed = apply("Classified: SECRET patient record", ["custom_redact"])
    assert out == "Classified: [REDACTED] patient record"
    assert changed["custom_redact"] is True


def test_config_snapshot_completeness():
    """NormalizerConfig snapshot contains all active feature flags for auditability."""
    cfg = NormalizerConfig()
    snap = cfg.snapshot()

    assert snap["normalizer_version"] == "normalizer-v0.2.0"
    assert snap["unicode_form"] == "NFC"
    assert snap["unfold_ligatures"] is True
    assert snap["preserve_paragraph_breaks"] is True
    assert snap["preserve_code_blocks"] is True
    assert snap["preserve_formulas"] is True
    assert snap["preserve_multilingual_zwnj"] is True
    assert snap["preserve_compound_hyphens"] is True
    assert snap["scope"]["blocks"] is True
    assert snap["scope"]["tables"] is True
    assert snap["scope"]["captions"] is True
    assert snap["scope"]["references"] is True
    assert snap["scope"]["metadata"] is True


def test_normalizer_non_destructive_deep_copy():
    """Normalizing a document never mutates the original document."""
    original_block = _b(0, "Original	Text–10²")
    doc = _doc(blocks=[original_block])

    out = Normalizer(NormalizerConfig()).normalize(doc)

    # Output document is changed
    assert out.pages[0].blocks[0].text == "Original Text-10²"
    # Original document is unchanged
    assert doc.pages[0].blocks[0].text == "Original	Text–10²"


# =========================================================================
# 9. Extended Edge Cases, Multilingual & Clinical Notation Tests
# =========================================================================


def test_empty_and_whitespace_only_inputs():
    """All rules must gracefully handle empty and whitespace-only strings."""
    for empty in ("", "   ", "\t", "\n\n", "\r\n"):
        for rule_id in RULE_IDS:
            fn = rules.get_rule(rule_id)
            res = fn(empty)
            assert isinstance(res, tuple)
            assert len(res) == 2


def test_clinical_abbreviations_and_blood_gases():
    """Arterial blood gases, clinical symbols, and pressures remain canonical."""
    raw = "pH: 7.38, PaO₂: 92 mmHg, PaCO₂: 40 mmHg, HCO₃⁻: 24 mEq/L, SaO₂: 98%"
    out, _ = apply(raw, RULE_IDS)
    assert "PaO₂: 92 mmHg" in out
    assert "PaCO₂: 40 mmHg" in out
    assert "HCO₃⁻: 24 mEq/L" in out


def test_chemical_compounds_and_ions():
    """Complex chemical formulas with subscripts and superscripts are preserved."""
    raw = "Reactions: H₂SO₄ + 2NaOH → Na₂SO₄ + 2H₂O; Fe³⁺ + e⁻ → Fe²⁺; C₆H₁₂O₆"
    out, _ = apply(raw, RULE_IDS)
    assert "H₂SO₄" in out
    assert "Na₂SO₄" in out
    assert "Fe³⁺" in out
    assert "Fe²⁺" in out
    assert "C₆H₁₂O₆" in out


def test_complex_code_block_multiline_preservation():
    """Multi-line code blocks retain indentation, blank lines, and symbols."""
    code = (
        "def query_patient(patient_id: str) -> dict:\n"
        "    # Fetch clinical record\n"
        "    sql = '''SELECT * FROM patients WHERE id = %s'''\n"
        "    params = (patient_id,)\n"
        "\n"
        "    return execute(sql, params)"
    )
    doc = _doc(blocks=[_b(0, code, kind="code")])
    out = Normalizer(NormalizerConfig()).normalize(doc)
    assert out.pages[0].blocks[0].text == code


def test_latex_formula_block_preservation():
    """LaTeX and MathML formulas in kind='formula' are protected."""
    formula = r"\sigma = \sqrt{\frac{1}{N} \sum_{i=1}^N (x_i - \mu)^2}"
    doc = _doc(blocks=[_b(0, formula, kind="formula")])
    out = Normalizer(NormalizerConfig()).normalize(doc)
    assert out.pages[0].blocks[0].text == formula


def test_multi_line_consecutive_hyphen_breaks():
    """Dehyphenation repairs multiple line-broken words across a paragraph."""
    raw = "The pa-\ntient re-\nceived inter-\nvention."
    out, changed = rules.dehyphenate(raw)
    assert out == "The patient received intervention."
    assert changed is True


def test_table_with_empty_and_mixed_cells():
    """Tables with empty strings, None-like, or already-clean cells handle cleanly."""
    table = Table(
        id="t2",
        header=["Col1", "Col2", ""],
        rows=[
            Row(cells=[Cell(text=""), Cell(text="Clean"), Cell(text="Messy	Cell")]),
            Row(cells=[Cell(text="10²"), Cell(text="5–10"), Cell(text="ﬁbrosis")]),
        ],
    )
    doc = _doc(tables=[table])
    out = Normalizer(NormalizerConfig()).normalize(doc)
    cells = out.pages[0].tables[0].rows
    assert cells[0].cells[0].text == ""
    assert cells[0].cells[1].text == "Clean"
    assert cells[0].cells[2].text == "Messy Cell"
    assert cells[1].cells[0].text == "10²"
    assert cells[1].cells[1].text == "5-10"
    assert cells[1].cells[2].text == "fibrosis"


def test_document_without_provenance_gets_provenance_created():
    """Defensive handling when input Document has provenance=None."""
    doc = _doc(blocks=[_b(0, "Sample text")])
    doc.provenance = None

    out = Normalizer(NormalizerConfig()).normalize(doc)
    assert out.provenance is not None
    assert out.provenance.normalizer_version == "normalizer-v0.2.0"
    assert out.provenance.normalization_report["blocks_seen"] == 1


def test_config_disabled_rules():
    """Feature flag toggles in NormalizerConfig disable selected rule stages."""
    cfg = NormalizerConfig(
        strip_controls=False,
        normalize_unicode=False,
        unfold_ligatures=False,
        dehyphenate=False,
        collapse_whitespace=False,
        fix_typography=False,
    )
    assert cfg.enabled_rule_ids == []

    doc = _doc(blocks=[_b(0, "  Unmodified	Text–ﬁbrosis  \n\npara-\ngraph")])
    out = Normalizer(cfg).normalize(doc)
    # When all rules disabled, block text is untouched
    assert (
        out.pages[0].blocks[0].text == "  Unmodified	Text–ﬁbrosis  \n\npara-\ngraph"
    )


def test_custom_unicode_form_nfkc():
    """Explicit unicode_form='NFKC' enables compatibility decomposition when configured."""
    cfg = NormalizerConfig(unicode_form="NFKC", unfold_ligatures=False)
    doc = _doc(blocks=[_b(0, "É  text")])
    out = Normalizer(cfg).normalize(doc)
    # Under NFKC, NBSP and compatible characters decompose
    assert " " not in out.pages[0].blocks[0].text


def test_cli_end_to_end(tmp_path):
    """CLI entry point reads parsed DOM from disk, normalizes, and writes out."""
    from app.normalizer import cli

    in_path = tmp_path / "parsed.dom.json"
    out_path = tmp_path / "normalized.dom.json"

    doc = _doc(blocks=[_b(0, "Patient:	John\r\n‘Severe’ ﬁbrosis—10² mg")])
    in_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    exit_code = cli.main(["--dom", str(in_path), "--out", str(out_path)])
    assert exit_code == 0
    assert out_path.exists()

    loaded = Document.model_validate_json(out_path.read_text(encoding="utf-8"))
    assert "Patient: John" in loaded.pages[0].blocks[0].text
    assert "fibrosis - 10² mg" in loaded.pages[0].blocks[0].text
    assert loaded.provenance.normalizer_version == "normalizer-v0.2.0"


def test_full_idempotency_f_f_x_equals_f_x_property():
    """Comprehensive property test: f(f(x)) == f(x) across varied DOM structures."""
    doc = _doc(
        blocks=[
            _b(0, "Header	1: Patient	Status", kind="heading"),
            _b(
                1,
                "The pa-\ntient has severe asthma—especially at night.\r\n\r\nDose: 10² mg.",
                kind="paragraph",
            ),
            _b(2, "SELECT	*	FROM	vitals;", kind="code"),
        ],
        tables=[
            Table(
                id="t_prop",
                caption="Table	1: Vitals–Summary",
                header=["Metric	Name", "Observed	Value"],
                rows=[Row(cells=[Cell(text="BP	(mmHg)"), Cell(text="120/80")])],
            )
        ],
        metadata=Metadata(title="Study	Report—2026", author="Dr.	Smith"),
    )

    norm = Normalizer(NormalizerConfig())
    pass1 = norm.normalize(doc)
    pass2 = norm.normalize(pass1)

    # All blocks match exactly
    for b1, b2 in zip(pass1.pages[0].blocks, pass2.pages[0].blocks):
        assert b1.text == b2.text

    # All table cells match exactly
    assert pass1.pages[0].tables[0].caption == pass2.pages[0].tables[0].caption
    assert pass1.pages[0].tables[0].header == pass2.pages[0].tables[0].header
    for r1, r2 in zip(pass1.pages[0].tables[0].rows, pass2.pages[0].tables[0].rows):
        for c1, c2 in zip(r1.cells, r2.cells):
            assert c1.text == c2.text

    # Metadata matches
    assert pass1.metadata.title == pass2.metadata.title
    assert pass1.metadata.author == pass2.metadata.author
