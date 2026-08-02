"""Download local open-source models into the repo `models/` folder.

Run once per machine:
    PYTHONPATH=. python scripts/download_models.py [model_id]

Default: BAAI/bge-m3 (1024-dim, multilingual) -> models/bge-m3, so the embedder
loads fully offline from inside the pipeline (not the shared HF cache).
Adheres to the "no data leaves the machine" + "models live in the repo" policy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def download(model_id: str = "BAAI/bge-m3", out_dir: str = "models") -> Path:
    from huggingface_hub import snapshot_download

    target = Path(out_dir) / model_id.rsplit("/", 1)[-1]  # models/<safename>
    target.mkdir(parents=True, exist_ok=True)
    print(f"downloading {model_id} -> {target} ...")
    snapshot_download(repo_id=model_id, local_dir=str(target))
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1024**2
    print(f"done: {target} ({size:.0f} MiB)")
    return target


if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-m3"
    download(model)