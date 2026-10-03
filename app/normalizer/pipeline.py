"""Compose normalization rules in a fixed, idempotent order.

`apply(text, rule_ids, context=None)` executes the configured rules sequentially,
tracking per-rule execution and returning the normalized text with a change map.

Guarantees idempotency across all supported rule compositions:
    apply(apply(x)) == apply(x)
"""

from __future__ import annotations

import inspect

from . import rules
from .rules.base import RuleContext, RuleResult


def apply(
    text: str,
    rule_ids: list[str],
    context: RuleContext | None = None,
) -> tuple[str, dict[str, bool]]:
    """Execute a list of rule IDs in order over the input text.

    Args:
        text: The source string to normalize.
        rule_ids: List of rule IDs to execute (e.g. ['strip_controls', 'unicode', ...]).
        context: Optional structural and domain context (block kind, toggles).

    Returns:
        tuple of (normalized_text, changed_map) where changed_map is {rule_id: bool}.
    """
    out = text
    changed: dict[str, bool] = {}
    ctx = context or RuleContext()

    for rid in rule_ids:
        fn = rules.get_rule(rid)
        try:
            # Check if function accepts context parameter
            sig = inspect.signature(fn)
            if len(sig.parameters) >= 2 or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                res = fn(out, context=ctx)
            else:
                res = fn(out)
        except TypeError:
            # Fallback for simple callables
            res = fn(out)

        if isinstance(res, RuleResult):
            after = res.text
            was = res.changed
        elif isinstance(res, tuple) and len(res) == 2:
            after, was = res
        else:
            raise ValueError(f"Rule '{rid}' returned invalid result: {res!r}")

        changed[rid] = was
        if was:
            out = after

    return out, changed


def is_idempotent(
    text: str,
    rule_ids: list[str],
    context: RuleContext | None = None,
) -> bool:
    """Verify that applying the pipeline twice produces identical output."""
    once, _ = apply(text, rule_ids, context=context)
    twice, _ = apply(once, rule_ids, context=context)
    return once == twice
