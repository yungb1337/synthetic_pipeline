"""Synthetic Data Generation Engine Module (#6).

Dual-route adaptive synthesis with GRA Quality Triad, 6-Gate Trust & Verification Engine,
and multi-format training dataset exporters.
"""

from app.synthetic.exporter.protocol import ExportFormat, ExporterRegistry
from app.synthetic.llm.api_client import APIClient
from app.synthetic.llm.llama_cpp import LlamaCppClient
from app.synthetic.llm.mock import MockLLMClient
from app.synthetic.llm.protocol import LLMClient
from app.synthetic.quality.adjudicator_agent import AdjudicatorAgent
from app.synthetic.quality.generator_agent import GeneratorAgent
from app.synthetic.quality.reviewer_agent import ReviewerAgent
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.recipes.protocol import SynthesisRecipe
from app.synthetic.recipes.registry import RecipeRegistry
from app.synthetic.router import DualRouteRouter, GenerationRoute
from app.synthetic.sampler import SubgraphTopologySampler
from app.synthetic.schema import (
    GroundingSubgraph,
    ProvenancePointer,
    QualityMetadata,
    SyntheticRecord,
)
from app.synthetic.trust.engine import TrustVerificationEngine

__all__ = [
    "SyntheticRecord",
    "GroundingSubgraph",
    "ProvenancePointer",
    "QualityMetadata",
    "GenerationRoute",
    "DualRouteRouter",
    "SubgraphTopologySampler",
    "LLMClient",
    "MockLLMClient",
    "LlamaCppClient",
    "APIClient",
    "GeneratorAgent",
    "ReviewerAgent",
    "AdjudicatorAgent",
    "GRAQualityTriad",
    "SynthesisRecipe",
    "RecipeRegistry",
    "TrustVerificationEngine",
    "ExportFormat",
    "ExporterRegistry",
]
