"""GRA Quality Triad — Adjudicator Agent.

Arbitrates disagreements and revises candidates when the Reviewer requests revision,
eliminating single-judge blind spots and ensuring bounded compute.
"""

from __future__ import annotations

import re
from typing import Literal
from pydantic import BaseModel

from app.synthetic.llm.protocol import LLMClient
from app.synthetic.quality.generator_agent import CandidateGeneration
from app.synthetic.quality.reviewer_agent import ReviewAssessment


class AdjudicationResult:
    """Result of adjudication arbitration."""

    def __init__(
        self,
        verdict: Literal["ACCEPT", "REJECT"],
        candidate: CandidateGeneration,
        notes: str = "",
    ) -> None:
        self.verdict = verdict
        self.candidate = candidate
        self.notes = notes


class AdjudicatorAgent:
    """Final arbiter agent resolving review revisions and disputes."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def adjudicate(
        self,
        candidate: CandidateGeneration,
        assessment: ReviewAssessment,
        grounding_context: str,
    ) -> AdjudicationResult:
        """Adjudicates a REVISE or disputed candidate, producing refined candidate or REJECT."""
        if assessment.verdict == "REJECT":
            return AdjudicationResult(
                verdict="REJECT",
                candidate=candidate,
                notes=f"Permanent reject upheld: {assessment.critique}",
            )

        # Attempt refinement based on critique
        prompt = (
            f"You are the Chief Clinical Adjudicator for synthetic clinical data.\n\n"
            f"Grounding Evidence:\n{grounding_context}\n\n"
            f"Original Question:\n{candidate.instruction}\n\n"
            f"Original Reasoning Trace:\n{candidate.reasoning_trace}\n\n"
            f"Original Answer:\n{candidate.target_response}\n\n"
            f"Reviewer Critique & Deficiencies:\n{assessment.critique}\n"
            f"Scores: {assessment.scores}\n\n"
            f"Task: Directly rewrite the reasoning trace and final answer to address all deficiencies "
            f"and guarantee 100% adherence to grounding evidence.\n\n"
            f"Format:\n"
            f"<think>\n<refined reasoning>\n</think>\n"
            f"RESPONSE: <refined final recommendation>"
        )

        try:
            output = self.llm_client.generate(
                prompt=prompt,
                system_prompt="You are an authoritative clinical arbiter producing verified medical content.",
                temperature=0.1,
            )

            think_match = re.search(r"<think>(.*?)</think>", output, re.DOTALL | re.IGNORECASE)
            refined_trace = (
                f"<think>\n{think_match.group(1).strip()}\n</think>"
                if think_match
                else candidate.reasoning_trace
            )

            if "RESPONSE:" in output:
                refined_response = output.split("RESPONSE:", 1)[1].strip()
            elif "</think>" in output:
                refined_response = output.split("</think>", 1)[1].strip()
            else:
                refined_response = output.strip()

            refined_candidate = CandidateGeneration(
                instruction=candidate.instruction,
                reasoning_trace=refined_trace,
                target_response=refined_response or candidate.target_response,
            )

            return AdjudicationResult(
                verdict="ACCEPT",
                candidate=refined_candidate,
                notes="Refined candidate addressing reviewer critique.",
            )
        except Exception as e:
            return AdjudicationResult(
                verdict="REJECT",
                candidate=candidate,
                notes=f"Adjudication refinement failed: {e}",
            )
