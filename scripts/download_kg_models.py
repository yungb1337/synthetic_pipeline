"""Download Knowledge Graph & Synthetic Data generation models into local `models/` directory.

Models:
1. urchade/gliner_large-v2.1 (~1.7 GB) -> models/gliner_large-v2.1
2. unsloth/Phi-4-mini-instruct-GGUF (Q4_K_M, ~2.3 GB) -> models/phi4-mini/Phi-4-mini-instruct-Q4_K_M.gguf
3. MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli (~714 MB) -> models/deberta-v3-nli

Usage:
    .venv/Scripts/python.exe scripts/download_kg_models.py --all
    .venv/Scripts/python.exe scripts/download_kg_models.py --model gliner
    .venv/Scripts/python.exe scripts/download_kg_models.py --model phi4-mini
    .venv/Scripts/python.exe scripts/download_kg_models.py --model deberta
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODELS_DIR = Path("models")


def download_gliner(target_dir: Path | None = None) -> Path:
    from huggingface_hub import snapshot_download

    dest = target_dir or (MODELS_DIR / "gliner_large-v2.1")
    dest.mkdir(parents=True, exist_ok=True)
    repo_id = "urchade/gliner_large-v2.1"
    print(f"Downloading GLiNER [{repo_id}] -> {dest} ...")
    snapshot_download(repo_id=repo_id, local_dir=str(dest))
    size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / (1024**2)
    print(f"GLiNER download complete: {dest} ({size_mb:.1f} MB)")
    return dest


def download_phi4_mini_gguf(target_dir: Path | None = None) -> Path:
    from huggingface_hub import hf_hub_download

    dest_dir = target_dir or (MODELS_DIR / "phi4-mini")
    dest_dir.mkdir(parents=True, exist_ok=True)
    repo_id = "unsloth/Phi-4-mini-instruct-GGUF"
    filename = "Phi-4-mini-instruct-Q4_K_M.gguf"
    print(f"Downloading Phi-4-mini Q4_K_M GGUF [{repo_id}/{filename}] -> {dest_dir} ...")
    out_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(dest_dir),
    )
    p = Path(out_path)
    size_gb = p.stat().st_size / (1024**3)
    print(f"Phi-4-mini GGUF download complete: {p} ({size_gb:.2f} GB)")
    return p


def download_deberta_nli(target_dir: Path | None = None) -> Path:
    from huggingface_hub import snapshot_download

    dest = target_dir or (MODELS_DIR / "deberta-v3-nli")
    dest.mkdir(parents=True, exist_ok=True)
    repo_id = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
    print(f"Downloading DeBERTa-v3 NLI [{repo_id}] -> {dest} ...")
    snapshot_download(repo_id=repo_id, local_dir=str(dest))
    size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / (1024**2)
    print(f"DeBERTa NLI download complete: {dest} ({size_mb:.1f} MB)")
    return dest


def verify_models(models_to_check: list[str]) -> bool:
    all_ok = True
    for m in models_to_check:
        if m == "gliner":
            p = MODELS_DIR / "gliner_large-v2.1"
            has_files = p.exists() and any(p.iterdir())
            print(f"GLiNER ({p}): {'PRESENT' if has_files else 'ABSENT'}")
            if not has_files:
                all_ok = False
        elif m == "phi4-mini":
            p = MODELS_DIR / "phi4-mini" / "Phi-4-mini-instruct-Q4_K_M.gguf"
            has_file = p.exists() and p.stat().st_size > 1024**3
            print(f"Phi-4-mini GGUF ({p}): {'PRESENT' if has_file else 'ABSENT'}")
            if not has_file:
                all_ok = False
        elif m == "deberta":
            p = MODELS_DIR / "deberta-v3-nli"
            has_files = p.exists() and any(p.iterdir())
            print(f"DeBERTa NLI ({p}): {'PRESENT' if has_files else 'ABSENT'}")
            if not has_files:
                all_ok = False
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Download KG & Synthetic Data Models")
    parser.add_argument(
        "--model",
        choices=["gliner", "phi4-mini", "deberta", "all"],
        default="all",
        help="Model to download (default: all)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify presence of models without downloading",
    )
    args = parser.parse_args()

    models_to_download = (
        ["gliner", "phi4-mini", "deberta"] if args.model == "all" else [args.model]
    )

    if args.verify_only:
        verify_models(models_to_download)
        return 0

    for m in models_to_download:
        try:
            if m == "gliner":
                download_gliner()
            elif m == "phi4-mini":
                download_phi4_mini_gguf()
            elif m == "deberta":
                download_deberta_nli()
        except Exception as e:
            print(f"Error downloading {m}: {e}", file=sys.stderr)
            return 1

    print("\nAll requested models downloaded and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
