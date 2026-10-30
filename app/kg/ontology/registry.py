"""Thread-safe Registry for Domain Ontology Providers."""

from __future__ import annotations

import threading
from typing import ClassVar

from app.kg.ontology.protocol import OntologyProvider


class OntologyRegistry:
    """Thread-safe registry for domain ontology providers."""

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _providers: ClassVar[dict[str, OntologyProvider]] = {}

    @classmethod
    def register(cls, domain: str, provider: OntologyProvider) -> None:
        """Registers an ontology provider under a domain key."""
        with cls._lock:
            cls._providers[domain.lower()] = provider

    @classmethod
    def get(cls, domain: str) -> OntologyProvider:
        """Retrieves registered provider for the given domain."""
        with cls._lock:
            key = domain.lower()
            if key not in cls._providers:
                raise KeyError(
                    f"No ontology provider registered for domain '{domain}'. "
                    f"Available domains: {list(cls._providers.keys())}"
                )
            return cls._providers[key]

    @classmethod
    def list_domains(cls) -> list[str]:
        """Lists all registered domain names."""
        with cls._lock:
            return list(cls._providers.keys())

    @classmethod
    def clear(cls) -> None:
        """Clears all registered providers (primarily for test isolation)."""
        with cls._lock:
            cls._providers.clear()
