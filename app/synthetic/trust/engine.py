"""Trust & Verification Master Engine (6 Hard Gates).

Coordinates the six hard verification gates certifying synthetic records:
1. Evidence Entailment (DeBERTa-v3 NLI)
2. Graph Consistency (Cycle prevention & population homogeneity)
3. Physical DOM Provenance Audit
4. PHI / PII Scrubbing (HIPAA compliance)
5. Ragas Faithfulness (Verbatim groundedness)
6. De-duplication (SimHash & Self-BLEU)
"""

from __future__ import annotations

import logging
from typing import Any

from app.synthetic.schema import SyntheticRecord
from app.synthetic.trust.consistency import GraphConsistencyGate
from app.synthetic.trust.dedup import DeDuplicationGate
from app.synthetic.trust.entailment import EvidenceEntailmentGate
from app.synthetic.trust.faithfulness import FaithfulnessGate
from app.synthetic.trust.privacy import PrivacyScrubGate
from app.synthetic.trust.protocol import TrustGate
from app.synthetic.trust.provenance import ProvenanceAuditGate

logger = logging.getLogger(__name__)


class TrustVerificationEngine:
    """Master gatekeeper orchestrating all 6 hard verification gates."""

    def __init__(
        self,
        gate1_entailment: TrustGate | None = None,
        gate2_consistency: TrustGate | None = None,
        gate3_provenance: TrustGate | None = None,
        gate4_privacy: TrustGate | None = None,
        gate5_faithfulness: TrustGate | None = None,
        gate6_dedup: TrustGate | None = None,
    ) -> None:
        self.gate1 = gate1_entailment or EvidenceEntailmentGate()
        self.gate2 = gate2_consistency or GraphConsistencyGate()
        self.gate3 = gate3_provenance or ProvenanceAuditGate()
        self.gate4 = gate4_privacy or PrivacyScrubGate(scrub_in_place=False)
        self.gate5 = gate5_faithfulness or FaithfulnessGate()
        self.gate6 = gate6_dedup or DeDuplicationGate()

    def verify_record(
        self,
        record: SyntheticRecord,
        canonical_dom: Any = None,
    ) -> tuple[bool, list[str]]:
        """Validates record across all 6 gates uniformly, updating QualityMetadata."""
        diagnostics: list[str] = []

        # Gate 1: Evidence Entailment
        res1 = self.gate1.evaluate(record)
        record.quality_metadata.evidence_entailment_score = res1.score or 0.0
        diagnostics.append(res1.message)
        if not res1.passed:
            return False, diagnostics

        # Gate 2: Graph Consistency
        res2 = self.gate2.evaluate(record)
        record.quality_metadata.symbolic_execution_valid = res2.passed
        diagnostics.append(res2.message)
        if not res2.passed:
            return False, diagnostics

        # Gate 3: Physical DOM Provenance Audit
        res3 = self.gate3.evaluate(record, canonical_dom=canonical_dom)
        record.quality_metadata.provenance_audit_passed = res3.passed
        diagnostics.append(res3.message)
        if not res3.passed:
            return False, diagnostics

        # Gate 4: PHI / PII Scrubbing
        res4 = self.gate4.evaluate(record)
        record.quality_metadata.phi_free = res4.passed
        diagnostics.append(res4.message)
        if not res4.passed:
            return False, diagnostics

        # Gate 5: Ragas Faithfulness
        res5 = self.gate5.evaluate(record)
        record.quality_metadata.ragas_faithfulness_score = res5.score or 0.0
        diagnostics.append(res5.message)
        if not res5.passed:
            return False, diagnostics

        # Gate 6: De-duplication
        res6 = self.gate6.evaluate(record)
        record.quality_metadata.dedup_passed = res6.passed
        diagnostics.append(res6.message)
        if not res6.passed:
            return False, diagnostics

        return True, diagnostics

    def filter_batch(
        self,
        records: list[SyntheticRecord],
        canonical_dom: Any = None,
    ) -> list[SyntheticRecord]:
        """Filters a batch of records, retaining only certified candidates."""
        certified: list[SyntheticRecord] = []
        for r in records:
            passed, diag = self.verify_record(r, canonical_dom=canonical_dom)
            if passed:
                certified.append(r)
            else:
                logger.info("Record %s rejected: %s", r.record_id, diag[-1])
        return certified
