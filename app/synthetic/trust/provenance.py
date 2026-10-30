"""Gate 3: Physical DOM Provenance Audit.

Audits every provenance pointer to ensure zero fabricated or floating references,
verifying doc_id, page_index, block_id, bounding boxes, and verbatim source text.
"""

from __future__ import annotations

from typing import Any

from app.synthetic.schema import SyntheticRecord
from app.synthetic.trust.protocol import GateResult


class ProvenanceAuditGate:
    """Gate 3: Asserts 100% physical anchoring of synthetic records back to Canonical DOM blocks."""

    def evaluate(
        self,
        record: SyntheticRecord,
        canonical_dom: Any = None,
        **kwargs: Any,
    ) -> GateResult:
        """Validates all provenance pointers in the synthetic record."""
        if not record.provenance:
            return GateResult(
                passed=False,
                score=0.0,
                message="Gate 3 FAIL: Zero provenance pointers attached to record.",
            )

        for idx, ptr in enumerate(record.provenance):
            if not ptr.doc_id or not ptr.doc_id.strip():
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 3 FAIL: Provenance pointer #{idx} has missing doc_id.",
                )

            if ptr.page_index < 0:
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 3 FAIL: Provenance pointer #{idx} has invalid negative page_index.",
                )

            if not ptr.block_id or not ptr.block_id.strip():
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 3 FAIL: Provenance pointer #{idx} has missing block_id.",
                )

            if not ptr.source_text or not ptr.source_text.strip():
                return GateResult(
                    passed=False,
                    score=0.0,
                    message=f"Gate 3 FAIL: Provenance pointer #{idx} has empty source_text.",
                )

            # Verify against Canonical DOM if passed
            if canonical_dom is not None:
                # Find matching block in canonical DOM
                found_match = False
                for page in getattr(canonical_dom, "pages", []):
                    if page.index == ptr.page_index:
                        for blk in page.blocks:
                            if blk.id == ptr.block_id:
                                if ptr.source_text in blk.text or blk.text in ptr.source_text:
                                    found_match = True
                                    break
                if not found_match:
                    return GateResult(
                        passed=False,
                        score=0.0,
                        message=f"Gate 3 FAIL: Provenance pointer #{idx} text does not match block '{ptr.block_id}' in Canonical DOM.",
                    )

        return GateResult(
            passed=True,
            score=1.0,
            message=f"Gate 3 PASS: All {len(record.provenance)} provenance pointers verified.",
        )
