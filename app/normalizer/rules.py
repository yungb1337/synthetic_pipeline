"""Deterministic, idempotent text-normalization rules.

Each rule is `(text: str) -> (text: str, changed: bool)`. Rules are composed
in a fixed order (see `apply_rules`) so a second pass is a no-op.

Design intent: normalize *formatting* only, NEVER content. Numbers, units
(uL, mg/dL, °C), and clinical tokens are preserved verbatim. Synonym/ontology
resolution is intentionally excluded (that's the Ontology module).
"""
from __future__ import annotations

import re
import unicodedata

# ---- rule 1: strip control chars / BOM --------------------------------
# C0 controls + DEL + zero-width chars, built programmatically (never paste
# literal control chars into source).
def _control_class() -> str:
    ranges = [(0x00, 0x09), (0x0B, 0x0C), (0x0E, 0x1F)]
    single = [0x7F, 0x200B, 0x200C, 0x200D, 0xFEFF]
    out = []
    for lo, hi in ranges:
        out.extend(chr(c) for c in range(lo, hi + 1))
    out.extend(chr(c) for c in single)
    return "".join(out)


_CONTROL_RE = re.compile("[" + _control_class() + "]")


def strip_controls(text: str) -> tuple[str, bool]:
    new = _CONTROL_RE.sub("", text)
    return new, new != text


# ---- rule 2: Unicode normalization (NFKC) ------------------------------
def nfkc(text: str) -> tuple[str, bool]:
    new = unicodedata.normalize("NFKC", text)
    return new, new != text


# ---- rule 3: dehyphenate broken words across line breaks -----------------
# "para-\ngraph" and "para- nent" -> "paragraph". Only joins when both sides
# are lowercase-ish word fragments; leaves real hyphens intact (e.g. "well-known").
_HYPHEN_JOIN_RE = re.compile(r"(\b[a-záéíóúüñ]+)-\s*\n\s*([a-záéíóúüñ]+\b)")

def dehyphenate(text: str) -> tuple[str, bool]:
    new = _HYPHEN_JOIN_RE.sub(r"\1\2", text)
    return new, new != text


# ---- rule 4: collapse whitespace --------------------------------------------
# Collapse every run of whitespace (space/tab/newline) to a single space.
# The newline branch consumes the whole run (leading space/tab, newlines,
# trailing space/tab) so two adjacent matches can never each emit a space.
_WS_RE = re.compile(r"(?:[ \t]*\n)+[ \t]*|[ \t]{2,}")

def collapse_whitespace(text: str) -> tuple[str, bool]:
    new = _WS_RE.sub(" ", text).strip()
    return new, new != text


# ---- rule 5: typography / punctuation ----------------------------------------
# Smart quotes -> straight; en/em dashes -> hyphen; non-breaking space -> space.
_TYPOG = {
    "‘": "'",  # ' '
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",   # en dash
    "—": "-",   # em dash (use as connector; NOT sentence dash removal)
    " ": " ",
    "­": "",    # soft hyphen
}

def typography(text: str) -> tuple[str, bool]:
    new = text
    for src, dst in _TYPOG.items():
        if src in new:
            new = new.replace(src, dst)
    return new, new != text


# ordered pipeline; each entry is (rule_id, function)
RULE_ORDER: list[str] = ["strip_controls", "nfkc", "dehyphenate", "collapse_whitespace", "typography"]

# registry: rule_id -> callable
RULE_MAP = {
    "strip_controls": strip_controls,
    "nfkc": nfkc,
    "dehyphenate": dehyphenate,
    "collapse_whitespace": collapse_whitespace,
    "typography": typography,
}