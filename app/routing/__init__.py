"""Intelligent Document Routing (ADR-011, docs/routing-spec.md).

A decision-only layer between ingestion and extraction: it cheaply inspects a
document, weighs detector evidence into a complexity score + confidence, and
picks the cheapest tier (Native / Enrichment / Docling) that reliably yields
the required fidelity — before any expensive parsing runs.

Bus-free and deterministic: routing is a pure function of `(bytes, detection,
RoutingConfig snapshot)`. No means is no route; no module outside this package
runs any routing logic, and this package never touches the loaders.
"""
from .config import RoutingConfig
from .router import Router, RoutingStats
from .schema import RoutingDecision, Signal

__all__ = [
    "RoutingConfig",
    "Router",
    "RoutingStats",
    "RoutingDecision",
    "Signal",
]