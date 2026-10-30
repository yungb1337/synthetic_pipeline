"""DuckDB Graph Store for MedFactory AI Knowledge Graph.

Backwards compatibility redirect to app.kg.store package.
"""

from app.kg.store.serializer import GraphSerializer
from app.kg.store.store import DuckDBGraphStore

__all__ = ["DuckDBGraphStore", "GraphSerializer"]
