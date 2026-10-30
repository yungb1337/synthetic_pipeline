"""Gate 6: Self-BLEU & SimHash De-Duplication.

Detects exact and near-duplicate synthetic records using 64-bit SimHash
(Hamming distance <= 3) and batch Self-BLEU to prevent repetitive collapse,
with bounded in-memory sliding window deques.
"""

from __future__ import annotations

import collections
import hashlib
import re
from collections import Counter
from typing import Any

from app.synthetic.schema import SyntheticRecord
from app.synthetic.trust.protocol import GateResult


def compute_simhash(text: str, hash_bits: int = 64) -> int:
    """Computes a 64-bit SimHash fingerprint for text."""
    tokens = [t.lower() for t in re.findall(r"\b\w+\b", text)]
    if not tokens:
        return 0

    v = [0] * hash_bits
    for token in tokens:
        # MD5 digest mapped to 64-bit integer
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:16], 16)
        for i in range(hash_bits):
            bit = (h >> i) & 1
            v[i] += 1 if bit == 1 else -1

    fingerprint = 0
    for i in range(hash_bits):
        if v[i] > 0:
            fingerprint |= 1 << i

    return fingerprint


def hamming_distance(hash1: int, hash2: int) -> int:
    """Computes Hamming distance between two integer hashes using hardware-accelerated POPCNT."""
    return (hash1 ^ hash2).bit_count()


def calculate_sentence_bleu(reference: list[str], candidate: list[str], max_n: int = 2) -> float:
    """Lightweight pure-Python sentence BLEU calculation."""
    if not reference or not candidate:
        return 0.0

    scores: list[float] = []
    for n in range(1, max_n + 1):
        ref_ngrams = Counter([tuple(reference[i : i + n]) for i in range(len(reference) - n + 1)])
        cand_ngrams = Counter([tuple(candidate[i : i + n]) for i in range(len(candidate) - n + 1)])

        if not cand_ngrams or not ref_ngrams:
            scores.append(0.0)
            continue

        clipped = sum(min(count, ref_ngrams[ng]) for ng, count in cand_ngrams.items())
        total = sum(cand_ngrams.values())
        scores.append(clipped / total if total > 0 else 0.0)

    # Geometric mean
    prod = 1.0
    for s in scores:
        prod *= max(s, 1e-6)
    return prod ** (1.0 / len(scores))


class DeDuplicationGate:
    """Gate 6: Rejects redundant or semantically identical synthetic records."""

    def __init__(
        self,
        max_hamming_distance: int = 3,
        max_self_bleu: float = 0.35,
        max_window_size: int = 10000,
    ) -> None:
        self.max_hamming_distance = max_hamming_distance
        self.max_self_bleu = max_self_bleu
        self.max_window_size = max_window_size
        self._seen_hashes: collections.deque[int] = collections.deque(maxlen=max_window_size)
        self._seen_tokens: collections.deque[list[str]] = collections.deque(maxlen=max_window_size)
        self._seen_exact: set[int] = set()

    def reset(self) -> None:
        """Clears memory index."""
        self._seen_hashes.clear()
        self._seen_tokens.clear()
        self._seen_exact.clear()

    def evaluate(self, record: SyntheticRecord, **kwargs: Any) -> GateResult:
        """Evaluates whether the record is a duplicate of previously seen records."""
        combined_text = f"{record.instruction} {record.target_response}"
        fp = compute_simhash(combined_text)

        # 1. Fast exact duplicate check
        if fp in self._seen_exact:
            return GateResult(
                passed=False,
                score=0.0,
                message="Gate 6 FAIL: Exact duplicate detected (SimHash match).",
            )

        # 2. SimHash near-duplicate check
        for past_fp in self._seen_hashes:
            dist = hamming_distance(fp, past_fp)
            if dist <= self.max_hamming_distance:
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 6 FAIL: Near-duplicate detected (Hamming distance {dist} <= {self.max_hamming_distance}).",
                )

        # 3. Self-BLEU check against seen instructions
        curr_tokens = [t.lower() for t in re.findall(r"\b\w+\b", record.instruction)]
        if self._seen_tokens and curr_tokens:
            ref_sample = list(self._seen_tokens)[-20:]
            bleu_scores = [calculate_sentence_bleu(ref, curr_tokens) for ref in ref_sample]
            avg_bleu = sum(bleu_scores) / len(bleu_scores) if bleu_scores else 0.0
            if avg_bleu > self.max_self_bleu and len(self._seen_tokens) >= 5:
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 6 FAIL: Self-BLEU score {avg_bleu:.3f} exceeds threshold {self.max_self_bleu}.",
                )

        # Register record into bounded structures
        if len(self._seen_hashes) == self._seen_hashes.maxlen and self._seen_hashes:
            evicted = self._seen_hashes[0]
            self._seen_exact.discard(evicted)

        self._seen_hashes.append(fp)
        self._seen_exact.add(fp)
        self._seen_tokens.append(curr_tokens)

        return GateResult(
            passed=True,
            score=1.0,
            message="Gate 6 PASS: Record is distinct and non-redundant.",
        )
