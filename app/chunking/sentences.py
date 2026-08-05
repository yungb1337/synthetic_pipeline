"""Deterministic, no-ML sentence splitting for chunking.

Boundary rule: a sentence ends at a run of sentence-final punctuation
(``. ! ? 。 ！ ？ …``) that is followed by whitespace and a capital/digit, or
directly by a CJK ideograph (CJK text has no inter-sentence whitespace). A
small abbreviation/initial guard suppresses false boundaries ("Dr. Smith",
"e.g. aspirin", "U.S.", "J. Smith") and a decimal guard suppresses bare-number
false splits in alphanumeric tokens ("BP120/80.", "Version2.0."); any boundary
the guards cannot resolve is left unsplit (conservative — fewer, larger
sentences).

Only stdlib. ``split_ambiguous`` counts suppressed boundary candidates so the
chunk report can surface how conservative the guard was.
"""
from __future__ import annotations

from .tokenize import TokenCounter

_FINAL_PUNCT = ".!?。！？…"

# abbreviation tokens, WITHOUT the trailing period ("e.g." -> "e.g").
_ABBREV_TOKENS = {
    "dr", "mr", "mrs", "ms", "st", "vs", "etc", "inc", "jr", "sr",
    "e.g", "i.e", "u.s", "u.k",
}


def _is_cjk(ch: str) -> bool:
    """Basic CJK Unified Ideographs (a sentence boundary can follow directly)."""
    return 0x4E00 <= ord(ch) <= 0x9FFF


def _abbrev_guard(text: str, i: int) -> bool:
    """True when the candidate boundary at ``i`` is an abbreviation/initial."""
    m = i
    while m > 0 and (text[m - 1].isalpha() or text[m - 1] == "."):
        m -= 1
    token = text[m:i]
    if not token:
        return False
    if token.lower() in _ABBREV_TOKENS:
        return True
    # single initial-capped token: "J. Smith"
    return len(token) == 1 and token.isupper()


def _decimal_guard(text: str, i: int) -> bool:
    """True when the candidate boundary at ``i`` is a decimal false-split.

    A ``.`` directly after a digit that is part of an alphanumeric run —
    ``BP120/80.``, ``Version2.0.`` — is NOT a sentence boundary. A plain number
    (``The dose is 45. He improved.``) still ends a sentence; only runs that
    also contain a letter are suppressed, so numeric-heavy medical text does not
    fragment mid-token. Deterministic; suppressed candidates count toward
    ``split_ambiguous`` like abbreviation/initial boundaries.
    """
    m = i
    if m == 0 or not text[m - 1].isdigit():
        return False
    has_letter = False
    while m > 0 and (text[m - 1].isalnum() or text[m - 1] in "/.-"):
        if text[m - 1].isalpha():
            has_letter = True
        m -= 1
    return has_letter


def split_sentences(text: str) -> tuple[list[str], int]:
    """Split ``text`` into sentences.

    Returns ``(sentences, split_ambiguous)`` where ``split_ambiguous`` counts
    candidate boundaries suppressed by the abbreviation/initial and decimal
    guards.
    """
    text = text or ""
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(text)
    ambiguous = 0
    while i < n:
        if text[i] not in _FINAL_PUNCT:
            i += 1
            continue
        # guard before any split decision
        if _abbrev_guard(text, i):
            ambiguous += 1
            i += 1
            continue
        j = i
        while j < n and text[j] in _FINAL_PUNCT:      # consume the punct run
            j += 1
        k = j
        while k < n and text[k] in " \t\n\r\f\v":     # skip whitespace
            k += 1
        if k >= n:
            # end of text: final sentence (no trailing empty sentence)
            sentences.append(text[start:].strip())
            start = n
            i = n
            continue
        nxt = text[k]
        if nxt.isupper() or nxt.isdigit() or _is_cjk(nxt):
            if _decimal_guard(text, i):
                ambiguous += 1
                i = j  # decimal false-split ("BP120/80."); keep scanning
                continue
            sentences.append(text[start:k].strip())
            start = k
            i = k
        else:
            i = j  # punct consumed but not a boundary; keep scanning
    tail = text[start:].strip()
    if tail or not sentences:
        if tail:
            sentences.append(tail)
    return sentences, ambiguous


def tail_sentences(text: str, counter: TokenCounter, budget_tokens: int) -> list[str]:
    """The final complete sentence(s) of ``text``, bounded by ``budget_tokens``.

    Accumulates from the end while the running token sum stays <= budget;
    always returns >= 1 sentence when ``text`` has sentences; order preserved
    (chronological). Used for the heading-seam overlap. A text with no
    sentence-final punctuation is treated as a single sentence.
    """
    sentences, _ = split_sentences(text)
    if not sentences:
        return []
    tail: list[str] = []
    total = 0
    for s in reversed(sentences):
        c = counter.count(s)
        if tail and total + c > budget_tokens:
            break
        tail.append(s)
        total += c
    tail.reverse()
    return tail
