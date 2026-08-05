"""A real, local, open-source embedding model (sentence-transformers / PyTorch).

This replaces the `DummyEmbedder` when PyTorch + CUDA are available: BGE-M3
(1024-dim, multilingual) loads from the local `models/bge-m3` copy and embeds
in batched calls on the RTX 3050 (GPU, `cuda`, fp16) with a CPU fallback.
Deterministic: same model + same inputs => same vectors (bit-exact on CPU,
cosine-stable on GPU-fp16 — ADR-010).

The pipeline never depends on it being present — `factory.default_embedder`
falls back to `DummyEmbedder` if torch/this model isn't installed (CI, cramped
machines). See README §"embedding seam".
"""
from __future__ import annotations

import hashlib
import os


def _local_ref(model: str, model_dir: str) -> str:
    """Prefer a local copy under <model_dir>/<safename> (downloaded by
    scripts/download_models.py); fall back to the HF identifier."""
    safename = model.rsplit("/", 1)[-1]
    candidate = os.path.join(model_dir, safename)
    return candidate if os.path.isdir(candidate) else model


def cuda_available() -> bool:
    """True only if torch is present AND reports a usable CUDA device."""
    try:
        import torch  # type: ignore
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def resolve_device(preferred: str = "auto") -> str:
    if preferred == "auto":
        return "cuda" if cuda_available() else "cpu"
    return preferred


class SentenceTransformerEmbedder:
    """Local embedding model — batched (list-in -> list[list[float]]), GPU-aware."""

    @property
    def name(self) -> str:
        """Model identity + revision + dtype (ADR-009).

        `emb-` storage keys must be unambiguous across models/dtypes, so the
        generic "sentence-transformers" label is gone. Example:
        ``BAAI/bge-m3@3f9a1c2b-fp16``.
        """
        dtype = "fp16" if self.fp16 else "fp32"
        return f"{self.model_name}@{self.revision}-{dtype}"

    @property
    def revision(self) -> str:
        """Deterministic, on-prem revision of the local model copy.

        sha256(config.json bytes)[:8] when the local model dir is present;
        "local" otherwise. Never reads remote state.
        """
        cfg = os.path.join(self._model_ref, "config.json")
        if os.path.isfile(cfg):
            try:
                with open(cfg, "rb") as fh:
                    return hashlib.sha256(fh.read()).hexdigest()[:8]
            except OSError:
                return "local"
        return "local"

    def __init__(
        self,
        model: str = "BAAI/bge-m3",
        device: str = "auto",
        batch_size: int = 32,
        model_dir: str = "models",
        fp16: bool = True,
    ):
        self.model_name = model
        self.batch_size = batch_size
        self.model_dir = model_dir
        self.fp16 = fp16 and resolve_device(device).startswith("cuda")
        self.device = resolve_device(device)
        self._st = None
        self._load_error: str = ""
        self.dim: int | None = None
        self._model_ref = _local_ref(model, model_dir)
        self._load()

    def _load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._st = SentenceTransformer(self._model_ref, device=self.device)
            if self.fp16:                      # cast to half to fit 4GB VRAM
                try:
                    self._st = self._st.half()
                except Exception:
                    pass
            try:  # modern + legacy API
                self.dim = int(self._st.get_embedding_dimension())
            except AttributeError:
                self.dim = int(self._st.get_sentence_embedding_dimension())
        except Exception as e:  # model download missing, torch absent, VRAM, etc.
            self._st = None
            self._load_error = str(e)

    @property
    def ok(self) -> bool:
        return self._st is not None

    def embed(self, texts: list[str], batch_size: int | None = None) -> list[list[float]]:
        if not self._st:
            raise RuntimeError(f"embedder unavailable: {self._load_error}")
        bs = batch_size or self.batch_size
        out: list[list[float]] = []
        for i in range(0, len(texts), bs):
            slice_texts = texts[i:i + bs]
            if not slice_texts:
                continue
            vecs = self._st.encode(
                slice_texts,
                batch_size=bs,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            out.extend(v.tolist() for v in vecs)
        return out