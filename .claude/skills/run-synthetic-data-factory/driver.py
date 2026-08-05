#!/usr/bin/env python
"""Smoke driver for the Synthetic Data Factory (parser + normalizer + processing).

Runs the REAL app — the actual CLI modules — end-to-end on a freshly written
set of sample documents, asserts on their output, and exits non-zero on any
failure. This is the agent-facing way to confirm the pipeline works.

Run from the repo root with the venv python:

    .venv/Scripts/python.exe .claude/skills/run-synthetic-data-factory/driver.py [--embed] [--test]

    --embed   also load BGE-M3 and embed sample text (GPU; slow first run)
    --test    also run the full pytest suite (≈60 s)
    --keep    keep the _skill_work scratch dir (default: delete on success)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo root = 4 levels up from this file (run-synthetic-data-factory/skills/.claude/repo).
REPO = Path(__file__).resolve().parents[3]
WORK = Path(tempfile.mkdtemp(prefix="sdf_"))  # scratch workspace

SAMPLE_MD = """# Admission Note

Patient John Doe, 62, admitted with chest pain.

## History
Hypertension and Type 2 Diabetes, managed with Metformin.

- Complaint: retrosternal pain for 3 hours
- Allergies: Penicillin

## Plan
Start Aspirin 81 mg daily, order troponin now.
"""

SAMPLE_CSV = "test,result,unit,ref_range,date\nHemoglobin,13.2,g/dL,13.5-17.5,2026-07-20\nGlucose,178,mg/dL,70-99,2026-07-20\n"

SAMPLE_JSON = """{
  "patient": "Jane Smith",
  "mrn": "M-100023",
  "diagnoses": ["Acute bronchitis", "Asthma"],
  "summary": "Improved with bronchodilators."
}
"""


def run(argv: list[str], check_in: str | None = None) -> str:
    """Run `python -m <argv>` in the repo root; return stdout+stderr.

    check_in: if given, a substring that must appear in output (else fail).
    """
    cmd = [sys.executable, "-m", *argv]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    status = "PASS" if proc.returncode == 0 else "FAIL"
    print(f"[{status}] $ python -m {' '.join(argv)}  (rc={proc.returncode})")
    if proc.returncode != 0:
        print(out)
        raise SystemExit(1)
    if check_in and check_in not in out:
        print(f"FAIL: expected {check_in!r} in output:\n{out}")
        raise SystemExit(1)
    return out


def write_workspace() -> None:
    samples = WORK / "samples"
    corpus = WORK / "corpus"
    samples.mkdir(parents=True)
    corpus.mkdir(parents=True)
    (samples / "note.md").write_text(SAMPLE_MD, encoding="utf-8")
    (samples / "labs.csv").write_text(SAMPLE_CSV, encoding="utf-8")
    (samples / "discharge.json").write_text(SAMPLE_JSON, encoding="utf-8")
    for i in range(1, 6):
        (corpus / f"doc_{i}.md").write_text(
            f"# Progress Note {i}\n\nPatient {i} improving with stable vitals.\n\n- BP: 118/74\n", encoding="utf-8"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Synthetic Data Factory smoke driver")
    ap.add_argument("--embed", action="store_true", help="also run GPU embedder check (slow)")
    ap.add_argument("--test", action="store_true", help="also run the pytest suite (~60s)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch workspace")
    args = ap.parse_args()

    store = WORK / "store"
    print(f"== workspace: {WORK}")

    write_workspace()

    # 1. Parser — directory of three formats.
    run(["app.parser.cli", "--in", str(WORK / "samples"), "--out", str(store)],
        check_in="parsed 3/3 documents")
    # versioned layout: dom/<doc_id>/dom-v{version}.docJSON
    dom_files = sorted((store / "dom").rglob("dom-v*.docJSON"))
    if len(dom_files) != 3:
        print(f"FAIL: expected 3 DOM files in store, got {len(dom_files)}")
        return 1

    # 2. Normalizer — parse output -> normalized DOM.
    norm_out = WORK / "note.norm.json"
    run(["app.normalizer.cli", "--dom", str(dom_files[0]), "--out", str(norm_out)],
        check_in="normalized")
    if not norm_out.exists():
        print("FAIL: normalized DOM not written")
        return 1

    # 3. Batch processing — whole corpus parse+normalize in parallel.
    manifest = WORK / "manifest.json"
    batch = WORK / "batch_out"
    run(["app.processing.cli", "--in", str(WORK / "corpus"), "--out", str(batch),
         "--concurrency", "2", "--manifest", str(manifest)],
        check_in="parsed+norm : 5")

    # 4. Re-run: incremental manifest must skip everything.
    run(["app.processing.cli", "--in", str(WORK / "corpus"), "--out", str(batch),
         "--concurrency", "2", "--manifest", str(manifest)],
        check_in="skipped: 5")

    # 5. Library import-and-call smoke (no CLI).
    lib = subprocess.run(
        [sys.executable, "-c",
         "from app.parser.detection import detect; from app.normalizer.normalizer import Normalizer; "
         "print('imports ok'); print(detect(b'%PDF-1.7 x').slug)"],
        cwd=REPO, capture_output=True, text=True, timeout=60)
    assert "pdf" in lib.stdout, lib.stdout + lib.stderr
    print(f"[PASS] $ library import + detect  ({lib.stdout.strip()!r})")

    if args.embed:
        # scripts/ is not a package and the script needs `app` importable,
        # so run it directly with PYTHONPATH=repo (its own dir is sys.path[0]).
        env = {**os.environ, "PYTHONPATH": str(REPO)}
        proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_embedder.py")],
                              cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
        out = proc.stdout + proc.stderr
        ok = proc.returncode == 0 and "vec dims" in out
        print(f"[{'PASS' if ok else 'FAIL'}] $ python scripts/check_embedder.py  (rc={proc.returncode})")
        if not ok:
            print(out)
            return 1
        print("   " + "\n   ".join(l for l in out.splitlines() if "embedder" in l or "device" in l or "dim" in l))

    if args.test:
        proc = subprocess.run([sys.executable, "-m", "pytest"], cwd=REPO,
                              capture_output=True, text=True, timeout=600)
        tail = proc.stdout.splitlines()[-1] if proc.stdout else ""
        print(f"[{'PASS' if proc.returncode == 0 else 'FAIL'}] $ python -m pytest  ({tail})")
        if proc.returncode != 0:
            print(proc.stdout + proc.stderr)
            return 1

    print("\n== smoke driver OK — all pipeline stages green ==")
    if not args.keep:
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
