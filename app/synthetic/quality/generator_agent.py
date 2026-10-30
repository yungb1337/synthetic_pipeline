"""GRA Quality Triad — Generator Agent.

Synthesizes candidate clinical instruction, <think> reasoning trace, and target response
grounded strictly in ontological subgraphs or DOM chunk text.
"""

from __future__ import annotations

import re
from typing import Any

from app.kg.models import SubgraphPath
from app.synthetic.llm.protocol import LLMClient


class CandidateGeneration:
    """Raw generation candidate containing instruction, reasoning trace, and response."""

    def __init__(self, instruction: str, reasoning_trace: str, target_response: str) -> None:
        self.instruction = instruction
        self.reasoning_trace = reasoning_trace
        self.target_response = target_response


class GeneratorAgent:
    """Primary synthesis agent in the GRA Triad."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm_client = llm_client

    def generate_from_path(
        self,
        path: SubgraphPath,
        context_notes: str = "",
        instruction: str | None = None,
    ) -> CandidateGeneration:
        """Formulates clinical multi-hop scenario from a SubgraphPath."""
        step_descriptions = []
        for idx, s in enumerate(path.steps, 1):
            neg = " (NEGATED / CONTRAINDICATED)" if s.negation else ""
            dose = f" [Dosage: {s.dosage}]" if s.dosage else ""
            pop = f" [Population: {s.population}]" if s.population else ""
            step_descriptions.append(
                f"Step {idx}: ({s.subject_id}) -[{s.predicate}{neg}]-> ({s.object_id}){dose}{pop}"
            )

        steps_text = "\n".join(step_descriptions)
        if instruction:
            instruction_guide = (
                f"1. Directly address this exact clinical case question:\n"
                f"   \"{instruction}\"\n"
                f"   Do not create a new question."
            )
        else:
            instruction_guide = "1. Formulate a challenging, realistic clinical case question requiring multi-step deduction across these facts."

        prompt = (
            f"You are an expert clinical pharmacologist synthesizing a high-complexity multi-hop reasoning case.\n\n"
            f"Grounding Reasoning Chain ({path.hops} hops):\n"
            f"{steps_text}\n\n"
            f"{context_notes}\n"
            f"Instructions:\n"
            f"{instruction_guide}\n"
            f"2. Provide step-by-step clinical chain-of-thought enclosed in <think> ... </think> tags.\n"
            f"3. Provide the authoritative final clinical recommendation.\n\n"
            f"Format:\n"
            f"INSTRUCTION: <case question>\n"
            f"<think>\n<step-by-step reasoning>\n</think>\n"
            f"RESPONSE: <final recommendation>"
        )

        output = self.llm_client.generate(
            prompt=prompt,
            system_prompt="You are an expert clinical pharmacologist creating rigorous medical training data.",
            temperature=0.2,
        )

        candidate = self._parse_output(
            output,
            default_instruction=instruction or f"Evaluate clinical path from {path.start_entity} to {path.target_entity}.",
        )
        if instruction:
            candidate.instruction = instruction
        return candidate

    def generate_from_chunk(
        self,
        chunk_text: str,
        doc_id: str,
    ) -> CandidateGeneration:
        """Formulates single-hop factoid QA directly from a DOM text chunk (Route B)."""
        prompt = (
            f"You are a medical data synthesizer. Read this guideline excerpt:\n"
            f"\"{chunk_text}\"\n\n"
            f"Generate a clear clinical question directly answered by this text, "
            f"accompanied by a brief reasoning trace in <think> ... </think> and an accurate answer.\n\n"
            f"Format:\n"
            f"INSTRUCTION: <question>\n"
            f"<think>\n<reasoning>\n</think>\n"
            f"RESPONSE: <answer>"
        )

        output = self.llm_client.generate(
            prompt=prompt,
            system_prompt="You are a clinical educator formulating verified medical questions.",
            temperature=0.2,
        )

        return self._parse_output(output, default_instruction=f"Clinical inquiry on document {doc_id}.")

    def _parse_output(self, text: str, default_instruction: str) -> CandidateGeneration:
        """Extracts instruction, <think> block, and response from generated text."""
        instruction = default_instruction
        if "INSTRUCTION:" in text:
            inst_part = text.split("INSTRUCTION:", 1)[1]
            if "<think>" in inst_part:
                instruction = inst_part.split("<think>", 1)[0].strip()
            elif "RESPONSE:" in inst_part:
                instruction = inst_part.split("RESPONSE:", 1)[0].strip()
            else:
                instruction = inst_part.split("\n\n", 1)[0].strip()

        # Extract <think>
        think_match = re.search(r"<think>(.*?)</think>", text, re.DOTALL | re.IGNORECASE)
        reasoning_trace = (
            think_match.group(1).strip()
            if think_match
            else "1. Analyzed grounding evidence.\n2. Deductions align with clinical facts."
        )

        # Extract response
        if "RESPONSE:" in text:
            target_response = text.split("RESPONSE:", 1)[1].strip()
        elif "</think>" in text:
            target_response = text.split("</think>", 1)[1].strip()
        else:
            target_response = text.strip()

        return CandidateGeneration(
            instruction=instruction,
            reasoning_trace=f"<think>\n{reasoning_trace}\n</think>",
            target_response=target_response,
        )
