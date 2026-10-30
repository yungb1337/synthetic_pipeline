"""Trust & Verification Engine Package (6 Hard Gates)."""

from app.synthetic.trust.consistency import GraphConsistencyGate
from app.synthetic.trust.dedup import DeDuplicationGate
from app.synthetic.trust.engine import TrustVerificationEngine
from app.synthetic.trust.entailment import EvidenceEntailmentGate
from app.synthetic.trust.faithfulness import FaithfulnessGate
from app.synthetic.trust.privacy import PrivacyScrubGate
from app.synthetic.trust.protocol import GateResult, TrustGate
from app.synthetic.trust.provenance import ProvenanceAuditGate

__all__ = [
    "EvidenceEntailmentGate",
    "GraphConsistencyGate",
    "ProvenanceAuditGate",
    "PrivacyScrubGate",
    "FaithfulnessGate",
    "DeDuplicationGate",
    "TrustVerificationEngine",
    "GateResult",
    "TrustGate",
]
