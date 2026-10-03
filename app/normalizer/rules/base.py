"""Base types, context, and interfaces for normalization rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuleContext:
    """Contextual metadata passed to normalization rules for structure awareness."""

    kind: str = "paragraph"  # "paragraph", "heading", "code", "formula", "list_item", "table_cell", "caption", "reference", "metadata"
    unicode_form: str = "NFC"
    preserve_paragraph_breaks: bool = True
    preserve_code_blocks: bool = True
    preserve_formulas: bool = True
    preserve_multilingual_zwnj: bool = True
    preserve_compound_hyphens: bool = True
    custom_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleResult:
    """Typed result from a normalization rule execution."""

    text: str
    changed: bool
    rule_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# Rule function signature: (text: str, context: RuleContext | None) -> tuple[str, bool] | RuleResult
RuleFunction = Callable[..., tuple[str, bool] | RuleResult]
