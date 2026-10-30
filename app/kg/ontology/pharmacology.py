"""Pharmacology Ontology Provider & Gazetteer (RxNorm + MeSH).

Integrates FlashText for O(N) exact alias lookup and RapidFuzz for fuzzy alias
matching, backed by curated pharmacology terminology.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flashtext import KeywordProcessor
from rapidfuzz import fuzz

from app.kg.models import Assertion, Entity
from app.kg.ontology.protocol import OntologyProvider
from app.kg.ontology.registry import OntologyRegistry
from app.kg.ontology_seeder import STARTER_PHARMACOLOGY_KB, seed_ontology_files

STANDARD_PREDICATES = [
    "CONTRAINDICATED_IN",
    "REQUIRES_DOSE_ADJUSTMENT",
    "FIRST_LINE_FOR",
    "INTERACTS_WITH",
    "MONITORING_REQUIRED",
    "ADVERSE_EFFECT_OF",
]


class PharmacologyOntologyProvider:
    """Ontology provider for Pharmacology (drugs, conditions, contraindications, dosages)."""

    def __init__(self, gazetteer_path: Path | str | None = None) -> None:
        self._domain = "pharmacology"
        self._entities_by_id: dict[str, Entity] = {}
        self._entities_list: list[Entity] = []
        self._keyword_processor = KeywordProcessor(case_sensitive=False)
        self._aliases_to_id: dict[str, str] = {}

        self._load_gazetteer(gazetteer_path)

    @property
    def domain(self) -> str:
        return self._domain

    def _load_gazetteer(self, path: Path | str | None) -> None:
        raw_items: list[dict[str, Any]]
        if path is not None:
            p = Path(path)
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    raw_items = json.load(f)
            else:
                raw_items = STARTER_PHARMACOLOGY_KB
        else:
            default_path = Path("ontologies") / "pharma_starter_gazetteer.json"
            if default_path.exists():
                with open(default_path, "r", encoding="utf-8") as f:
                    raw_items = json.load(f)
            else:
                seed_file = seed_ontology_files()
                with open(seed_file, "r", encoding="utf-8") as f:
                    raw_items = json.load(f)

        for item in raw_items:
            aliases = list(item.get("synonyms", item.get("aliases", [])))
            entity = Entity(
                id=item["id"],
                name=item["name"],
                category=item["category"],
                aliases=aliases,
                atc_code=item.get("atc_code"),
                mesh_id=item.get("mesh_id"),
                description=item.get("description"),
            )
            self._entities_by_id[entity.id] = entity
            self._entities_list.append(entity)

            # Map primary name and aliases to entity ID
            self._keyword_processor.add_keyword(entity.name, entity.id)
            self._aliases_to_id[entity.name.lower()] = entity.id

            for alias in aliases:
                self._keyword_processor.add_keyword(alias, entity.id)
                self._aliases_to_id[alias.lower()] = entity.id

    def lookup_entity(self, text: str, category_hint: str | None = None) -> Entity | None:
        """Looks up an entity mention using exact FlashText lookup, falling back to RapidFuzz."""
        query = text.strip()
        if not query:
            return None

        # 1. Exact match via direct alias dictionary or FlashText
        query_lower = query.lower()
        if query_lower in self._aliases_to_id:
            entity = self._entities_by_id[self._aliases_to_id[query_lower]]
            if category_hint is None or entity.category.lower() == category_hint.lower():
                return entity

        extracted_ids = self._keyword_processor.extract_keywords(query)
        if extracted_ids:
            # Pick first matching candidate
            for ent_id in extracted_ids:
                candidate = self._entities_by_id.get(ent_id)
                if candidate and (category_hint is None or candidate.category.lower() == category_hint.lower()):
                    return candidate

        # 2. RapidFuzz token_sort_ratio fallback (threshold >= 85)
        best_score = 0.0
        best_entity: Entity | None = None

        for alias_clean, ent_id in self._aliases_to_id.items():
            candidate = self._entities_by_id[ent_id]
            if category_hint and candidate.category.lower() != category_hint.lower():
                continue
            score = fuzz.token_sort_ratio(query_lower, alias_clean)
            if score > best_score:
                best_score = score
                best_entity = candidate

        if best_score >= 85.0 and best_entity is not None:
            return best_entity

        return None

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities_by_id.get(entity_id)

    def search_entities(self, query: str, limit: int = 10) -> list[Entity]:
        query_lower = query.strip().lower()
        if not query_lower:
            return self._entities_list[:limit]

        scored: list[tuple[float, Entity]] = []
        for ent in self._entities_list:
            score = fuzz.partial_ratio(query_lower, ent.name.lower())
            for alias in ent.aliases:
                alias_score = fuzz.partial_ratio(query_lower, alias.lower())
                if alias_score > score:
                    score = alias_score
            if score > 50:
                scored.append((score, ent))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ent for _, ent in scored[:limit]]

    def get_predicates(self) -> list[str]:
        return list(STANDARD_PREDICATES)

    def validate_assertion(self, assertion: Assertion) -> tuple[bool, str | None]:
        if assertion.predicate not in STANDARD_PREDICATES:
            return False, f"Unknown predicate '{assertion.predicate}' for pharmacology domain."

        if assertion.subject_id not in self._entities_by_id:
            return False, f"Subject entity '{assertion.subject_id}' not found in ontology."

        if assertion.object_id not in self._entities_by_id:
            return False, f"Object entity '{assertion.object_id}' not found in ontology."

        return True, None


# Auto-register default pharmacology provider
_default_pharma_provider = PharmacologyOntologyProvider()
OntologyRegistry.register("pharmacology", _default_pharma_provider)
