"""Document-Level Clinical Coreference Resolution.

Resolves anaphoric clinical expressions ("this drug", "the agent", "it", "these patients")
to the most recently mentioned canonical entity across adjacent blocks in reading order.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from app.kg.models import Entity, EntityMention
from app.kg.ontology.registry import OntologyRegistry
from app.parser.dom.models import Block

PRONOUN_PATTERNS = [
    (re.compile(r"\b(this drug|the drug|this agent|the agent|the medication|this medication|it)\b", re.I), "Drug"),
    (re.compile(r"\b(these patients|this condition|this disease|the condition|the disease)\b", re.I), "Condition"),
]


class SimpleCoreferenceResolver:
    """Sliding-window coreference resolver for DOM blocks in reading order."""

    def __init__(
        self,
        window_size: int = 3,
        ontology_domain: str = "pharmacology",
    ) -> None:
        self.window_size = window_size
        self.ontology_domain = ontology_domain
        self._recent_drugs: deque[Entity] = deque(maxlen=window_size)
        self._recent_conditions: deque[Entity] = deque(maxlen=window_size)

    def reset(self) -> None:
        """Resets sliding window state."""
        self._recent_drugs.clear()
        self._recent_conditions.clear()

    def process_block(
        self,
        block: Block,
        mentions: list[EntityMention],
    ) -> list[EntityMention]:
        """Updates internal entity memory and resolves anaphoric pronouns in the block."""
        # Reset memory upon encountering section headings
        if block.kind.lower() in ("heading", "title", "header"):
            self.reset()
            return mentions

        onto = OntologyRegistry.get(self.ontology_domain)

        # 1. Register canonical entities found in current mentions
        for m in mentions:
            if m.canonical_id:
                ent = onto.get_entity(m.canonical_id)
                if ent:
                    if ent.category.lower() == "drug":
                        self._recent_drugs.append(ent)
                    elif ent.category.lower() == "condition":
                        self._recent_conditions.append(ent)

        # 2. Check block text for unresolved pronouns
        resolved_mentions = list(mentions)
        existing_spans = {(m.start_char, m.end_char) for m in mentions if m.start_char is not None}

        for regex, category in PRONOUN_PATTERNS:
            target_queue = self._recent_drugs if category == "Drug" else self._recent_conditions
            if not target_queue:
                continue

            last_entity = target_queue[-1]

            for match in regex.finditer(block.text):
                span = (match.start(), match.end())
                if span in existing_spans:
                    continue

                # Create resolved mention pointing to the antecedent entity
                resolved_mentions.append(
                    EntityMention(
                        text=match.group(0),
                        category=category,
                        confidence=0.88,
                        start_char=match.start(),
                        end_char=match.end(),
                        canonical_id=last_entity.id,
                    )
                )
                existing_spans.add(span)

        return resolved_mentions
