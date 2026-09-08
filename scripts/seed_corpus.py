#!/usr/bin/env python
"""seed_corpus.py — discover open-access medical/academic PDFs into a corpus manifest.

Queries Europe PMC (REST) and arXiv (Atom API) for OPEN-ACCESS, PDF-available
documents stratified by the run's known parser risk surfaces (multi-column
reviews, tables, guidelines, scans/mixed, edge). Append-only: never overwrites
existing manifest records. SHAs are filled later by download_curated_corpus.py.

Usage:
    .venv/Scripts/python.exe scripts/seed_corpus.py --out <sources/manifest.json> [--limit N] [--strata S1,S5] [--dry-run]

Writes manifest entries: {id, url, stratum, source, title, status:new}
Fully deterministic given API responses; safe to re-run (dedups by id).
Dependencies: requests (installed)."""
from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# Stable public PDFs that cannot be discovered via Europe PMC/arXiv but cover
# the scan/forms/mixed strata (S4/S5). All public, non-PHI, .gov/.int.
CURATED_SEED: list[dict] = [
    # CMS-1500 claim form (blank + sample) — S4 scans/forms
    {"id": "cms-1500-blank", "url": "https://www.cms.gov/Medicare/CMS-Forms/CMS-Forms/Downloads/CMS1500.pdf",
     "stratum": "S4", "source": "curated", "title": "CMS-1500 Health Insurance Claim Form (blank)", "status": "new"},
    {"id": "hcfa-1500-sample", "url": "https://www.cms.gov/Medicare/CMS-Forms/CMS-Forms/Downloads/CMS-1500-2014.pdf",
     "stratum": "S4", "source": "curated", "title": "CMS-1500 02/12 sample form", "status": "new"},
    # CDC MMWR (surveillance tables) — S2 table-dense
    {"id": "mmwr-weekly-morbidity-table", "url": "https://www.cdc.gov/mmwr/PDF/wk/mm7407.pdf",
     "stratum": "S2", "source": "curated", "title": "MMWR Surveillance Tables (sample week)", "status": "new"},
    # WHO guideline PDF (public) — S3
    {"id": "who-hiv-guideline", "url": "https://iris.who.int/bitstream/handle/10665/208825/9789241549709-eng.pdf",
     "stratum": "S3", "source": "curated", "title": "WHO Consolidated HIV Guidelines (public)", "status": "new"},
]

ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# Stratum -> Europe PMC query fragments (all OPEN_ACCESS + PDF-available)
STRATA_QUERIES: dict[str, list[str]] = {
    "S1": [  # multi-column academic reviews
        'JOURNAL:"BMJ Open" OPEN_ACCESS:y AND PUB_TYPE:"Review"',
        'JOURNAL:"PLoS ONE" OPEN_ACCESS:y AND PUB_TYPE:"Review" AND (TITLE:"cardiovascular" OR TITLE:"oncology")',
        'JOURNAL:"Scientific Reports" OPEN_ACCESS:y AND PUB_TYPE:"Review" AND TITLE:"medicine"',
        'JOURNAL:"Frontiers in Medicine" OPEN_ACCESS:y AND PUB_TYPE:"Review"',
        'JOURNAL:"BMC Medicine" OPEN_ACCESS:y AND PUB_TYPE:"Review"',
        'JOURNAL:"eLife" OPEN_ACCESS:y AND (PUB_TYPE:"Review" OR TITLE:"review") AND TITLE:"health"',
        'OPEN_ACCESS:y AND PUB_TYPE:"Systematic Review" AND (TITLE:"meta-analysis" OR TITLE:"epidemiology")',
    ],
    "S2": [  # table-dense / clinical-trial reports
        'OPEN_ACCESS:y AND PUB_TYPE:"Clinical Trial" AND (TITLE:"randomized controlled trial" OR TITLE:"double-blind" OR TITLE:"surveillance")',
        'OPEN_ACCESS:y AND PUB_TYPE:"Clinical Trial" AND (TITLE:"phase 3" OR TITLE:"efficacy" OR TITLE:"placebo")',
        'JOURNAL:"Trials" OPEN_ACCESS:y AND (TITLE:"randomised" OR TITLE:"protocol" OR TITLE:"trial")',
        'OPEN_ACCESS:y AND (TITLE:"cohort study" OR TITLE:"multicenter study" OR TITLE:"registry")',
        'OPEN_ACCESS:y AND PUB_TYPE:"Clinical Trial" AND (TITLE:"safety" OR TITLE:"tolerability" OR TITLE:"pharmacokinetics")',
    ],
    "S3": [  # clinical guidelines
        'OPEN_ACCESS:y AND PUB_TYPE:"Practice Guideline"',
        'OPEN_ACCESS:y AND (TITLE:"consensus statement" OR TITLE:"clinical guideline") AND hasPDF:y',
        'OPEN_ACCESS:y AND (TITLE:"management guideline" OR TITLE:"clinical practice recommendations")',
        'JOURNAL:"BMJ" OPEN_ACCESS:y AND (TITLE:"guideline" OR TITLE:"recommendations")',
        'OPEN_ACCESS:y AND (TITLE:"diagnostic criteria" OR TITLE:"screening recommendations" OR TITLE:"treatment guidelines")',
    ],
    "S4": [  # forms, scans, lab reports, pathology case studies
        'OPEN_ACCESS:y AND (PUB_TYPE:"Case Reports" OR TITLE:"case report") AND (TITLE:"pathology" OR TITLE:"clinical presentation")',
        'OPEN_ACCESS:y AND (TITLE:"laboratory findings" OR TITLE:"biomarker panel" OR TITLE:"histological")',
        'JOURNAL:"BMJ Case Reports" OPEN_ACCESS:y',
    ],
    "S5": [  # mixed / edge — image-heavy medical imaging
        'OPEN_ACCESS:y AND (JOURNAL:"Medical image analysis" OR TITLE:"CT imaging" OR TITLE:"MRI") AND PUB_TYPE:"Journal Article"',
        'OPEN_ACCESS:y AND (TITLE:"ultrasound" OR TITLE:"histopathology" OR TITLE:"PET scan" OR TITLE:"endoscopy")',
        'JOURNAL:"Radiology: Artificial Intelligence" OPEN_ACCESS:y',
        'JOURNAL:"Frontiers in Oncology" OPEN_ACCESS:y AND (TITLE:"imaging" OR TITLE:"segmentation" OR TITLE:"biomarker")',
    ],
}


