"""Stage 1 Zero-Shot NER Mentions with GLiNER.

Extracts candidate spans using GLiNER (CPU execution, ~40ms/page) and automatically
links mentions to canonical ontology identifiers via the OntologyRegistry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.kg.models import EntityMention
from app.kg.ontology.registry import OntologyRegistry

DEFAULT_LABELS = ["Drug", "Condition", "Dosage", "Population", "LabTest", "AdverseEffect"]


class GLiNEREntityExtractor:
    """Stage 1 NER Extractor backed by GLiNER zero-shot model with hermetic fallback."""

    def __init__(
        self,
        model_name_or_path: str | Path | None = None,
        labels: list[str] | None = None,
        threshold: float = 0.50,
        device: str = "cpu",
        ontology_domain: str = "pharmacology",
    ) -> None:
        self.labels = labels or list(DEFAULT_LABELS)
        self.threshold = threshold
        self.device = device
        self.ontology_domain = ontology_domain
        self._model: Any = None
        self._load_model(model_name_or_path)

    def _load_model(self, model_path: str | Path | None) -> None:
        target_path: Path | None = None
        if model_path:
            p = Path(model_path)
            if p.exists():
                target_path = p
        else:
            default_p = Path("models") / "gliner_large-v2.1"
            if default_p.exists():
                target_path = default_p

        try:
            from gliner import GLiNER

            if target_path and target_path.exists():
                self._model = GLiNER.from_pretrained(str(target_path)).to(self.device)
            else:
                # Do not download automatically in hermetic environments
                self._model = None
        except Exception:
            self._model = None

    def extract_entities(
        self, text: str, block_context: dict[str, Any] | None = None
    ) -> list[EntityMention]:
        """Extracts candidate spans from text and links them to canonical ontology IDs."""
        if not text or not text.strip():
            return []

        mentions: list[EntityMention] = []

        if self._model is not None:
            try:
                raw_entities = self._model.predict_entities(
                    text, self.labels, threshold=self.threshold
                )
                for ent in raw_entities:
                    mention = EntityMention(
                        text=ent["text"],
                        category=ent["label"],
                        confidence=float(ent.get("score", 1.0)),
                        start_char=ent.get("start"),
                        end_char=ent.get("end"),
                    )
                    mentions.append(mention)
            except Exception:
                mentions = self._heuristic_extract(text)
        else:
            mentions = self._heuristic_extract(text)

        # Automatic Entity Linking
        try:
            onto = OntologyRegistry.get(self.ontology_domain)
            for mention in mentions:
                linked = onto.lookup_entity(mention.text, category_hint=mention.category)
                if not linked:
                    # Try without category hint
                    linked = onto.lookup_entity(mention.text)
                if linked:
                    mention.canonical_id = linked.id
        except KeyError:
            pass

        return mentions

    def _heuristic_extract(self, text: str) -> list[EntityMention]:
        """Lightweight deterministic fallback for hermetic offline execution."""
        mentions: list[EntityMention] = []
        try:
            onto = OntologyRegistry.get(self.ontology_domain)
            # Find known drug/condition terms in text
            for ent in onto.search_entities("", limit=100):
                # Search name
                pattern = re.compile(rf"\b{re.escape(ent.name)}\b", re.IGNORECASE)
                for m in pattern.finditer(text):
                    mentions.append(
                        EntityMention(
                            text=m.group(0),
                            category=ent.category,
                            confidence=0.99,
                            start_char=m.start(),
                            end_char=m.end(),
                            canonical_id=ent.id,
                        )
                    )
                # Search aliases
                for alias in ent.aliases:
                    if len(alias) >= 3:
                        pat = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
                        for m in pat.finditer(text):
                            mentions.append(
                                EntityMention(
                                    text=m.group(0),
                                    category=ent.category,
                                    confidence=0.95,
                                    start_char=m.start(),
                                    end_char=m.end(),
                                    canonical_id=ent.id,
                                )
                            )
        except Exception:
            pass

        # Also extract dosage patterns (e.g., '25 mg daily', '< 30 mg')
        dosage_matches = re.finditer(r"\b\d+(?:\.\d+)?\s*(?:mg|g|mcg|mL)(?:/[a-z]+)?(?:\s+(?:daily|once daily|bid|tid))?\b", text, re.IGNORECASE)
        for dm in dosage_matches:
            mentions.append(
                EntityMention(
                    text=dm.group(0),
                    category="Dosage",
                    confidence=0.85,
                    start_char=dm.start(),
                    end_char=dm.end(),
                )
            )

        # De-duplicate overlapping mentions preferring longer spans
        mentions.sort(key=lambda m: (m.start_char or 0, -(len(m.text))))
        filtered: list[EntityMention] = []
        last_end = -1
        for m in mentions:
            start = m.start_char if m.start_char is not None else 0
            end = m.end_char if m.end_char is not None else start + len(m.text)
            if start >= last_end:
                filtered.append(m)
                last_end = end

        return filtered
