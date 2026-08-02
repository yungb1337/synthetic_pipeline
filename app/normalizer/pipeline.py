"""Compose normalization rules in a fixed, idempotent order.

`apply(text, rule_ids)` returns the normalized text plus a changed-map
(`rule_id -> bool`) for reporting. Because every rule is individually
idempotent and the order is fixed, the whole pipeline is idempotent:
apply(apply(x)) == apply(x).
"""
from __future__ import annotations

from . import rules


def apply(text: str, rule_ids: list[str]) -> tuple[str, dict[str, bool]]:
    out = text
    changed: dict[str, bool] = {}
    for rid in rule_ids:
        fn = rules.RULE_MAP[rid]
        after, was = fn(out)
        changed[rid] = was
        if was:
            out = after
    return out, changed


def is_idempotent(text: str, rule_ids: list[str]) -> bool:
    once, _ = apply(text, rule_ids)
    twice, _ = apply(once, rule_ids)
    return once == twice