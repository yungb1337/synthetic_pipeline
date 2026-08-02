"""A real, local, open-source embedding model (sentence-transformers / PyTorch).

This replaces the `DummyEmbedder` when PyTorch + CUDA are available: a model
(e.g. BAAI/bge-small-en-v1.5, or all-MiniLM-L6-v2) downloads once to the local
HF cache and embeds in batched calls on the RTX 3050 (GPU, `cuda`) with a CPU
fallback. Deterministic: same model + same inputs => same vectors.

The pipeline never depends on it being present — `factory.default_embedder`
falls back to `DummyEmbedder` if torch/this model isn't installed (CI, cramped
machines). See README §"embedding seam".
"""
from __future__ import annotations

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

    name = "sentence-transformers"

    def __init__(
        self,
        model: str = "BAAI/bge-m3",
        device: str = "auto",
        batch_size: int = 128,
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
            kwargs = {"device": self.device}
            if self.fp16:
                import torch  # type: ignore
                kwargs["torch_dtype"] = torch.float16  # fits 4GB VRAM
            self._st = SentenceTransformer(self._model_ref, **kwargs)
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