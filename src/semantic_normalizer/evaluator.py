"""Frozen O3 retrieval ablations and selective auto-match evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .normalizer import analyzer, normalize_text, normalized_search
from .registry import automatic_surfaces

K1 = 1.2
B = 0.75
RECALL_CUTOFFS = (1, 3, 5, 10, 50)
CONDITIONS = ("A", "B", "C", "D", "E")
BOOTSTRAP_SEED = 1729
BOOTSTRAP_REPETITIONS = 2000
PHRASE_GOLD_SCHEMA = "semantic-phrase-gold-v1"
RG_GATE_SCHEMA = "semantic-rg-gate-v1"
DATASET_ROLES = {"public-development-only", "heldout-evaluation"}


def _bm25_field(
    documents: dict[str, str], query: str, *, k1: float = K1, b: float = B
) -> dict[str, float]:
    """Score one field without changing term frequencies through concatenation."""
    tokenized = {doc_id: analyzer(text) for doc_id, text in documents.items()}
    query_tokens = analyzer(query)
    if not tokenized or not query_tokens:
        return {doc_id: 0.0 for doc_id in tokenized}
    average_length = sum(map(len, tokenized.values())) / len(tokenized) or 1.0
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized.values():
        document_frequency.update(set(tokens))
    scores: dict[str, float] = {}
    for doc_id, tokens in tokenized.items():
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_tokens:
            frequency = frequencies[term]
            if not frequency:
                continue
            inverse_frequency = math.log(
                1 + (len(tokenized) - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            score += (
                inverse_frequency
                * frequency
                * (k1 + 1)
                / (
                    frequency
                    + k1 * (1 - b + b * len(tokens) / average_length)
                )
            )
        scores[doc_id] = score
    return scores


def _combine(
    document_fields: dict[str, dict[str, str]],
    query_parts: list[tuple[str, str, float]],
) -> tuple[list[str], dict[str, float]]:
    scores = {doc_id: 0.0 for doc_id in document_fields}
    by_field: dict[str, dict[str, str]] = {
        field: {doc_id: values.get(field, "") for doc_id, values in document_fields.items()}
        for field in {field for field, _, _ in query_parts}
    }
    for field, query, weight in query_parts:
        for doc_id, score in _bm25_field(by_field[field], query).items():
            scores[doc_id] += weight * score
    ranking = [
        doc_id
        for doc_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if score > 0
    ]
    return ranking, scores


def _aggregate_records(text: str, source: str, registry: dict) -> dict[str, Any]:
    records = normalize_text(text, source, "text", registry)
    return {
        "records": records,
        "text_raw": text,
        "canonical_text": "\n".join(record["canonical_text"] for record in records),
        "concept_tokens": " ".join(
            token for record in records for token in record["concept_tokens"]
        ),
        "operator_tokens": " ".join(
            token for record in records for token in record["operator_tokens"]
        ),
        "concept_ids": list(
            dict.fromkeys(
                concept_id for record in records for concept_id in record["concept_ids"]
            )
        ),
    }


def _automatic_expansions(query: dict[str, Any], registry: dict) -> list[str]:
    expansions: list[str] = []
    for concept_id in query["concept_ids"]:
        record = registry["by_id"][concept_id]
        for language in ("en", "pt-BR"):
            for value in automatic_surfaces(record, language):
                if value and value not in expansions:
                    expansions.append(value)
    return expansions


def _condition_parts(
    condition: str, query: dict[str, Any], registry: dict
) -> list[tuple[str, str, float]]:
    if condition == "A":
        return [("text_raw", query["text_raw"], 1.0)]
    if condition == "B":
        return [
            ("text_raw", query["text_raw"], 1.0),
            *[
                ("text_raw", expansion, 0.5)
                for expansion in _automatic_expansions(query, registry)
            ],
        ]
    if condition == "C":
        return [
            ("text_raw", query["text_raw"], 3.0),
            ("canonical_text", query["canonical_text"], 2.0),
        ]
    if condition == "D":
        return [
            ("text_raw", query["text_raw"], 3.0),
            ("concept_tokens", query["concept_tokens"], 2.0),
            ("operator_tokens", query["operator_tokens"], 2.0),
        ]
    if condition == "E":
        return [("canonical_text", query["canonical_text"], 1.0)]
    raise ValueError(f"unknown condition: {condition}")


def _rank_of(ranking: list[str], relevant: set[str]) -> int | None:
    return next(
        (rank for rank, document_id in enumerate(ranking, 1) if document_id in relevant),
        None,
    )


def _metrics(ranking: list[str], relevant: set[str]) -> dict[str, float | bool]:
    metrics: dict[str, float | bool] = {
        "zero_result": not ranking,
        "precision@5": len(relevant.intersection(ranking[:5])) / 5,
    }
    for cutoff in RECALL_CUTOFFS:
        metrics[f"recall@{cutoff}"] = (
            len(relevant.intersection(ranking[:cutoff])) / len(relevant)
            if relevant
            else 0.0
        )
    first = _rank_of(ranking[:10], relevant)
    metrics["mrr@10"] = 1 / first if first else 0.0
    dcg = sum(
        1 / math.log2(rank + 1)
        for rank, document_id in enumerate(ranking[:10], 1)
        if document_id in relevant
    )
    ideal = sum(
        1 / math.log2(rank + 1)
        for rank in range(1, min(len(relevant), 10) + 1)
    )
    metrics["ndcg@10"] = dcg / ideal if ideal else 0.0
    return metrics


def _mean(rows: list[dict], field: str) -> float:
    return (
        sum(float(row["metrics"][field]) for row in rows) / len(rows)
        if rows
        else 0.0
    )


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * probability)),
    )
    return ordered[index]


def _paired_bootstrap(
    deltas: list[float],
    *,
    seed: int = BOOTSTRAP_SEED,
    repetitions: int = BOOTSTRAP_REPETITIONS,
) -> dict:
    if not deltas:
        return {
            "delta": 0.0, "ci95": [0.0, 0.0], "n_queries": 0,
            "seed": seed, "repetitions": repetitions,
        }
    generator = random.Random(seed)
    samples = [
        sum(deltas[generator.randrange(len(deltas))] for _ in deltas) / len(deltas)
        for _ in range(repetitions)
    ]
    return {
        "delta": sum(deltas) / len(deltas),
        "ci95": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
        "n_queries": len(deltas),
        "seed": seed,
        "repetitions": repetitions,
    }


def evaluate_ablations(dataset: dict, registry: dict) -> dict:
    """Evaluate the frozen A-E definitions with one normalization per item."""
    documents = {
        item["id"]: _aggregate_records(item["content"], item["id"], registry)
        for item in dataset["documents"]
    }
    queries = {
        item["id"]: _aggregate_records(item["text"], item["id"], registry)
        for item in dataset["queries"]
    }
    rows: list[dict] = []
    for query_spec in dataset["queries"]:
        query = queries[query_spec["id"]]
        relevant = set(query_spec.get("relevant_doc_ids", []))
        condition_rows = {}
        for condition in CONDITIONS:
            start = time.perf_counter_ns()
            ranking, scores = _combine(
                documents, _condition_parts(condition, query, registry)
            )
            elapsed = time.perf_counter_ns() - start
            condition_rows[condition] = {
                "ranking": ranking[:50],
                "metrics": _metrics(ranking, relevant),
                "first_relevant_rank": _rank_of(ranking, relevant),
                "latency_ns_observed": elapsed,
                "nonzero_scores": sum(score > 0 for score in scores.values()),
            }
        baseline_rank = condition_rows["A"]["first_relevant_rank"]
        fallback_rank = len(documents) + 1
        for condition in CONDITIONS[1:]:
            rank = condition_rows[condition]["first_relevant_rank"]
            condition_rows[condition]["relevant_rank_delta_vs_A"] = (
                (rank or fallback_rank) - (baseline_rank or fallback_rank)
            )
        rows.append({
            "id": query_spec["id"],
            "stratum": query_spec.get("stratum", "unspecified"),
            "relevant_doc_ids": sorted(relevant),
            "conditions": condition_rows,
        })

    metric_names = [
        *(f"recall@{cutoff}" for cutoff in RECALL_CUTOFFS),
        "precision@5", "mrr@10", "ndcg@10",
    ]
    strata: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        strata[row["stratum"]].append(row)
    summaries = {}
    for condition in CONDITIONS:
        condition_rows = [
            {"metrics": row["conditions"][condition]["metrics"]}
            for row in rows
            if row["relevant_doc_ids"]
        ]
        summaries[condition] = {
            **{name: _mean(condition_rows, name) for name in metric_names},
            "zero_result_rate": sum(
                row["conditions"][condition]["metrics"]["zero_result"] for row in rows
            ) / len(rows),
            "latency_ns_observed": {
                "total": sum(row["conditions"][condition]["latency_ns_observed"] for row in rows),
                "measurement": "observational_not_an_inference_gate",
            },
        }
    stratum_summary = {}
    for stratum, members in sorted(strata.items()):
        with_qrels = [row for row in members if row["relevant_doc_ids"]]
        if not with_qrels:
            stratum_summary[stratum] = {
                "status": "not_run",
                "reason": "stratum_has_no_queries_with_qrels",
                "n_queries": 0,
            }
            continue
        stratum_summary[stratum] = {
            "status": "evaluated",
            "n_queries": len(with_qrels),
            "conditions": {
                condition: {
                    name: _mean(
                        [
                            {"metrics": row["conditions"][condition]["metrics"]}
                            for row in with_qrels
                        ],
                        name,
                    )
                    for name in metric_names
                }
                for condition in CONDITIONS
            },
            "paired_comparisons_vs_A": {
                condition: {
                    name: _paired_bootstrap([
                        float(row["conditions"][condition]["metrics"][name])
                        - float(row["conditions"]["A"]["metrics"][name])
                        for row in with_qrels
                    ])
                    for name in metric_names
                }
                for condition in CONDITIONS[1:]
            },
        }
    paired_comparisons = {}
    qrel_rows = [row for row in rows if row["relevant_doc_ids"]]
    for condition in CONDITIONS[1:]:
        paired_comparisons[condition] = {}
        for name in metric_names:
            deltas = [
                float(row["conditions"][condition]["metrics"][name])
                - float(row["conditions"]["A"]["metrics"][name])
                for row in qrel_rows
            ]
            paired_comparisons[condition][name] = _paired_bootstrap(deltas)
    regressions = []
    for row in rows:
        if not row["relevant_doc_ids"]:
            continue
        for condition in CONDITIONS[1:]:
            delta = row["conditions"][condition]["relevant_rank_delta_vs_A"]
            if delta > 0:
                regressions.append({
                    "query_id": row["id"], "condition": condition,
                    "rank_drop": delta, "stratum": row["stratum"],
                })
    regressions.sort(key=lambda item: (-item["rank_drop"], item["query_id"], item["condition"]))
    literal_regressions = {
        condition: sorted(
            row["id"] for row in rows
            if row["stratum"] == "literal"
            and row["conditions"][condition]["relevant_rank_delta_vs_A"] > 0
        )
        for condition in CONDITIONS[1:]
    }
    raw_bytes = sum(len(item["text_raw"].encode("utf-8")) for item in documents.values())
    index_bytes = {
        condition: sum(
            len(
                "".join(
                    item[field] for field in {
                        "A": ("text_raw",),
                        "B": ("text_raw",),
                        "C": ("text_raw", "canonical_text"),
                        "D": ("text_raw", "concept_tokens", "operator_tokens"),
                        "E": ("canonical_text",),
                    }[condition]
                ).encode("utf-8")
            )
            for item in documents.values()
        )
        for condition in CONDITIONS
    }
    return {
        "evaluation_type": "retrieval",
        "evaluation_version": "ablations-o3-v1",
        "dataset_role": dataset.get("dataset_role", "unspecified"),
        "registry_version": registry["version"],
        "registry_sha256": registry["hash"],
        "analyzer": {
            "unicode_normalization": "NFC", "case": "casefold",
            "accent_folding": False, "k1": K1, "b": B, "tie_break": "document_id",
        },
        "documents": len(documents), "queries": len(queries),
        "conditions": summaries, "strata": stratum_summary,
        "paired_comparisons_vs_A": paired_comparisons,
        "literal_regressions_vs_A": literal_regressions,
        "worst_regressions": regressions[:20],
        "index_size_utf8_bytes": {
            condition: {
                "bytes": size, "raw_bytes": raw_bytes,
                "delta_bytes": size - raw_bytes,
            }
            for condition, size in index_bytes.items()
        },
        "adapters": {
            "dense": {"status": "not_run", "reason": "no frozen dense adapter"},
            "rrf": {"status": "not_run", "reason": "no second executed retriever"},
            "reranker": {"status": "not_run", "reason": "no frozen reranker"},
        },
        "rows": rows,
        "limitations": [
            "Development retrieval results are not held-out evidence.",
            "perf_counter_ns latency is observational and excluded from inference.",
            "A positive mean is not a significant improvement when the paired CI includes zero.",
        ],
    }


def evaluate_auto_matches(
    candidates: list[dict],
    adjudications: list[dict],
    *,
    ambiguous_count: int = 0,
    protected_checks: list[dict] | None = None,
) -> dict:
    """Score automatic predictions; unadjudicated occurrences remain UNCERTAIN."""
    decisions = {item["occurrence_id"]: item for item in adjudications}
    counts = Counter({"TP": 0, "FP": 0, "FN": 0, "UNCERTAIN": 0})
    false_merges = []
    protected_checks = protected_checks or []
    protected_mutations = [
        check for check in protected_checks
        if not (
            check["value"] == check["source_value"]
            == check["sequence_value"] == check["canonical_value"]
        )
    ]
    rows = []
    for candidate in candidates:
        occurrence_id = candidate["occurrence_id"]
        predicted = candidate.get("predicted_concept_id")
        decision = decisions.get(occurrence_id)
        gold = decision.get("gold_concept_id") if decision else "UNCERTAIN"
        if gold == "UNCERTAIN":
            label = "UNCERTAIN"
        else:
            if predicted and gold == predicted:
                label = "TP"
            elif predicted:
                label = "FP"
                false_merges.append({
                    "occurrence_id": occurrence_id,
                    "predicted_concept_id": predicted,
                    "gold_concept_id": gold,
                })
            elif gold != "NO_CONCEPT":
                label = "FN"
            else:
                label = "UNCERTAIN"
        counts[label] += 1
        rows.append({"occurrence_id": occurrence_id, "label": label})
    automatic = counts["TP"] + counts["FP"]
    adjudicated = counts["TP"] + counts["FP"] + counts["FN"]
    total_auto = len(candidates)
    return {
        "evaluation_type": "auto-match-selective-o3",
        "counts": dict(counts),
        "auto_match_precision": (
            counts["TP"] / automatic
            if automatic and not counts["UNCERTAIN"] else "not_run"
        ),
        "adjudicated_subset_precision": (
            counts["TP"] / automatic if automatic else "not_run"
        ),
        "selective_coverage": (
            total_auto / (total_auto + ambiguous_count)
            if total_auto + ambiguous_count else 0.0
        ),
        "adjudication_coverage": (
            adjudicated / len(candidates) if candidates else 0.0
        ),
        "ambiguous_review_detections": ambiguous_count,
        "false_automatic_merges": false_merges,
        "protected_spans_checked": len(protected_checks),
        "protected_span_mutations": len(protected_mutations),
        "protected_mutation_details": protected_mutations,
        "rows": rows,
        "claim_status": (
            "partial_adjudication" if counts["UNCERTAIN"] else "fully_adjudicated"
        ),
    }


def dataset_sha256(dataset: dict) -> str:
    payload = json.dumps(
        dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _closed_keys(value: object, required: set[str], where: str) -> dict:
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{where}: expected exactly {sorted(required)}")
    return value


def _dataset_metadata(dataset_role: object) -> dict:
    if dataset_role not in DATASET_ROLES:
        raise ValueError(
            f"invalid dataset_role; expected one of {sorted(DATASET_ROLES)}"
        )
    heldout = dataset_role == "heldout-evaluation"
    return {
        "heldout_accessed": heldout,
        "claim_scope": (
            "heldout_evaluation" if heldout
            else "public_development_only_not_heldout"
        ),
    }


def evaluate_phrase_gold(dataset: dict, registry: dict) -> dict:
    """Evaluate governed phrase semantics as an exact all-or-nothing contract."""
    _closed_keys(
        dataset,
        {"schema_version", "dataset_role", "language_scope", "phrases"},
        "phrase dataset",
    )
    if dataset["schema_version"] != PHRASE_GOLD_SCHEMA:
        raise ValueError(f"unsupported phrase schema: {dataset['schema_version']}")
    metadata = _dataset_metadata(dataset["dataset_role"])
    if not isinstance(dataset["phrases"], list) or not dataset["phrases"]:
        raise ValueError("phrase dataset must contain phrases")
    rows = []
    false_merges: list[dict] = []
    missing_units: list[dict] = []
    wrong_roles: list[dict] = []
    wrong_senses: list[dict] = []
    wrong_relations: list[dict] = []
    protected_mutations: list[dict] = []
    expected_unit_total = 0
    matched_unit_total = 0
    abstentions_expected = 0
    abstentions_passed = 0
    for phrase in dataset["phrases"]:
        _closed_keys(
            phrase,
            {
                "id", "language", "kind", "text", "expected_behavior",
                "units", "relations", "protected",
            },
            "phrase",
        )
        if phrase["expected_behavior"] not in {"match", "abstain"}:
            raise ValueError(f"{phrase['id']}: invalid expected_behavior")
        if phrase["kind"] not in {"text", "markdown"}:
            raise ValueError(f"{phrase['id']}: invalid kind")
        expected_units = phrase["units"]
        expected_relations = phrase["relations"]
        expected_protected = phrase["protected"]
        for unit in expected_units:
            _closed_keys(
                unit,
                {"id", "start", "end", "concept_id", "role", "sense"},
                f"{phrase['id']}.unit",
            )
            if phrase["text"][unit["start"]:unit["end"]].strip() == "":
                raise ValueError(f"{phrase['id']}.{unit['id']}: empty source span")
            if unit["concept_id"] not in registry["by_id"]:
                raise ValueError(
                    f"{phrase['id']}.{unit['id']}: unknown concept_id"
                )
        expected_ids = {unit["id"] for unit in expected_units}
        if len(expected_ids) != len(expected_units):
            raise ValueError(f"{phrase['id']}: duplicate gold unit id")
        for relation in expected_relations:
            _closed_keys(
                relation,
                {"type", "source", "target"},
                f"{phrase['id']}.relation",
            )
            if relation["source"] not in expected_ids or relation["target"] not in expected_ids:
                raise ValueError(f"{phrase['id']}: relation references unknown unit")
        for protected in expected_protected:
            _closed_keys(
                protected,
                {"start", "end", "kind", "original"},
                f"{phrase['id']}.protected",
            )
            if (
                phrase["text"][protected["start"]:protected["end"]]
                != protected["original"]
            ):
                raise ValueError(f"{phrase['id']}: invalid protected source span")

        records = normalize_text(
            phrase["text"], phrase["id"], phrase["kind"], registry
        )
        actual_units = []
        runtime_to_gold: dict[tuple[int, str], str] = {}
        expected_by_signature = {
            (unit["start"], unit["end"], unit["concept_id"]): unit
            for unit in expected_units
        }
        if len(expected_by_signature) != len(expected_units):
            raise ValueError(f"{phrase['id']}: duplicate gold unit signature")
        for record_index, record in enumerate(records):
            for unit in record["semantic_units"]:
                if unit.get("governance") != "approved_lexicon":
                    continue
                actual = {
                    "start": unit["start"],
                    "end": unit["end"],
                    "concept_id": unit["concept_id"],
                    "role": unit["role"],
                    "sense": unit["sense"],
                }
                actual_units.append(actual)
                expected = expected_by_signature.get(
                    (actual["start"], actual["end"], actual["concept_id"])
                )
                if expected:
                    runtime_to_gold[(record_index, unit["unit_id"])] = expected["id"]
        actual_by_signature = {
            (unit["start"], unit["end"], unit["concept_id"]): unit
            for unit in actual_units
        }
        phrase_missing = [
            unit for signature, unit in expected_by_signature.items()
            if signature not in actual_by_signature
        ]
        phrase_false = [
            unit for signature, unit in actual_by_signature.items()
            if signature not in expected_by_signature
        ]
        phrase_wrong_roles = []
        phrase_wrong_senses = []
        for signature in expected_by_signature.keys() & actual_by_signature.keys():
            expected = expected_by_signature[signature]
            actual = actual_by_signature[signature]
            if expected["role"] != actual["role"]:
                phrase_wrong_roles.append({
                    "unit_id": expected["id"],
                    "expected": expected["role"],
                    "actual": actual["role"],
                })
            if expected["sense"] != actual["sense"]:
                phrase_wrong_senses.append({
                    "unit_id": expected["id"],
                    "expected": expected["sense"],
                    "actual": actual["sense"],
                })
            if (
                expected["role"] == actual["role"]
                and expected["sense"] == actual["sense"]
            ):
                matched_unit_total += 1
        expected_unit_total += len(expected_units)

        actual_relation_set: set[tuple[str, str, str]] = set()
        unmapped_relations = []
        for record_index, record in enumerate(records):
            for relation in record["semantic_relations"]:
                source = runtime_to_gold.get(
                    (record_index, relation["source_unit"])
                )
                target = runtime_to_gold.get(
                    (record_index, relation["target_unit"])
                )
                if source is None or target is None:
                    unmapped_relations.append({
                        "type": relation["type"],
                        "source_runtime": relation["source_unit"],
                        "target_runtime": relation["target_unit"],
                    })
                else:
                    actual_relation_set.add((relation["type"], source, target))
        expected_relation_set = {
            (item["type"], item["source"], item["target"])
            for item in expected_relations
        }
        phrase_wrong_relations = [
            {"kind": "missing", "type": item[0], "source": item[1], "target": item[2]}
            for item in sorted(expected_relation_set - actual_relation_set)
        ] + [
            {"kind": "extra", "type": item[0], "source": item[1], "target": item[2]}
            for item in sorted(actual_relation_set - expected_relation_set)
        ] + [
            {"kind": "unmapped_extra", **item} for item in unmapped_relations
        ]

        actual_protected = [
            {
                "start": item["start"],
                "end": item["end"],
                "kind": item["kind"],
                "original": item["original"],
            }
            for record in records for item in record["protected"]
        ]
        protected_ok = {
            (item["start"], item["end"], item["kind"], item["original"])
            for item in actual_protected
        } == {
            (item["start"], item["end"], item["kind"], item["original"])
            for item in expected_protected
        }
        phrase_protected_mutations = [] if protected_ok else [{
            "expected": expected_protected,
            "actual": actual_protected,
        }]
        contract_ok = not any((
            phrase_missing,
            phrase_false,
            phrase_wrong_roles,
            phrase_wrong_senses,
            phrase_wrong_relations,
            phrase_protected_mutations,
        ))
        safety_ok = not phrase_false and not phrase_protected_mutations
        if phrase["expected_behavior"] == "abstain":
            abstentions_expected += 1
            contract_ok = contract_ok and not actual_units
            safety_ok = safety_ok and not actual_units
            abstentions_passed += int(contract_ok)
        represented = (
            phrase["expected_behavior"] == "match"
            and bool(expected_units)
            and contract_ok
        )
        diagnostics = {
            "false_merges": phrase_false,
            "missing_units": phrase_missing,
            "wrong_roles": phrase_wrong_roles,
            "wrong_senses": phrase_wrong_senses,
            "wrong_relations": phrase_wrong_relations,
            "protected_mutations": phrase_protected_mutations,
        }
        rows.append({
            "id": phrase["id"],
            "language": phrase["language"],
            "expected_behavior": phrase["expected_behavior"],
            "contract_passed": contract_ok,
            "safety_passed": safety_ok,
            "semantically_represented": represented,
            "expected_governed_units": len(expected_units),
            "actual_governed_units": len(actual_units),
            "diagnostics": diagnostics,
        })
        for target, values in (
            (false_merges, phrase_false),
            (missing_units, phrase_missing),
            (wrong_roles, phrase_wrong_roles),
            (wrong_senses, phrase_wrong_senses),
            (wrong_relations, phrase_wrong_relations),
            (protected_mutations, phrase_protected_mutations),
        ):
            target.extend({"phrase_id": phrase["id"], **item} for item in values)
    contract_passed = sum(row["contract_passed"] for row in rows)
    safety_passed = sum(row["safety_passed"] for row in rows)
    semantically_represented = sum(
        row["semantically_represented"] for row in rows
    )
    coverage = semantically_represented / len(rows)
    return {
        "evaluation_type": "semantic-phrase-gold",
        "schema_version": PHRASE_GOLD_SCHEMA,
        "dataset_role": dataset["dataset_role"],
        "dataset_sha256": dataset_sha256(dataset),
        "contract_passed": contract_passed,
        "safety_passed": safety_passed,
        "semantically_represented": semantically_represented,
        "total": len(rows),
        "coverage": coverage,
        "majority_pass": coverage > 0.5,
        "governed_units_matched": matched_unit_total,
        "governed_units_expected": expected_unit_total,
        "governed_unit_coverage": (
            matched_unit_total / expected_unit_total if expected_unit_total else 0.0
        ),
        "abstentions_expected": abstentions_expected,
        "abstentions_passed": abstentions_passed,
        "false_merges": false_merges,
        "missing_units": missing_units,
        "wrong_roles": wrong_roles,
        "wrong_senses": wrong_senses,
        "wrong_relations": wrong_relations,
        "protected_mutations": protected_mutations,
        "rows": rows,
        **metadata,
    }


def _rg_sidecar(
    content: str, source: str, kind: str, registry: dict
) -> str:
    records = normalize_text(content, source, kind, registry)
    parts = [content]
    for record in records:
        parts.append(normalized_search(record))
        for concept_id in record["concept_ids"]:
            concept = registry["by_id"][concept_id]
            parts.append(concept_id)
            for language in ("en", "pt-BR"):
                parts.extend(automatic_surfaces(concept, language))
    return "\n".join(dict.fromkeys(part for part in parts if part))


def _run_rg(
    executable: str,
    query: str,
    corpus: Path,
    filenames: dict[str, str],
) -> list[str]:
    command = [executable, "-F", "-i", "-l", "--", query, str(corpus)]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"rg failed with exit {result.returncode}: {result.stderr.strip()}"
        )
    return sorted(
        filenames[Path(line).name]
        for line in result.stdout.splitlines()
        if Path(line).name in filenames
    )


def evaluate_rg_gate(dataset: dict, registry: dict) -> dict:
    """Execute a deterministic fixed-string raw-versus-sidecar rg benchmark."""
    _closed_keys(
        dataset,
        {"schema_version", "dataset_role", "documents", "queries"},
        "rg dataset",
    )
    if dataset["schema_version"] != RG_GATE_SCHEMA:
        raise ValueError(f"unsupported rg schema: {dataset['schema_version']}")
    metadata = _dataset_metadata(dataset["dataset_role"])
    executable = shutil.which("rg")
    if executable is None:
        return {
            "evaluation_type": "rg-fixed-string",
            "schema_version": RG_GATE_SCHEMA,
            "dataset_role": dataset["dataset_role"],
            "dataset_sha256": dataset_sha256(dataset),
            "status": "not_run",
            "executed": False,
            "reason": "rg executable not found on PATH",
            **metadata,
        }
    version_result = subprocess.run(
        [executable, "--version"],
        text=True,
        capture_output=True,
        check=False,
    )
    if version_result.returncode != 0 or not version_result.stdout.splitlines():
        raise RuntimeError("rg --version failed")
    rg_version = version_result.stdout.splitlines()[0]
    document_ids = [item["id"] for item in dataset["documents"]]
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("rg dataset contains duplicate document ids")
    rows = []
    with tempfile.TemporaryDirectory(prefix="semantic-rg-gate-") as directory:
        root = Path(directory)
        raw_dir, sidecar_dir = root / "raw", root / "sidecar"
        raw_dir.mkdir()
        sidecar_dir.mkdir()
        filenames = {}
        for number, document in enumerate(dataset["documents"], 1):
            _closed_keys(document, {"id", "kind", "content"}, "rg document")
            if document["kind"] not in {"text", "markdown"}:
                raise ValueError(f"{document['id']}: invalid rg document kind")
            filename = f"{number:04d}.txt"
            filenames[filename] = document["id"]
            (raw_dir / filename).write_text(document["content"], encoding="utf-8")
            (sidecar_dir / filename).write_text(
                _rg_sidecar(
                    document["content"],
                    document["id"],
                    document["kind"],
                    registry,
                ),
                encoding="utf-8",
            )
        for query in dataset["queries"]:
            _closed_keys(
                query,
                {
                    "id", "stratum", "text", "relevant_doc_ids",
                    "negative_doc_ids",
                },
                "rg query",
            )
            relevant = set(query["relevant_doc_ids"])
            negative = set(query["negative_doc_ids"])
            if not relevant <= set(document_ids) or not negative <= set(document_ids):
                raise ValueError(f"{query['id']}: unknown qrel document")
            conditions = {}
            for name, corpus in (("raw", raw_dir), ("sidecar", sidecar_dir)):
                hits = _run_rg(executable, query["text"], corpus, filenames)
                hit_set = set(hits)
                conditions[name] = {
                    "hit_doc_ids": hits,
                    "relevant_hit": bool(relevant & hit_set),
                    "false_hit": bool(negative & hit_set),
                    "zero_result": not hits,
                }
            rows.append({
                "id": query["id"],
                "stratum": query["stratum"],
                "query_starts_with_hyphen": query["text"].startswith("-"),
                "relevant_doc_ids": sorted(relevant),
                "negative_doc_ids": sorted(negative),
                "conditions": conditions,
                "exact_literal_regression": (
                    query["stratum"] == "literal"
                    and conditions["raw"]["relevant_hit"]
                    and not conditions["sidecar"]["relevant_hit"]
                ),
            })

    summaries = {}
    strata = sorted({row["stratum"] for row in rows})
    for condition in ("raw", "sidecar"):
        with_relevant = [row for row in rows if row["relevant_doc_ids"]]
        with_negative = [row for row in rows if row["negative_doc_ids"]]
        summaries[condition] = {
            "hit_rate": (
                sum(row["conditions"][condition]["relevant_hit"] for row in with_relevant)
                / len(with_relevant) if with_relevant else 0.0
            ),
            "false_hit_rate": (
                sum(row["conditions"][condition]["false_hit"] for row in with_negative)
                / len(with_negative) if with_negative else 0.0
            ),
            "zero_result_rate": (
                sum(row["conditions"][condition]["zero_result"] for row in rows)
                / len(rows) if rows else 0.0
            ),
            "strata": {
                stratum: {
                    "queries": len(members),
                    "hit_rate": (
                        sum(
                            row["conditions"][condition]["relevant_hit"]
                            for row in members if row["relevant_doc_ids"]
                        )
                        / len([row for row in members if row["relevant_doc_ids"]])
                        if any(row["relevant_doc_ids"] for row in members) else 0.0
                    ),
                    "false_hit_rate": (
                        sum(
                            row["conditions"][condition]["false_hit"]
                            for row in members if row["negative_doc_ids"]
                        )
                        / len([row for row in members if row["negative_doc_ids"]])
                        if any(row["negative_doc_ids"] for row in members) else 0.0
                    ),
                }
                for stratum in strata
                for members in [[row for row in rows if row["stratum"] == stratum]]
            },
        }
    regressions = sorted(
        row["id"] for row in rows if row["exact_literal_regression"]
    )
    return {
        "evaluation_type": "rg-fixed-string",
        "schema_version": RG_GATE_SCHEMA,
        "dataset_role": dataset["dataset_role"],
        "dataset_sha256": dataset_sha256(dataset),
        "status": "executed",
        "executed": True,
        "rg_version": rg_version,
        "command": ["rg", "-F", "-i", "-l", "--", "<query>", "<corpus-dir>"],
        "documents": len(document_ids),
        "queries": len(rows),
        "conditions": summaries,
        "exact_literal_regressions": regressions,
        "hyphen_query_executed": any(
            row["query_starts_with_hyphen"] for row in rows
        ),
        "rows": rows,
        **metadata,
        "limitations": [
            "This is a public development benchmark, not held-out evidence.",
            "No statistical significance claim is made.",
        ],
    }
