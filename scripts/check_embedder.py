"""Runtime check: does this machine have a usable embedder (GPU/CPU)?"""
from __future__ import annotations

import time


def main() -> int:
    from app.embedding import cuda_available, default_embedder, EmbeddingOptions

    print(f"torch.cuda.is_available()  : {cuda_available()}")
    embed = default_embedder(EmbeddingOptions(real_if_available=True))
    print(f"embedder                   : {embed.name}")
    print(f"device                     : {getattr(embed, 'device', 'n/a')}")
    print(f"dim                        : {getattr(embed, 'dim', 'n/a')}")

    sample = ["The patient has diabetes.", "Metformin is first-line.", "Aspirin for MI."]
    t0 = time.time()
    vecs = embed.embed(sample, batch_size=2)
    dt = (time.time() - t0) * 1000
    print(f"embedded {len(sample)} texts ({dt:.0f} ms)")
    print(f"vec dims   : {[len(v) for v in vecs]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())