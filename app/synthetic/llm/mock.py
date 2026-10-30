"""Hermetic Mock LLM Client for Offline Testing and Verification."""

from __future__ import annotations

from typing import Any, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class MockLLMClient:
    """Mock LLM client enabling 100% offline, deterministic testing."""

    def __init__(
        self,
        model_name: str = "mock-phi4-mini",
        custom_responses: dict[str, str] | None = None,
        custom_structured: Any = None,
    ) -> None:
        self._model_name = model_name
        self._custom_responses = custom_responses or {}
        self._custom_structured = custom_structured
        self.call_history: list[dict[str, Any]] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        self.call_history.append(
            {
                "type": "generate",
                "prompt": prompt,
                "system_prompt": system_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        for key, resp in self._custom_responses.items():
            if key in prompt:
                return resp

        # Default clinical CoT response
        return (
            "<think>\n"
            "1. Analyzed patient clinical parameters and pharmacological guidelines.\n"
            "2. Identified contraindications and appropriate dosage ceilings.\n"
            "3. Formulated step-by-step clinical recommendation.\n"
            "</think>\n\n"
            "Based on clinical evidence, the recommended therapeutic adjustment is verified."
        )

    def structured_predict(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.1,
    ) -> T:
        self.call_history.append(
            {
                "type": "structured_predict",
                "prompt": prompt,
                "response_model": response_model.__name__,
                "system_prompt": system_prompt,
                "temperature": temperature,
            }
        )

        if self._custom_structured is not None:
            if isinstance(self._custom_structured, response_model):
                return self._custom_structured
            if isinstance(self._custom_structured, dict):
                return response_model.model_validate(self._custom_structured)

        # Inspect fields and construct synthetic valid defaults
        fields_data: dict[str, Any] = {}
        for field_name, field_info in response_model.model_fields.items():
            if field_info.default is not None and not repr(field_info.default).startswith(
                "PydanticUndefined"
            ):
                fields_data[field_name] = field_info.default
            elif field_info.default_factory is not None:
                fields_data[field_name] = field_info.default_factory()
            else:
                # Infer simple defaults from type annotation
                annotation = str(field_info.annotation)
                if "str" in annotation:
                    if "verdict" in field_name:
                        fields_data[field_name] = "ACCEPT"
                    else:
                        fields_data[field_name] = f"mock_{field_name}"
                elif "float" in annotation:
                    fields_data[field_name] = 1.0
                elif "int" in annotation:
                    fields_data[field_name] = 1
                elif "bool" in annotation:
                    fields_data[field_name] = True
                elif "list" in annotation:
                    fields_data[field_name] = []
                elif "dict" in annotation:
                    fields_data[field_name] = {}
                else:
                    fields_data[field_name] = None

        return response_model.model_validate(fields_data)
