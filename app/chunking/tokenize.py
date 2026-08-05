"""Deterministic token counting for chunk budgets.

`TokenCounter` is resolved ONCE per pipeline run — all chunking and batching
decisions share the same instance, so the budget the batching enforces and the
``token_count`` recorded on each chunk always agree.

Two modes, both deterministic, both recorded in ``ChunkProvenance``:
  * ``bge-m3`` (primary) — the pinned BGE tokenizer (``tokenizers`` lib, local
    ``models/bge-m3/tokenizer.json``). BPE is deterministic: same string ->
    same ids. ``tokenizer_ref_hash = sha256(tokenizer.json bytes)``.
  * ``char4`` (fallback)  — ``max(1, len(text) // 4)``. Dependency-free, used
    when the tokenizer file is absent or fails to load (hermetic CI/tests).
"""
from __future__ import annotations

import hashlib
from pathlib import Path


class TokenCounter:
    """Count tokens deterministically; records which mode actually ran."""

    def __init__(
        self,
        mode: str = "bge-m3",
        tokenizer_path: str = "models/bge-m3/tokenizer.json",
        allow_char4_fallback: bool = True,
    ):
        self._mode_requested = mode
        self._tokenizer_path = tokenizer_path
        self._allow_char4_fallback = allow_char4_fallback
        # resolved lazily on first use; `None` until then
        self._mode: str | None = None
        self._tokenizer = None
        self._ref_hash: str | None = None

    def _ensure(self) -> None:
        """Resolve the actual counting mode once (lazy)."""
        if self._mode is not None:
            return
        if self._mode_requested == "bge-m3":
            if self._load_bge():
                return
            if not self._allow_char4_fallback:
                raise RuntimeError(
                    f"bge-m3 tokenizer unavailable at {self._tokenizer_path!r} and char4 fallback disabled"
                )
        self._mode = "char4"
        self._tokenizer = None
        self._ref_hash = None

    def _load_bge(self) -> bool:
        path = Path(self._tokenizer_path)
        if not path.is_file():
            return False
        try:
            from tokenizers import Tokenizer  # sentence-transformers dependency (Fact)

            data = path.read_bytes()
            self._ref_hash = hashlib.sha256(data).hexdigest()
            self._tokenizer = Tokenizer.from_file(str(path))
            self._mode = "bge-m3"
            return True
        except Exception:
            self._tokenizer = None
            self._ref_hash = None
            return False

    @property
    def tokenizer(self) -> str:
        """The ACTUAL mode in effect: ``"bge-m3"`` or ``"char4"``."""
        self._ensure()
        assert self._mode is not None
        return self._mode

    @property
    def tokenizer_ref_hash(self) -> str | None:
        """sha256 of the tokenizer file (``bge-m3`` only; ``None`` for ``char4``)."""
        self._ensure()
        return self._ref_hash

    def count(self, text: str) -> int:
        """Deterministic token count for ``text``."""
        self._ensure()
        if self._mode == "bge-m3":
            return len(self._tokenizer.encode(text).ids)
        return max(1, len(text) // 4)
