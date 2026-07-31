#!/usr/bin/env python3
"""Generate deterministic public-development semantic evaluation reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from semantic_normalizer import (  # noqa: E402
    canonical_json,
    evaluate_ablations,
    evaluate_phrase_gold,
    evaluate_rg_gate,
    load_registry,
)
from semantic_normalizer.evaluator import dataset_sha256  # noqa: E402

NORMALIZER = ROOT / "src/semantic_normalizer/normalizer.py"
EVALUATOR = ROOT / "src/semantic_normalizer/evaluator.py"
SIDECAR_SCHEMA = ROOT / "src/semantic_normalizer/data/sidecar.schema.json"


def _reject_heldout(path: Path) -> None:
    if "heldout" in str(path).casefold():
        raise ValueError(f"heldout path is prohibited in the dev evaluator: {path}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scrub_observational_latency(value: object) -> None:
    if isinstance(value, dict):
        value.pop("latency_ns_observed", None)
        for child in value.values():
            _scrub_observational_latency(child)
    elif isinstance(value, list):
        for child in value:
            _scrub_observational_latency(child)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--semantic-dataset",
        type=Path,
        default=ROOT / "tests/fixtures/semantic_gates.json",
    )
    parser.add_argument(
        "--retrieval-dataset",
        type=Path,
        default=ROOT / "tests/fixtures/dev_retrieval.json",
    )
    parser.add_argument(
        "--semantic-output",
        type=Path,
        default=ROOT / "reports/dev-semantic-gates.json",
    )
    parser.add_argument(
        "--retrieval-output",
        type=Path,
        default=ROOT / "reports/dev-retrieval-ablations-final.json",
    )
    args = parser.parse_args(argv)
    for path in (
        args.semantic_dataset,
        args.retrieval_dataset,
        args.semantic_output,
        args.retrieval_output,
    ):
        _reject_heldout(path)

    registry = load_registry()
    semantic_dataset = _read_json(args.semantic_dataset)
    if semantic_dataset.get("schema_version") != "semantic-gates-public-dev-v1":
        raise ValueError("unsupported semantic gate bundle schema")
    if semantic_dataset.get("dataset_role") != "public-development-only":
        raise ValueError("dev evaluator accepts only public-development-only data")
    phrase = evaluate_phrase_gold(semantic_dataset["phrase_gold"], registry)
    rg = evaluate_rg_gate(semantic_dataset["rg"], registry)
    reproducibility = {
        "semantic_dataset_sha256": _sha256(args.semantic_dataset),
        "registry_sha256": registry["hash"],
        "registry_schema_sha256": registry["schema_hash"],
        "normalizer_sha256": _sha256(NORMALIZER),
        "evaluator_sha256": _sha256(EVALUATOR),
        "sidecar_schema_sha256": _sha256(SIDECAR_SCHEMA),
    }
    semantic_report = {
        "schema_version": "semantic-gates-report-v1",
        "dataset_role": "public-development-only",
        "reproducibility": reproducibility,
        "phrase_gold": phrase,
        "rg": rg,
        "heldout_accessed": False,
        "limitations": [
            "Development results do not authorize a held-out claim.",
            "No statistical significance claim is made.",
        ],
    }
    _write_json(args.semantic_output, semantic_report)

    retrieval_dataset = _read_json(args.retrieval_dataset)
    if retrieval_dataset.get("dataset_role") != "public-development-only":
        raise ValueError("dev evaluator accepts only public-development-only retrieval data")
    retrieval_report = evaluate_ablations(retrieval_dataset, registry)
    _scrub_observational_latency(retrieval_report)
    retrieval_report["schema_version"] = "retrieval-ablations-final-v1"
    retrieval_report["dataset_sha256"] = dataset_sha256(retrieval_dataset)
    retrieval_report["reproducibility"] = {
        "dataset_file_sha256": _sha256(args.retrieval_dataset),
        "dataset_canonical_sha256": dataset_sha256(retrieval_dataset),
        "registry_sha256": registry["hash"],
        "registry_schema_sha256": registry["schema_hash"],
        "normalizer_sha256": _sha256(NORMALIZER),
        "evaluator_sha256": _sha256(EVALUATOR),
        "sidecar_schema_sha256": _sha256(SIDECAR_SCHEMA),
    }
    retrieval_report["heldout_accessed"] = False
    retrieval_report["claim_scope"] = "public_development_only_not_heldout"
    _write_json(args.retrieval_output, retrieval_report)
    print(canonical_json({
        "semantic_output_sha256": _sha256(args.semantic_output),
        "retrieval_output_sha256": _sha256(args.retrieval_output),
        "phrase_coverage": phrase["coverage"],
        "rg_status": rg["status"],
        "rg_literal_regressions": len(rg.get("exact_literal_regressions", [])),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
