"""scripts/route_probe.py — inspect router decisions across the 100 PMC benchmark PDFs."""
import csv
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser.detection import detect
from app.routing import Router, RoutingConfig
from app.routing.detectors import get_detectors
from app.routing.inspectors import FastInspector
from app.routing.policy import RoutingPolicy
from app.routing.scoring import WeightedHeuristicScorer

def main():
    src_dir = Path("checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf")
    pdfs = sorted(src_dir.glob("*.pdf"))[:100]
    print(f"Inspecting {len(pdfs)} PDFs from {src_dir}...\n")

    cfg = RoutingConfig()
    inspector = FastInspector()
    detectors = get_detectors()
    scorer = WeightedHeuristicScorer(cfg)
    policy = RoutingPolicy(cfg)

    results = []
    band_counts = {"native": 0, "enrichment": 0, "docling": 0}

    for p in pdfs:
        data = p.read_bytes()
        det = detect(data, p.name)
        feat = inspector.inspect(data)
        if not feat:
            continue

        signals = []
        for d in detectors:
            signals.extend(d.evaluate(feat).signals)

        score = scorer.score(signals, feat)
        band = policy.route(score.complexity, score.confidence)
        band_counts[band] = band_counts.get(band, 0) + 1

        sig_map = {s.name: (float(s.value) if s.value is not None else 0.0) for s in signals if s.status == "ok"}

        results.append({
            "file": p.name,
            "pages": feat.page_count,
            "complexity": score.complexity,
            "confidence": score.confidence,
            "band": band,
            "font_div": sig_map.get("metric_font_diversity", 0.0),
            "reading_order": sig_map.get("metric_reading_order_ambiguity", 0.0),
            "layout_cplx": sig_map.get("metric_layout_complexity", 0.0),
            "multi_col": sig_map.get("metric_multi_column_probability", 0.0),
            "table_prob": sig_map.get("metric_table_probability", 0.0),
            "scanned_prob": sig_map.get("metric_scanned_page_probability", 0.0),
            "detected_tables": feat.detected_tables or 0,
        })

    print(f"{'Filename':<18} | {'Pgs':<4} | {'Score':<5} | {'Band':<10} | {'Font':<5} | {'RO':<5} | {'Layout':<6} | {'Multi':<5} | {'TblProb':<7} | {'PyMuTbl':<7}")
    print("-" * 95)
    for r in results[:30]:
        print(f"{r['file']:<18} | {r['pages']:<4} | {r['complexity']:<5} | {r['band']:<10} | {r['font_div']:<5.2f} | {r['reading_order']:<5.2f} | {r['layout_cplx']:<6.2f} | {r['multi_col']:<5.2f} | {r['table_prob']:<7.2f} | {r['detected_tables']:<7}")

    print("\n" + "=" * 50)
    print(f"Summary of 100 benchmark PDFs under CURRENT config:")
    print(f"  Native     : {band_counts.get('native', 0)}")
    print(f"  Enrichment : {band_counts.get('enrichment', 0)}")
    print(f"  Docling    : {band_counts.get('docling', 0)}")
    print("=" * 50)

if __name__ == "__main__":
    main()
