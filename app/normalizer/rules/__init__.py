"""Extensible normalization rules registry and pipeline components.

Provides pure, deterministic, idempotent transformation rules with support
for runtime extension without core modifications.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from .base import RuleContext, RuleFunction, RuleResult
from .dehyphenate import dehyphenate
from .hygiene import strip_controls
from .typography import typography
from .unicode import expand_ligatures, nfc, nfkc, unicode
from .whitespace import collapse_whitespace

# Canonical execution sequence for rules
RULE_ORDER: list[str] = [
    "strip_controls",
    "unicode",
    "expand_ligatures",
    "dehyphenate",
    "collapse_whitespace",
    "typography",
]

# Rule registry mapping ID to function
RULE_MAP: dict[str, RuleFunction] = {
    "strip_controls": strip_controls,
    "unicode": unicode,
    "nfc": nfc,
    "nfkc": nfkc,
    "expand_ligatures": expand_ligatures,
    "dehyphenate": dehyphenate,
    "collapse_whitespace": collapse_whitespace,
    "typography": typography,
}


def register_rule(rule_id: str, fn: RuleFunction, index: int | None = None) -> None:
    """Register a custom normalization rule at runtime.

    Allows future domain-specific enhancements without modifying core files.
    """
    RULE_MAP[rule_id] = fn
    if rule_id not in RULE_ORDER:
        if index is not None and 0 <= index <= len(RULE_ORDER):
            RULE_ORDER.insert(index, rule_id)
        else:
            RULE_ORDER.append(rule_id)


def get_rule(rule_id: str) -> RuleFunction:
    """Retrieve a rule function by its identifier."""
    if rule_id not in RULE_MAP:
        raise KeyError(
            f"Unknown normalizer rule ID: '{rule_id}'. Available: {list(RULE_MAP.keys())}"
        )
    return RULE_MAP[rule_id]


__all__ = [
    "RULE_MAP",
    "RULE_ORDER",
    "RuleContext",
    "RuleFunction",
    "RuleResult",
    "collapse_whitespace",
    "dehyphenate",
    "expand_ligatures",
    "get_rule",
    "nfc",
    "nfkc",
    "register_rule",
    "strip_controls",
    "typography",
    "unicode",
]
