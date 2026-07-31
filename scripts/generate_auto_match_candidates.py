#!/usr/bin/env python3
"""Generate the frozen public O3 auto-match candidate and blind-review sets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from semantic_normalizer import (  # noqa: E402
    canonical_json,
    evaluate_auto_matches,
    load_registry,
    normalize_text,
)

NORMALIZER = ROOT / "src/semantic_normalizer/normalizer.py"
BASELINE_SNAPSHOT = {
    "schema_version": "auto-match-candidates-o3-baseline-v1",
    "candidate_set_sha256": "f30678d1092de567ecc9b174f4ae85e747638ac427977e727edec9729d1a5069",
    "seed_sha256": "973d21cdb70eb50dc1cae8e392ccecc1aefef894fbc707b9de100684e69c7e13",
    "blind_sha256": "4f9cdeb4de0fa7a7a9627108c92850d6669d15df7eb1da90423b4cfbd6163bb0",
    "pending_sha256": "494ec474efb206eb4d41d744757bab7558f20a05a597e2f0d78ab74a8bd88680",
    "counts": {
        "candidates": 145,
        "derivable_seed": 73,
        "blind_adjudicated": 72,
        "TP": 139,
        "FP": 6,
        "FN": 0,
    },
}
FINAL_SNAPSHOT = {
    "schema_version": "auto-match-candidates-o3-corrected-v1",
    "candidate_set_sha256": "55d4f5ddfe1836a8a0697036a8a63498211172fb2ba6886e1e7b787815353fef",
    "counts": {
        "candidates": 137,
        "derivable_seed": 71,
        "blind_pending": 66,
        "golden_auto": 93,
        "dev_document_auto": 34,
        "dev_query_auto": 10,
        "ambiguous_events": 16,
        "protected_values": 36,
    },
}
CORRECTED_CLASSES = {
    "html_code_pre": "protect_complete_block",
    "condition.if:pt-BR:se": "review",
    "action.index:en:index": "review",
    "action.create:en:create": "review",
}


def _reject_heldout(path: Path) -> None:
    if "heldout" in str(path).casefold():
        raise ValueError(f"heldout path is prohibited: {path}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _occurrence_id(candidate: dict) -> str:
    # Prediction is deliberately absent: changing a model/registry decision must
    # not change the identity of the source occurrence.
    identity = {
        key: candidate[key]
        for key in (
            "source_group", "source_id", "input_sha256", "segment_index",
            "start", "end", "surface",
        )
    }
    return "occ-" + hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _events(
    text: str, source_group: str, source_id: str, kind: str, registry: dict
) -> tuple[list[dict], int, list[dict]]:
    records = normalize_text(text, source_id, kind, registry)
    input_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    normalizer_sha256 = _sha256(NORMALIZER)
    candidates = []
    for segment_index, record in enumerate(records):
        for event in record["match_events"]:
            candidate = {
                "source_group": source_group,
                "source_id": source_id,
                "input_sha256": input_sha256,
                "normalizer_sha256": normalizer_sha256,
                "registry_sha256": registry["hash"],
                "segment_index": segment_index,
                "start": event["start"],
                "end": event["end"],
                "surface": text[event["start"]:event["end"]],
                "context": record["original"],
                "language": event["alias_language"],
                "predicted_concept_id": event["concept_id"],
                "basis": event["basis"],
            }
            candidate["occurrence_id"] = _occurrence_id(candidate)
            candidates.append(candidate)
    ambiguous = sum(len(record["ambiguous_candidates"]) for record in records)
    protected_checks = []
    for record in records:
        for protected in (
            item for item in record["semantic_sequence"] if item["type"] == "protected"
        ):
            shift = sum(
                mapping["canonical_end"] - mapping["canonical_start"]
                - (mapping["original_end"] - mapping["original_start"])
                for mapping in record["canonical_mappings"]
                if mapping["original_end"] <= protected["local_start"]
            )
            canonical_start = protected["local_start"] + shift
            protected_checks.append({
                "source_id": source_id,
                "start": protected["start"],
                "end": protected["end"],
                "value": protected["text"],
                "source_value": text[protected["start"]:protected["end"]],
                "sequence_value": protected["text"],
                "canonical_value": record["canonical_text"][
                    canonical_start:canonical_start + len(protected["text"])
                ],
            })
    return candidates, ambiguous, protected_checks


def generate(
    golden_path: Path,
    dev_path: Path,
    registry: dict,
    *,
    case_range: tuple[int, int] = (1, 40),
    snapshot: dict | None = FINAL_SNAPSHOT,
) -> tuple[list, list, list, dict]:
    """`case_range` bounds the `gNN` ids read from the golden file.

    The bound used to be the literal `1 <= number <= 40` below, which silently dropped `g41`
    and made `g100` collide with `g10` (the slice reads two characters). A domain batch larger
    than 40 sampled occurrences could not be scored at all: `automatic` stayed 0 and
    `evaluate_auto_matches` returned the string `"not_run"` instead of a precision. The
    default preserves the historical behaviour exactly.

    `snapshot` pins the run against a previously adjudicated result and raises on any drift.
    That is right for the 2.0.0 baseline and wrong for a new corpus, where drift is the point;
    pass `snapshot=None` to generate against fresh material. The default keeps the pin.
    """
    low, high = case_range
    _reject_heldout(golden_path)
    _reject_heldout(dev_path)
    golden_cases = [
        json.loads(line)
        for line in golden_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates, seed, pending, protected_checks = [], [], [], []
    counters = Counter()
    for case in golden_cases:
        if case.get("type") != "normalize" or not case["id"].startswith("g"):
            continue
        number = int(case["id"][1:])
        if not low <= number <= high:
            continue
        case_candidates, ambiguous, case_protected = _events(
            case["input"], "golden", case["id"], case.get("kind", "text"), registry
        )
        protected_checks.extend(case_protected)
        counters.update(
            golden_auto=len(case_candidates), ambiguous=ambiguous,
            protected=len(case_protected),
        )
        expected = case.get("expected", {})
        explicit = set(expected.get("concept_ids", [])) | set(
            expected.get("contains_concepts", [])
        )
        predicted_counts = Counter(
            candidate["predicted_concept_id"] for candidate in case_candidates
        )
        for candidate in case_candidates:
            candidates.append(candidate)
            concept_id = candidate["predicted_concept_id"]
            if concept_id in explicit and predicted_counts[concept_id] == 1:
                seed.append({
                    "occurrence_id": candidate["occurrence_id"],
                    "gold_concept_id": concept_id,
                    "decision_source": "public_golden_explicit_unique",
                    "reason": "prediction_is_explicit_and_unique_in_public_golden_expectation",
                })
            else:
                pending.append(_blind(candidate))

    dev = json.loads(dev_path.read_text(encoding="utf-8"))
    for document in dev["documents"]:
        case_candidates, ambiguous, case_protected = _events(
            document["content"], "dev-document", document["id"], "text", registry
        )
        protected_checks.extend(case_protected)
        counters.update(
            dev_document_auto=len(case_candidates), ambiguous=ambiguous,
            protected=len(case_protected),
        )
        candidates.extend(case_candidates)
        pending.extend(_blind(candidate) for candidate in case_candidates)
    for query in dev["queries"]:
        case_candidates, ambiguous, case_protected = _events(
            query["text"], "dev-query", query["id"], "text", registry
        )
        protected_checks.extend(case_protected)
        counters.update(
            dev_query_auto=len(case_candidates), ambiguous=ambiguous,
            protected=len(case_protected),
        )
        candidates.extend(case_candidates)
        pending.extend(_blind(candidate) for candidate in case_candidates)

    candidates.sort(key=lambda item: item["occurrence_id"])
    if len({item["occurrence_id"] for item in candidates}) != len(candidates):
        raise ValueError("duplicate occurrence_id")
    candidate_set_sha256 = hashlib.sha256(_jsonl_bytes(candidates)).hexdigest()
    for item in seed:
        item["candidate_set_sha256"] = candidate_set_sha256
    for item in pending:
        item["candidate_set_sha256"] = candidate_set_sha256
    seed.sort(key=lambda item: item["occurrence_id"])
    pending.sort(key=lambda item: item["occurrence_id"])
    report = {
        "schema_version": "auto-match-candidates-o3-v1",
        "registry_version": registry["version"],
        "registry_sha256": registry["hash"],
        "registry_schema_sha256": registry["schema_hash"],
        "normalizer_sha256": _sha256(NORMALIZER),
        "candidate_set_sha256": candidate_set_sha256,
        "inputs": {
            "golden_sha256": _sha256(golden_path),
            "dev_sha256": _sha256(dev_path),
        },
        "counts": {
            "candidates": len(candidates),
            "derivable_seed": len(seed),
            "blind_pending": len(pending),
            "golden_auto": counters["golden_auto"],
            "dev_document_auto": counters["dev_document_auto"],
            "dev_query_auto": counters["dev_query_auto"],
            "ambiguous_events": counters["ambiguous"],
            "protected_values": counters["protected"],
        },
        "adjudication_status": "partial_public_seed_only",
        "heldout_accessed": False,
    }
    if snapshot is not None and report["counts"] != snapshot["counts"]:
        raise ValueError(
            "corrected public snapshot count drift: "
            f"expected={snapshot['counts']} actual={report['counts']}"
        )
    if snapshot is not None and candidate_set_sha256 != snapshot["candidate_set_sha256"]:
        raise ValueError(
            "corrected candidate hash drift: "
            f"expected={snapshot['candidate_set_sha256']} "
            f"actual={candidate_set_sha256}"
        )
    report["_protected_checks"] = protected_checks
    return candidates, seed, pending, report


def _blind(candidate: dict) -> dict:
    return {
        key: value for key, value in candidate.items()
        if key not in {"predicted_concept_id", "basis"}
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_bytes(_jsonl_bytes(records))


def _jsonl_bytes(records: list[dict]) -> bytes:
    return "".join(
        canonical_json(record) + "\n" for record in records
    ).encode("utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _decision_map(seed_path: Path, blind_path: Path) -> tuple[dict[str, dict], dict]:
    expected_hashes = {
        seed_path: BASELINE_SNAPSHOT["seed_sha256"],
        blind_path: BASELINE_SNAPSHOT["blind_sha256"],
    }
    for path, expected in expected_hashes.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"immutable adjudication input drift for {path.name}: "
                f"expected={expected} actual={actual}"
            )
    seed = _read_jsonl(seed_path)
    blind = _read_jsonl(blind_path)
    decisions: dict[str, dict] = {}
    for decision in [*seed, *blind]:
        occurrence_id = decision["occurrence_id"]
        if occurrence_id in decisions:
            raise ValueError(f"duplicate adjudication occurrence_id: {occurrence_id}")
        decisions[occurrence_id] = decision
    expected_count = (
        BASELINE_SNAPSHOT["counts"]["derivable_seed"]
        + BASELINE_SNAPSHOT["counts"]["blind_adjudicated"]
    )
    if len(decisions) != expected_count:
        raise ValueError(
            f"baseline adjudication count drift: expected={expected_count} "
            f"actual={len(decisions)}"
        )
    return decisions, {
        "seed_sha256": expected_hashes[seed_path],
        "blind_sha256": expected_hashes[blind_path],
        "seed_count": len(seed),
        "blind_count": len(blind),
    }


def _baseline_evaluation(
    candidate_path: Path,
    decisions: dict[str, dict],
    evaluation_path: Path,
) -> None:
    if not candidate_path.exists():
        return None
    candidate_hash = _sha256(candidate_path)
    if candidate_hash != BASELINE_SNAPSHOT["candidate_set_sha256"]:
        raise ValueError(
            "baseline candidate input drift: "
            f"expected={BASELINE_SNAPSHOT['candidate_set_sha256']} "
            f"actual={candidate_hash}"
        )
    candidates = _read_jsonl(candidate_path)
    candidate_ids = {item["occurrence_id"] for item in candidates}
    if candidate_ids != set(decisions):
        raise ValueError("baseline candidates and exact occurrence decisions differ")
    classification = evaluate_auto_matches(candidates, list(decisions.values()))
    expected = {
        key: BASELINE_SNAPSHOT["counts"][key] for key in ("TP", "FP", "FN")
    }
    actual = {key: classification["counts"][key] for key in expected}
    if actual != expected:
        raise ValueError(
            f"baseline adjudication drift: expected={expected} actual={actual}"
        )
    if not evaluation_path.exists():
        raise ValueError(
            "complete pre-freeze baseline evaluation is required"
        )
    preserved = json.loads(evaluation_path.read_text(encoding="utf-8"))
    expected_fields = {
        "candidate_set_sha256": candidate_hash,
        "ambiguous_review_detections": 10,
        "protected_spans_checked": 37,
        "protected_span_mutations": 0,
        "selective_coverage": 145 / 155,
        "heldout_accessed": False,
    }
    actual_fields = {
        key: preserved.get(key) for key in expected_fields
    }
    if actual_fields != expected_fields or preserved.get("counts") != {
        "TP": 139, "FP": 6, "FN": 0, "UNCERTAIN": 0
    }:
        raise ValueError(
            "complete pre-freeze baseline evaluation drift: "
            f"expected={expected_fields} actual={actual_fields}"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden", type=Path, default=ROOT / "tests/fixtures/golden.jsonl"
    )
    parser.add_argument(
        "--dev", type=Path, default=ROOT / "tests/fixtures/dev_retrieval.json"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--registry-schema", type=Path)
    args = parser.parse_args(argv)
    for path in (args.golden, args.dev, args.output_dir):
        _reject_heldout(path)
    registry = load_registry(args.registry, args.registry_schema)
    seed_path = args.output_dir / "dev-auto-match-adjudication-seed.jsonl"
    blind_path = args.output_dir / "dev-auto-match-adjudication-blind.jsonl"
    pending_path = args.output_dir / "dev-auto-match-blind-pending.jsonl"
    candidate_path = args.output_dir / "dev-auto-match-candidates.jsonl"
    baseline_evaluation_path = (
        args.output_dir / "dev-auto-match-evaluation-baseline.json"
    )
    decisions, adjudication_inputs = _decision_map(seed_path, blind_path)
    if _sha256(pending_path) != BASELINE_SNAPSHOT["pending_sha256"]:
        raise ValueError("baseline blind-pending audit artifact drift")
    _baseline_evaluation(
        candidate_path,
        decisions,
        baseline_evaluation_path,
    )
    baseline_candidates = _read_jsonl(candidate_path)
    baseline_false_positives = []
    for candidate in baseline_candidates:
        decision = decisions[candidate["occurrence_id"]]
        if decision["gold_concept_id"] == candidate["predicted_concept_id"]:
            continue
        baseline_false_positives.append({
            "occurrence_id": candidate["occurrence_id"],
            "predicted_concept_id": candidate["predicted_concept_id"],
            "gold_concept_id": decision["gold_concept_id"],
            "surface": candidate["surface"],
            "source_group": candidate["source_group"],
            "source_id": candidate["source_id"],
        })
    if len(baseline_false_positives) != BASELINE_SNAPSHOT["counts"]["FP"]:
        raise ValueError(
            "baseline false-positive inventory drift: "
            f"expected={BASELINE_SNAPSHOT['counts']['FP']} "
            f"actual={len(baseline_false_positives)}"
        )
    candidates, seed, pending, report = generate(
        args.golden, args.dev, registry
    )
    protected_checks = report.pop("_protected_checks")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    final_ids = {item["occurrence_id"] for item in candidates}
    baseline_ids = set(decisions)
    new_ids = sorted(final_ids - baseline_ids)
    removed_ids = sorted(baseline_ids - final_ids)
    if new_ids:
        raise ValueError(
            f"corrected candidate set introduced new occurrence IDs: {new_ids}"
        )
    final_decisions = [decisions[item["occurrence_id"]] for item in candidates]
    final_evaluation = evaluate_auto_matches(
        candidates,
        final_decisions,
        ambiguous_count=report["counts"]["ambiguous_events"],
        protected_checks=protected_checks,
    )
    if final_evaluation["counts"] != {
        "TP": 137, "FP": 0, "FN": 0, "UNCERTAIN": 0
    }:
        raise ValueError(
            f"corrected adjudication drift: {final_evaluation['counts']}"
        )
    final_candidate_path = (
        args.output_dir / "dev-auto-match-candidates-final.jsonl"
    )
    final_seed_path = (
        args.output_dir / "dev-auto-match-adjudication-seed-final.jsonl"
    )
    final_pending_path = (
        args.output_dir / "dev-auto-match-blind-pending-final.jsonl"
    )
    _write_jsonl(final_candidate_path, candidates)
    _write_jsonl(final_seed_path, seed)
    _write_jsonl(final_pending_path, pending)
    final_evaluation.update({
        "schema_version": FINAL_SNAPSHOT["schema_version"],
        "candidate_set_sha256": report["candidate_set_sha256"],
        "adjudication_scope": "complete_seed_plus_blind_reused_by_exact_occurrence_id",
        "baseline_candidate_set_sha256": BASELINE_SNAPSHOT["candidate_set_sha256"],
        "heldout_accessed": False,
    })
    final_evaluation_path = (
        args.output_dir / "dev-auto-match-evaluation-final.json"
    )
    final_evaluation_path.write_text(
        json.dumps(
            final_evaluation,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    final_generation_path = (
        args.output_dir / "dev-auto-match-generation-final.json"
    )
    final_generation_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    correction_manifest = {
        "schema_version": "auto-match-correction-manifest-o3-v1",
        "baseline": {
            **BASELINE_SNAPSHOT,
            "precision": 139 / 145,
            "selective_coverage": 145 / 155,
            "ambiguous_review_detections": 10,
            "protected_spans_checked": 37,
            "protected_span_mutations": 0,
            "evaluation_sha256": _sha256(baseline_evaluation_path),
            "false_positives": baseline_false_positives,
            "recovery": {
                "method": "byte_exact_regeneration_with_pre_freeze_generator",
                "source_artifact": "pre-freeze release ZIP and wheel",
                "normalizer_sha256": "78a51eeeafceca68026efd94ceee490deb22e2efbc7ac77ee1c7c1b76760320f",
                "registry_sha256": "9ed00a5a73cbaa2b98c32ccd1e622df8b337bf87a8da3313a1b6af8872ab9e80",
                "reproduced_candidate_sha256": BASELINE_SNAPSHOT["candidate_set_sha256"],
                "reproduced_seed_sha256": BASELINE_SNAPSHOT["seed_sha256"],
                "reproduced_pending_sha256": BASELINE_SNAPSHOT["pending_sha256"],
            },
        },
        "corrections": CORRECTED_CLASSES,
        "final": {
            "candidate_set_sha256": report["candidate_set_sha256"],
            "counts": final_evaluation["counts"],
            "precision": final_evaluation["auto_match_precision"],
            "selective_coverage": final_evaluation["selective_coverage"],
            "ambiguous_events": report["counts"]["ambiguous_events"],
        },
        "adjudication_inputs": adjudication_inputs,
        "subset_validation": {
            "is_subset": final_ids <= baseline_ids,
            "baseline_occurrence_ids": len(baseline_ids),
            "final_occurrence_ids": len(final_ids),
            "removed_occurrence_ids": removed_ids,
            "new_occurrence_ids": new_ids,
            "new_occurrence_id_count": len(new_ids),
        },
        "outputs": {
            "candidates_final_sha256": _sha256(final_candidate_path),
            "seed_final_sha256": _sha256(final_seed_path),
            "blind_pending_final_sha256": _sha256(final_pending_path),
            "evaluation_final_sha256": _sha256(final_evaluation_path),
            "generation_final_sha256": _sha256(final_generation_path),
        },
        "heldout_accessed": False,
    }
    correction_path = (
        args.output_dir / "dev-auto-match-correction-manifest.json"
    )
    correction_path.write_text(
        json.dumps(
            correction_manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(canonical_json(correction_manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
