"""Model-Agnostic LLM Seam Protocol.

Decouples generation logic, extraction, and evaluation from specific model backends.
"""

from __future__ import annotations

import json
from typing import Callable, Protocol, TypeVar, runtime_checkable
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def format_structured_prompt(prompt: str, response_model: type[T]) -> str:
    """Formats prompt with JSON schema instruction for structured prediction."""
    schema = response_model.model_json_schema()
    return (
        f"{prompt}\n\n"
        f"You MUST respond ONLY with valid JSON conforming to this schema:\n"
        f"```json\n{json.dumps(schema, indent=2)}\n```\n"
        f"Output JSON only, enclosed in ```json ``` code block."
    )


def parse_structured_output(response_text: str, response_model: type[T]) -> T:
    """Parses JSON response text into Pydantic model with code block stripping and decode error handling."""
    cleaned = response_text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse LLM structured output as valid JSON: {exc}. Raw snippet: {cleaned[:200]!r}"
        ) from exc

    return response_model.model_validate(data)


def structured_predict_helper(
    generate_fn: Callable[..., str],
    prompt: str,
    response_model: type[T],
    system_prompt: str | None = None,
    temperature: float = 0.1,
) -> T:
    """Deduplicated helper coordinating JSON schema formatting, LLM call, and parsing."""
    json_prompt = format_structured_prompt(prompt, response_model)
    response_text = generate_fn(
        prompt=json_prompt,
        system_prompt=system_prompt,
        temperature=temperature,
    )
    return parse_structured_output(response_text, response_model)


@runtime_checkable
class LLMClient(Protocol):
    """Protocol defining the core contract for LLM generation and structured prediction."""

    @property
    def model_name(self) -> str:
        """Identifier of the model backend (e.g. 'phi4-mini:3.8b-q4_k_m', 'gemini-1.5-flash')."""
        ...

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        """Generates raw text response for the given prompt."""
        ...

    def structured_predict(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> T:
        """Generates Pydantic-validated structured output enforcing the response_model schema."""
        ...