def _norm_key(rec: dict) -> str:
    return f"{rec.get('source')}:{rec.get('id')}"


def search_europepmc(query: str, page_size: int = 100, retries: int = 3,
                     backoff: float = 4.0) -> list[dict]:
    """Query Europe PMC REST search; return result records (core)."""
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                params={"query": query, "format": "json", "resultType": "core",
                        "pageSize": page_size},
                timeout=45,
            )
            r.raise_for_status()
            return r.json().get("resultList", {}).get("result", [])
        except Exception as exc:  # noqa: BLE001 — retry on any network/API failure
            if attempt == retries - 1:
                print(f"  [europepmc] FAILED after {retries}: {exc}", file=sys.stderr)
                return []
            time.sleep(backoff * (attempt + 1))
    return []


def pmc_to_manifest(records: list[dict], stratum: str) -> list[dict]:
    """Convert Europe PMC records to manifest entries (PDF via ?pdf=render)."""
    out: list[dict] = []
    for rec in records:
        pmcid = rec.get("pmcid")
        if not pmcid or rec.get("isOpenAccess") != "Y":
            continue
        pdf_url = None
        for u in rec.get("fullTextUrlList", {}).get("fullTextUrl", []):
            if u.get("documentStyle") == "pdf" and u.get("availability") == "Open access":
                pdf_url = u.get("url")
                break
        if not pdf_url:  # fall back to the stable render endpoint
            pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render"
        out.append({
            "id": pmcid,
            "url": pdf_url,
            "stratum": stratum,
            "source": "europepmc",
            "title": (rec.get("title") or "")[:200],
            "pmid": rec.get("pmid"),
            "doi": rec.get("doi"),
            "pub_year": rec.get("pubYear"),
            "status": "new",
        })
    return out


