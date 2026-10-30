"""Registry for Pluggable Synthesis Recipes."""

from __future__ import annotations

import threading
from typing import ClassVar

from app.synthetic.recipes.protocol import SynthesisRecipe


class RecipeRegistry:
    """Thread-safe registry for synthesis recipes."""

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _recipes: ClassVar[dict[str, SynthesisRecipe]] = {}

    @classmethod
    def register(cls, recipe: SynthesisRecipe) -> None:
        """Registers a recipe instance."""
        with cls._lock:
            cls._recipes[recipe.name.lower()] = recipe

    @classmethod
    def get(cls, name: str) -> SynthesisRecipe:
        """Retrieves recipe by name."""
        with cls._lock:
            key = name.lower()
            if key not in cls._recipes:
                raise KeyError(
                    f"No recipe registered with name '{name}'. "
                    f"Available recipes: {list(cls._recipes.keys())}"
                )
            return cls._recipes[key]

    @classmethod
    def list_recipes(cls) -> list[str]:
        """Lists all registered recipe names."""
        with cls._lock:
            return list(cls._recipes.keys())

    @classmethod
    def clear(cls) -> None:
        """Clears all registered recipes (for test isolation)."""
        with cls._lock:
            cls._recipes.clear()
