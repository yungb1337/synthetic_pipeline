"""Config for the normalization module.

Immutable; snapshot the whole struct into the DOM's normalization report so
any downstream module can reproduce exactly what was applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NormalizerConfig:
    normalizer_version: str = "normalizer-v0.1.0"
    # toggles: disable a rule family entirely (useful for debugging / auditing)
    strip_controls: bool = True
    normalize_unicode: bool = True
    dehyphenate: bool = True
    collapse_whitespace: bool = True
    fix_typography: bool = True
    # enabled rule ids, in run order (derived from toggles)
    @property
    def enabled_rule_ids(self) -> list[str]:
        order = {
            "strip_controls": self.strip_controls,
            "nfkc": self.normalize_unicode,
            "dehyphenate": self.dehyphenate,
            "collapse_whitespace": self.collapse_whitespace,
            "typography": self.fix_typography,
        }
        return [k for k, v in order.items() if v]

    def snapshot(self) -> dict:
        return {
            "normalizer_version": self.normalizer_version,
            "rules": self.enabled_rule_ids,
        }