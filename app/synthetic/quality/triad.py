"""GRA Quality Triad Orchestrator.

Coordinates the Generator -> Reviewer -> Adjudicator peer-review loop,
guaranteeing bounded compute (max 2 refinement rounds) and eliminating single-judge bias.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.synthetic.llm.protocol import LLMClient
from app.synthetic.quality.adjudicator_agent import AdjudicatorAgent
from app.synthetic.quality.generator_agent import CandidateGeneration, GeneratorAgent
from app.synthetic.quality.reviewer_agent import ReviewAssessment, ReviewerAgent

logger = logging.getLogger(__name__)


class GRAQualityTriad:
    """Orchestrates peer-review loop across Generator, Reviewer, and Adjudicator agents."""

    def __init__(
        self,
        llm_client: LLMClient,
        generator: GeneratorAgent | None = None,
        reviewer: ReviewerAgent | None = None,
        adjudicator: AdjudicatorAgent | None = None,
        max_refinements: int = 2,
    ) -> None:
        self.generator = generator or GeneratorAgent(llm_client)
        self.reviewer = reviewer or ReviewerAgent(llm_client)
        self.adjudicator = adjudicator or AdjudicatorAgent(llm_client)
        self.max_refinements = max_refinements

    def evaluate_and_refine(
        self,
        candidate: CandidateGeneration,
        grounding_context: str,
    ) -> tuple[CandidateGeneration | None, ReviewAssessment]:
        """Runs the candidate through review and adjudication loop."""
        current_candidate = candidate
        last_assessment = self.reviewer.review(current_candidate, grounding_context)

        if last_assessment.verdict == "ACCEPT":
            return current_candidate, last_assessment

        if last_assessment.verdict == "REJECT":
            return None, last_assessment

        # Enter adjudication refinement loop (bounded by max_refinements)
        for round_idx in range(self.max_refinements):
            logger.info("Adjudicating candidate revision (round %d)...", round_idx + 1)
            adj_res = self.adjudicator.adjudicate(
                candidate=current_candidate,
                assessment=last_assessment,
                grounding_context=grounding_context,
            )

            if adj_res.verdict == "REJECT":
                return None, last_assessment

            current_candidate = adj_res.candidate
            # Re-review refined candidate
            last_assessment = self.reviewer.review(current_candidate, grounding_context)
            if last_assessment.verdict == "ACCEPT":
                return current_candidate, last_assessment

        # If loop exhausts without ACCEPT, return None
        return None, last_assessment
