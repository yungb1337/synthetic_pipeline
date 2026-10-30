"""Command Line Interface for Synthetic Data Generation Engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.kg.store.store import DuckDBGraphStore
from app.synthetic.exporter.protocol import ExporterRegistry
from app.synthetic.llm.llama_cpp import LlamaCppClient
from app.synthetic.llm.mock import MockLLMClient
from app.synthetic.llm.protocol import LLMClient
from app.synthetic.quality.triad import GRAQualityTriad
from app.synthetic.recipes.registry import RecipeRegistry
from app.synthetic.trust.engine import TrustVerificationEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.synthetic.cli",
        description="MedFactory AI Synthetic Data Generation Engine",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate", help="Generate synthetic dataset")
    gen_parser.add_argument(
        "--db-path",
        type=str,
        default=":memory:",
        help="Path to DuckDB database file (default: :memory:)",
    )
    gen_parser.add_argument(
        "--doc-id",
        type=str,
        default="sample-doc",
        help="Target document ID",
    )
    gen_parser.add_argument(
        "--recipe",
        type=str,
        choices=["multi_hop", "dpo_perturbation", "table_reasoning", "dom_direct", "contradiction"],
        default="multi_hop",
        help="Synthesis recipe to execute (default: multi_hop)",
    )
    gen_parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Target number of records to generate (default: 5)",
    )
    gen_parser.add_argument(
        "--format",
        type=str,
        choices=["chatml", "alpaca", "dpo", "parquet"],
        default="chatml",
        help="Export format (default: chatml)",
    )
    gen_parser.add_argument(
        "--out",
        type=str,
        default="output/synthetic_records.jsonl",
        help="Output destination path (default: output/synthetic_records.jsonl)",
    )
    gen_parser.add_argument(
        "--mock",
        action="store_true",
        help="Use hermetic MockLLMClient instead of neural model weights",
    )

    return parser


def run_generate(args: argparse.Namespace) -> int:
    print(f"Starting synthetic generation [recipe={args.recipe}, format={args.format}, count={args.count}]...")

    # 1. Initialize LLM Backend
    llm: LLMClient
    if args.mock:
        llm = MockLLMClient()
    else:
        try:
            llm = LlamaCppClient()
        except Exception:
            print("Note: Local GGUF weights not loaded; using MockLLMClient fallback.")
            llm = MockLLMClient()

    # 2. Initialize Store and Quality Triad
    store = DuckDBGraphStore(db_path=args.db_path)
    triad = GRAQualityTriad(llm_client=llm)

    # 3. Lookup and execute recipe
    recipe_name = (
        "dpo_perturbation"
        if args.recipe in ("dpo", "dpo_perturbation")
        else ("dom_direct" if args.recipe in ("direct", "dom_direct") else args.recipe)
    )
    recipe = RecipeRegistry.get(recipe_name)
    raw_records = recipe.generate(store=store, triad=triad, count=args.count)

    print(f"Synthesized {len(raw_records)} candidate records from recipe '{recipe_name}'.")

    # 4. Filter with Trust Verification Engine (6 Hard Gates)
    trust_engine = TrustVerificationEngine()
    certified_records = trust_engine.filter_batch(raw_records)

    print(f"Certified {len(certified_records)}/{len(raw_records)} records passing all 6 Trust Gates.")

    # 5. Export
    exporter = ExporterRegistry.get(args.format)
    out_path = Path(args.out)
    final_path = exporter.export(certified_records, out_path)

    print(f"Dataset exported cleanly to: {final_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate":
        return run_generate(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