def search_arxiv(max_results: int = 60) -> list[dict]:
    """Pull medical-imaging arXiv preprints (S5 mixed/edge)."""
    query = (
        "cat:eess.IV AND (cat:q-bio.QM OR cat:cs.LG OR cat:med) AND "
        "(TITLE:medical OR TITLE:clinical OR TITLE:radiology OR TITLE:histopathology)"
    )
    url = "http://export.arxiv.org/api/query"
    try:
        r = requests.get(url, params={"search_query": query, "start": 0, "max_results": max_results}, timeout=45)
        r.raise_for_status()
        root = ET.fromstring(r.text)
    except Exception as exc:  # noqa: BLE001
        print(f"  [arxiv] FAILED: {exc}", file=sys.stderr)
        return []
    out: list[dict] = []
    for entry in root.findall("a:entry", ARXIV_NS):
        aid = entry.findtext("a:id", default="", namespaces=ARXIV_NS)
        title = " ".join((entry.findtext("a:title", default="", namespaces=ARXIV_NS) or "").split())
        if "/abs/" in aid:
            arxid = aid.rsplit("/abs/", 1)[1]
            out.append({
                "id": f"arxiv-{arxid}",
                "url": f"https://arxiv.org/pdf/{arxid}",
                "stratum": "S5",
                "source": "arxiv",
                "title": title[:200],
                "arxiv_id": arxid,
                "status": "new",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="path to corpus manifest.json (append-only)")
    ap.add_argument("--limit", type=int, default=800, help="max new entries added this run")
    ap.add_argument("--strata", default="S1,S2,S3,S4,S5", help="comma-separated strata subset to seed")
    ap.add_argument("--no-arxiv", action="store_true", help="skip the arXiv query (S5)")
    ap.add_argument("--dry-run", action="store_true", help="print candidates without touching manifest")
    args = ap.parse_args()

    out = Path(args.out)
    existing: dict[str, dict] = {}
    if out.exists():
        for rec in json.loads(out.read_text(encoding="utf-8")):
            existing[_norm_key(rec)] = rec

    strata = {s.strip() for s in args.strata.split(",") if s.strip()}
    added: list[dict] = []
    strata_added: dict[str, int] = {s: 0 for s in strata}
    per_stratum_cap = max(20, (args.limit // max(1, len(strata))))

    # 1) Curated .gov/.int seeds (S4 forms/scans + few S2/S3)
    if "S4" in strata:
        for rec in CURATED_SEED:
            if _norm_key(rec) not in existing:
                added.append(rec)
                strata_added["S4"] = strata_added.get("S4", 0) + 1

    # 2) Europe PMC per-stratum queries
    for stratum in sorted(strata):
        if stratum not in STRATA_QUERIES:
            continue
        for q in STRATA_QUERIES[stratum]:
            if strata_added.get(stratum, 0) >= per_stratum_cap or len(added) >= args.limit:
                break
            recs = search_europepmc(q, page_size=100)
            for rec in pmc_to_manifest(recs, stratum):
                if strata_added.get(stratum, 0) >= per_stratum_cap or len(added) >= args.limit:
                    break
                if _norm_key(rec) not in existing:
                    added.append(rec)
                    strata_added[stratum] = strata_added.get(stratum, 0) + 1
            time.sleep(0.8)  # polite rate limit

    # 3) arXiv edge/mixed (S5)
    if "S5" in strata and not args.no_arxiv and len(added) < args.limit:
        for rec in search_arxiv(max_results=80):
            if len(added) >= args.limit:
                break
            if _norm_key(rec) not in existing:
                added.append(rec)
                strata_added["S5"] = strata_added.get("S5", 0) + 1

    if args.dry_run:
        for rec in added:
            print(f"  [{rec['stratum']}] {rec['id']:<18} {rec['url']}")
        print(f"DRY-RUN: {len(added)} would be added (existing {len(existing)})")
        return 0

    merged = list(existing.values()) + added
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: manifest {out} now has {len(merged)} entries (+{len(added)} new, "
          f"{sum(1 for r in merged if r.get('status') == 'ok')} already downloaded)")
    for rec in added:
        print(f"  +[{rec['stratum']}] {rec['id']:<18} {rec['url'][:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())