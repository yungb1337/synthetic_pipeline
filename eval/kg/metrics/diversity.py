"""Diversity and Lexical Entropy Metrics for Synthetic Datasets."""

from __future__ import annotations

import math
import re
from collections import Counter


def compute_vocabulary_entropy(texts: list[str]) -> float:
    """Computes Shannon entropy of vocabulary distribution across texts."""
    tokens: list[str] = []
    for t in texts:
        tokens.extend([w.lower() for w in re.findall(r"\b\w+\b", t)])

    if not tokens:
        return 0.0

    counts = Counter(tokens)
    total = len(tokens)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)

    return entropy


def compute_average_length(texts: list[str]) -> float:
    """Computes average word length across generated texts."""
    if not texts:
        return 0.0
    lengths = [len(re.findall(r"\b\w+\b", t)) for t in texts]
    return sum(lengths) / len(lengths)
