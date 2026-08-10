"""Wave F: routing provenance persistence (additive, old-DOM-safe, §9)."""
from __future__ import annotations

from app.parser.config import default_config
from app.parser.dom.models import Provenance
from app.parser.events import EventPublisher
from app.parser.extraction import Extractor
from app.parser.storage import FilesystemStore

from .routing_fixtures import text_pdf


def _extractor(tmp_path):
    store = FilesystemStore(str(tmp_path / "store"))
    pub = EventPublisher(sink=lambda name, payload: None)
    return Extractor(default_config(), store, events=pub), store


def test_provenance_routing_field_defaults_none():
    p = Provenance(parser_version="p", dom_schema_version="d")
    assert p.routing is None              # old DOMs without routing stay valid


def test_auto_pdf_records_full_routing_decision(tmp_path):
    ex, _ = _extractor(tmp_path)
    data = text_pdf(pages=1)
    out = ex.extract(data, "doc.pdf")
    assert out.ok
    prov = out.document.provenance
    assert prov is not None
    r = prov.routing
    assert r is not None
    assert r.route in ("native", "enrichment", "docling")
    assert r.router_version == "router-v0.1.0"
    assert r.policy_version == "policy-v0.1.0"
    assert r.scoring_version == "scoring-v0.1.0"
    assert r.detector_versions                       # per-detector versions (§10)
    assert r.bands                                   # band audit co-ordinates
    assert out.report.get("route") == r.route
    # serializes for provenance audit
    prov.model_dump_json()


def test_old_dom_without_routing_roundtrips():
    from app.parser.dom.models import Document, Metadata

    doc = Document(version="dom-schema-v0.1.0", document_id="d", source_hash="h",
                   metadata=Metadata(mime="application/pdf", detected_type="pdf"))
    payload = doc.model_dump_json()
    back = Document.model_validate_json(payload)
    assert back.provenance is None
    assert back.metadata.detected_type == "pdf"