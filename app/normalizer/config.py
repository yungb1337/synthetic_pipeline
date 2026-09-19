"""Config for the document normalization module (Module #2).

Immutable; snapshot the whole struct into the DOM's normalization report so
any downstream module can reproduce exactly what was applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizerConfig:
    normalizer_version: str = "normalizer-v0.2.0"

    # Core rule family toggles
    strip_controls: bool = True
    normalize_unicode: bool = True
    unicode_form: str = "NFC"  # "NFC" (default, preserves exponents/chemistry) | "NFKC"
    unfold_ligatures: bool = True  # Expand ﬁ -> fi, ﬂ -> fl, etc.
    dehyphenate: bool = True
    collapse_whitespace: bool = True
    fix_typography: bool = True

    # Structural and domain-specific options
    preserve_paragraph_breaks: bool = True  # Preserve \n\n as paragraph separation
    preserve_code_blocks: bool = True       # Preserve indentation and newlines in kind="code"
    preserve_formulas: bool = True          # Preserve whitespace and notation in kind="formula"
    preserve_multilingual_zwnj: bool = True # Preserve ZWNJ/ZWJ for Persian, Arabic, Indic
    preserve_compound_hyphens: bool = True  # Guard legitimate compounds (e.g. cost-effective)

    # DOM traversal scope toggles
    normalize_blocks: bool = True
    normalize_tables: bool = True
    normalize_captions: bool = True
    normalize_references: bool = True
    normalize_metadata: bool = True

    @property
    def enabled_rule_ids(self) -> list[str]:
        """Ordered list of active rule IDs for the normalization pipeline."""
        rules: list[str] = []
        if self.strip_controls:
            rules.append("strip_controls")
        if self.normalize_unicode:
            rules.append("unicode")
        if self.unfold_ligatures:
            rules.append("expand_ligatures")
        if self.dehyphenate:
            rules.append("dehyphenate")
        if self.collapse_whitespace:
            rules.append("collapse_whitespace")
        if self.fix_typography:
            rules.append("typography")
        return rules

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable dictionary snapshot for provenance."""
        return {
            "normalizer_version": self.normalizer_version,
            "rules": self.enabled_rule_ids,
            "unicode_form": self.unicode_form,
            "unfold_ligatures": self.unfold_ligatures,
            "preserve_paragraph_breaks": self.preserve_paragraph_breaks,
            "preserve_code_blocks": self.preserve_code_blocks,
            "preserve_formulas": self.preserve_formulas,
            "preserve_multilingual_zwnj": self.preserve_multilingual_zwnj,
            "preserve_compound_hyphens": self.preserve_compound_hyphens,
            "scope": {
                "blocks": self.normalize_blocks,
                "tables": self.normalize_tables,
                "captions": self.normalize_captions,
                "references": self.normalize_references,
                "metadata": self.normalize_metadata,
            },
        }
