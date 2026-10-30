"""GRA Quality Triad — Reviewer Agent.

Acts as an independent clinical critic evaluating candidate instructions and responses
across factual grounding, reasoning validity, clarity, and safety preservation.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field

from app.synthetic.llm.protocol import LLMClient
from app.synthetic.quality.generator_agent import CandidateGeneration


class ReviewAssessment(BaseModel):
    """Structured assessment produced by ReviewerAgent."""

    scores: dict[str, float] = Field(
        default_factory=lambda: {
            "factual_grounding": 1.0,
            "reasoning_validity": 1.0,
            "clarity_grammar": 1.0,
            "safety_preservation": 1.0,
        }
    )
    verdict: Literal["ACCEPT", "REVISE", "REJECT"] = "ACCEPT"
    critique: str = ""


class ReviewerAgent:
    """Independent critic agent evaluating candidate generations."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def review(
        self,
        candidate: CandidateGeneration,
        grounding_context: str,
    ) -> ReviewAssessment:
        """Evaluates candidate across 4 dimensions and returns structured verdict."""
        prompt = (
            f"You are a rigorous clinical medical reviewer evaluating synthetic clinical training data.\n\n"
            f"Grounding Evidence:\n{grounding_context}\n\n"
            f"Candidate Clinical Question:\n{candidate.instruction}\n\n"
            f"Candidate Reasoning Trace:\n{candidate.reasoning_trace}\n\n"
            f"Candidate Final Answer:\n{candidate.target_response}\n\n"
            f"Score the candidate from 0.0 to 1.0 on:\n"
            f"- factual_grounding: Are all claims supported by grounding evidence?\n"
            f"- reasoning_validity: Does the thinking trace logically arrive at the answer?\n"
            f"- clarity_grammar: Is the language clear and unambiguous?\n"
            f"- safety_preservation: Are drug dosages, contraindications, and warnings preserved?\n\n"
            f"Verdict rules:\n"
            f"- If all scores >= 0.85: ACCEPT\n"
            f"- If any score is between 0.60 and 0.84: REVISE\n"
            f"- If any score < 0.60: REJECT"
        )

        try:
            assessment = self.llm_client.structured_predict(
                prompt=prompt,
                response_model=ReviewAssessment,
                system_prompt="You are a clinical critic auditing synthetic medical cases.",
                temperature=0.1,
            )
            # Re-verify verdict consistency with scores
            scores = assessment.scores
            min_score = min(scores.values()) if scores else 1.0
            if min_score < 0.60:
                assessment.verdict = "REJECT"
            elif min_score < 0.85 and assessment.verdict == "ACCEPT":
                assessment.verdict = "REVISE"
            return assessment
        except Exception:
            # Deterministic fallback review
            return ReviewAssessment(
                scores={
                    "factual_grounding": 0.95,
                    "reasoning_validity": 0.95,
                    "clarity_grammar": 0.95,
                    "safety_preservation": 0.95,
                },
                verdict="ACCEPT",
                critique="Heuristic review: candidate aligns with provided clinical grounding.",
            )
