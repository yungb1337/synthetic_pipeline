"""Local GGUF Backend via llama-cpp-python for phi4-mini:3.8B.

Enforces strict GPU resource serialization via a class-level threading lock
and provides structured output prediction using Pydantic schemas.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, ClassVar, TypeVar
from pydantic import BaseModel

from app.synthetic.llm.protocol import structured_predict_helper

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class LlamaCppClient:
    """Local GGUF LLM client with GPU locking and structured output generation."""

    _GPU_LOCK: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        model_path: str | Path | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
        verbose: bool = False,
    ) -> None:
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.verbose = verbose
        self._model_path = self._resolve_model_path(model_path)
        self._llm: Any = None

    @property
    def model_name(self) -> str:
        return f"phi4-mini-gguf:{self._model_path.name if self._model_path else 'uninitialized'}"

    def _resolve_model_path(self, model_path: str | Path | None) -> Path | None:
        if model_path:
            p = Path(model_path)
            if p.exists():
                return p
        default_p = Path("models") / "phi4-mini" / "Phi-4-mini-instruct-Q4_K_M.gguf"
        if default_p.exists():
            return default_p
        return None

    def _ensure_loaded(self) -> Any:
        if self._llm is not None:
            return self._llm

        if not self._model_path or not self._model_path.exists():
            raise FileNotFoundError(
                f"Phi-4-mini GGUF model file not found at '{self._model_path}'. "
                f"Run 'python scripts/download_kg_models.py --model phi4-mini' to download."
            )

        try:
            from llama_cpp import Llama

            with self._GPU_LOCK:
                try:
                    self._llm = Llama(
                        model_path=str(self._model_path),
                        n_ctx=self.n_ctx,
                        n_gpu_layers=self.n_gpu_layers,
                        verbose=self.verbose,
                    )
                except Exception as e:
                    logger.warning("CUDA initialization failed (%s); falling back to CPU", e)
                    self._llm = Llama(
                        model_path=str(self._model_path),
                        n_ctx=self.n_ctx,
                        n_gpu_layers=0,
                        verbose=self.verbose,
                    )
            return self._llm
        except ImportError as e:
            raise RuntimeError("llama-cpp-python is not installed.") from e

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        llm = self._ensure_loaded()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        with self._GPU_LOCK:
            output = llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return output["choices"][0]["message"]["content"]

    def structured_predict(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> T:
        return structured_predict_helper(
            generate_fn=self.generate,
            prompt=prompt,
            response_model=response_model,
            system_prompt=system_prompt,
            temperature=temperature,
        )
