"""Gate 4: PHI / PII Scrubbing and Anonymization with Microsoft Presidio.

Detects HIPAA Protected Health Information (PHI) including direct identifiers
(SSNs, phone numbers, email addresses, MRNs, dates of birth) and broad person/physician
names, enforcing zero unmasked leaks with Presidio analyzer/anonymizer and regex fallback.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.synthetic.schema import SyntheticRecord
from app.synthetic.trust.protocol import GateResult

logger = logging.getLogger(__name__)

HIPAA_PATTERNS = [
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
    ("PHONE_NUMBER", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("MEDICAL_RECORD_NUMBER", re.compile(r"\b(?:MRN|Medical Record Number|Patient ID)[:\s#]*[A-Z0-9-]+\b", re.I)),
    ("DATE_OF_BIRTH", re.compile(r"\b(?:DOB|Date of Birth)[:\s]*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.I)),
    ("PHYSICIAN_NAME", re.compile(r"\b(?:Dr\.|Doctor|Prof\.|Professor|Nurse|Attending)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")),
    ("PERSON", re.compile(r"\b(?:Patient|Subject|Mr\.|Mrs\.|Ms\.)\s+(?!(?:has|presents|is|with|who|underwent|receives|started)\b)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")),
]

_PRESIDIO_INITIALIZED = False
_analyzer_engine: Any = None
_anonymizer_engine: Any = None


def _get_presidio_engines() -> tuple[Any, Any]:
    """Initializes and returns Presidio Analyzer and Anonymizer engines with safety guards."""
    global _PRESIDIO_INITIALIZED, _analyzer_engine, _anonymizer_engine
    if _PRESIDIO_INITIALIZED:
        return _analyzer_engine, _anonymizer_engine

    try:
        import spacy
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import SpacyNlpEngine
        from presidio_anonymizer import AnonymizerEngine

        # Check for pre-installed spacy model, else fallback to blank model to avoid blocking download
        spacy_model = None
        for m in ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"):
            if spacy.util.is_package(m):
                spacy_model = m
                break

        if spacy_model:
            nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": spacy_model}])
        else:
            nlp = spacy.blank("en")
            nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en"}])
            nlp_engine.nlp = {"en": nlp}

        _analyzer_engine = AnalyzerEngine(nlp_engine=nlp_engine)
        _anonymizer_engine = AnonymizerEngine()
    except Exception as exc:
        logger.warning("Presidio initialization failed, using regex fallback: %s", exc)
        _analyzer_engine = None
        _anonymizer_engine = None

    _PRESIDIO_INITIALIZED = True
    return _analyzer_engine, _anonymizer_engine


class PrivacyScrubGate:
    """Gate 4: Scans synthetic records for unmasked PHI/PII entities."""

    def __init__(self, scrub_in_place: bool = False) -> None:
        self.scrub_in_place = scrub_in_place
        self.analyzer, self.anonymizer = _get_presidio_engines()

    def scan_text(self, text: str) -> list[tuple[str, str, int, int]]:
        """Scans text for HIPAA PHI entities using Presidio and regex patterns.

        Returns list of (entity_type, matched_text, start, end).
        """
        findings: list[tuple[str, str, int, int]] = []
        spans_covered: list[tuple[int, int]] = []

        # 1. Regex scanning (always active for guaranteed deterministic offline pass)
        for ent_type, pat in HIPAA_PATTERNS:
            for match in pat.finditer(text):
                findings.append((ent_type, match.group(0), match.start(), match.end()))
                spans_covered.append((match.start(), match.end()))

        # 2. Presidio Analyzer scanning if available
        if self.analyzer is not None:
            try:
                presidio_results = self.analyzer.analyze(
                    text=text,
                    language="en",
                    entities=["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "DATE_TIME", "LOCATION", "MEDICAL_LICENSE"],
                )
                for res in presidio_results:
                    matched_str = text[res.start : res.end].strip()
                    # Filter clinical false-positives for DATE_TIME: dosage amounts and administration frequencies
                    if res.entity_type == "DATE_TIME" and (
                        re.search(r"\b\d+\s*(?:mg|g|mcg|ml|kg)\b", matched_str, re.I)
                        or matched_str.lower() in {
                            "daily", "once daily", "bid", "tid", "qid", "weekly", "monthly", "hourly"
                        }
                    ):
                        continue

                    # Avoid duplicate span reporting
                    if not any(s <= res.start and res.end <= e for s, e in spans_covered):
                        findings.append((res.entity_type, matched_str, res.start, res.end))
                        spans_covered.append((res.start, res.end))
            except Exception as exc:
                logger.debug("Presidio analyze error: %s", exc)

        return findings

    def anonymize_text(self, text: str) -> str:
        """Replaces detected PHI entities with typed redaction tokens (e.g. <PHONE_NUMBER>)."""
        anonymized = text

        # 1. Presidio anonymizer if available
        if self.analyzer is not None and self.anonymizer is not None:
            try:
                results = self.analyzer.analyze(
                    text=anonymized,
                    language="en",
                    entities=["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "DATE_TIME", "LOCATION", "MEDICAL_LICENSE"],
                )
                filtered_results = []
                for res in results:
                    matched_str = anonymized[res.start : res.end].strip()
                    if res.entity_type == "DATE_TIME" and (
                        re.search(r"\b\d+\s*(?:mg|g|mcg|ml|kg)\b", matched_str, re.I)
                        or matched_str.lower() in {
                            "daily", "once daily", "bid", "tid", "qid", "weekly", "monthly", "hourly"
                        }
                    ):
                        continue
                    filtered_results.append(res)

                if filtered_results:
                    anon_result = self.anonymizer.anonymize(text=anonymized, analyzer_results=filtered_results)
                    anonymized = anon_result.text
            except Exception as exc:
                logger.debug("Presidio anonymize error: %s", exc)

        # 2. Regex redaction for patterns missed or if presidio unavailable
        for ent_type, pat in HIPAA_PATTERNS:
            anonymized = pat.sub(f"<{ent_type}>", anonymized)

        return anonymized

    def evaluate(self, record: SyntheticRecord, **kwargs: Any) -> GateResult:
        """Evaluates record against Gate 4 (zero unmasked PHI).

        Returns typed GateResult.
        """
        texts_to_check = [
            ("instruction", record.instruction),
            ("reasoning_trace", record.reasoning_trace),
            ("target_response", record.target_response),
        ]
        if record.negative_response:
            texts_to_check.append(("negative_response", record.negative_response))

        detected_entities: list[str] = []

        for field_name, val in texts_to_check:
            findings = self.scan_text(val)
            for ent_type, matched_text, _, _ in findings:
                detected_entities.append(f"{field_name}:{ent_type}")

        if detected_entities:
            if self.scrub_in_place:
                record.instruction = self.anonymize_text(record.instruction)
                record.reasoning_trace = self.anonymize_text(record.reasoning_trace)
                record.target_response = self.anonymize_text(record.target_response)
                if record.negative_response:
                    record.negative_response = self.anonymize_text(record.negative_response)
                record.quality_metadata.phi_free = True
                return GateResult(
                    passed=True,
                    score=1.0,
                    message=f"Gate 4 PASS (anonymized): Scrubbed PHI entities: {detected_entities}",
                    metadata={"entities": detected_entities},
                )
            else:
                record.quality_metadata.phi_free = False
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 4 FAIL: Detected unmasked PHI entities: {detected_entities}",
                    metadata={"entities": detected_entities},
                )

        record.quality_metadata.phi_free = True
        return GateResult(
            passed=True,
            score=1.0,
            message="Gate 4 PASS: Zero PHI/PII entities detected.",
            metadata={"entities": []},
        )
