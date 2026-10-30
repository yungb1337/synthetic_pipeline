"""Frontier API Client Adapter for OpenAI and Gemini Endpoints.

Handles sequential key rotation, 429 rate limit backoff with jitter,
and structured Pydantic response extraction without secret leakage.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Any, TypeVar
from pydantic import BaseModel

from app.synthetic.llm.protocol import structured_predict_helper

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)


class APIClient:
    """Client adapter for external LLM APIs (OpenAI and Google Gemini)."""

    def __init__(
        self,
        provider: str = "gemini",
        model_name: str | None = None,
        api_keys: list[str] | None = None,
        max_retries: int = 5,
    ) -> None:
        self.provider = provider.lower()
        self.custom_model_name = model_name
        self.max_retries = max_retries
        self._api_keys = self._discover_keys(api_keys)
        self._key_index = 0

    @property
    def model_name(self) -> str:
        if self.custom_model_name:
            return self.custom_model_name
        return "gemini-1.5-flash" if self.provider == "gemini" else "gpt-4o-mini"

    def _discover_keys(self, provided_keys: list[str] | None) -> list[str]:
        if provided_keys:
            return provided_keys

        discovered: list[str] = []
        prefix = "GEMINI_API_KEY" if self.provider == "gemini" else "OPENAI_API_KEY"

        # Check primary key
        if os.getenv(prefix):
            discovered.append(os.environ[prefix])

        # Check numbered keys (_1, _2, _3)
        for i in range(1, 10):
            k = os.getenv(f"{prefix}_{i}")
            if k:
                discovered.append(k)

        return discovered

    def _get_current_key(self) -> str | None:
        if not self._api_keys:
            return None
        return self._api_keys[self._key_index % len(self._api_keys)]

    def _rotate_key(self) -> None:
        if self._api_keys:
            self._key_index = (self._key_index + 1) % len(self._api_keys)
            logger.info("Rotated to API key index %d", self._key_index)

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        key = self._get_current_key()
        if not key:
            raise RuntimeError(f"No API keys found for provider '{self.provider}'.")

        for attempt in range(self.max_retries):
            try:
                if self.provider == "gemini":
                    return self._call_gemini(prompt, system_prompt, temperature, max_tokens, key)
                else:
                    return self._call_openai(prompt, system_prompt, temperature, max_tokens, key)
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "rate limit" in err_msg.lower() or "resource_exhausted" in err_msg.lower():
                    self._rotate_key()
                    key = self._get_current_key()
                    backoff = (2**attempt) + random.uniform(0.5, 1.5)
                    logger.warning("Hit 429 rate limit. Backing off for %.2fs...", backoff)
                    time.sleep(backoff)
                else:
                    if attempt == self.max_retries - 1:
                        raise
                    time.sleep(1.0)

        raise RuntimeError(f"Failed to generate after {self.max_retries} retries.")

    def _call_gemini(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        key: str,
    ) -> str:
        # Using urllib to keep network dependencies zero if google-genai not installed
        import urllib.request
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
        contents: list[dict[str, Any]] = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTIONS: {system_prompt}"}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"]

    def _call_openai(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
        key: str,
    ) -> str:
        import urllib.request
        url = "https://api.openai.com/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

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
