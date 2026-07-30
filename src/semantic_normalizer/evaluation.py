from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bm25 import BM25Index
from .normalizer import SemanticNormalizer


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return rows


def evaluate_retrieval(
    *,
    normalizer: SemanticNormalizer,
    documents_path: str | Path,
    queries_path: str | Path,
    k_values: tuple[int, ...] = (1, 3, 5),
    target_language: str = "en",
) -> dict[str, Any]:
    documents = read_jsonl(documents_path)
    queries = read_jsonl(queries_path)
    max_k = max(k_values)

    document_views: dict[str, dict[str, str]] = {
        "raw": {},
        "canonical": {},
        "expanded": {},
    }
    normalized_documents: dict[str, dict[str, Any]] = {}
    for document in documents:
        doc_id = str(document["id"])
        text = str(document["text"])
        language = str(document.get("lang", "auto"))
        result = normalizer.normalize(
            text,
            source_language=language,
            target_language=target_language,
        )
        normalized_documents[doc_id] = result.to_dict()
        document_views["raw"][doc_id] = text
        document_views["canonical"][doc_id] = result.canonical_text
        document_views["expanded"][doc_id] = result.canonical_search_text

    indexes = {mode: BM25Index(values) for mode, values in document_views.items()}
    report: dict[str, Any] = {
        "dataset": {
            "documents": len(documents),
            "queries": len(queries),
            "target_language": target_language,
            "k_values": list(k_values),
        },
        "modes": {},
    }

    for mode, index in indexes.items():
        hit_counts = {k: 0 for k in k_values}
        recall_sums = {k: 0.0 for k in k_values}
        reciprocal_ranks: list[float] = []
        per_query: list[dict[str, Any]] = []

        for query in queries:
            query_id = str(query["id"])
            text = str(query["text"])
            language = str(query.get("lang", "auto"))
            relevant = {str(value) for value in query["relevant_doc_ids"]}
            normalized_query = normalizer.normalize(
                text,
                source_language=language,
                target_language=target_language,
            )
            query_view = {
                "raw": text,
                "canonical": normalized_query.canonical_text,
                "expanded": normalized_query.canonical_search_text,
            }[mode]
            ranking = index.search(query_view, top_k=max_k)
            ranked_ids = [doc_id for doc_id, _score in ranking]

            first_rank = next(
                (rank for rank, doc_id in enumerate(ranked_ids, start=1) if doc_id in relevant),
                None,
            )
            reciprocal_ranks.append(0.0 if first_rank is None else 1.0 / first_rank)
            for k in k_values:
                top_ids = set(ranked_ids[:k])
                retrieved_relevant = len(top_ids & relevant)
                if retrieved_relevant:
                    hit_counts[k] += 1
                recall_sums[k] += retrieved_relevant / max(len(relevant), 1)

            per_query.append(
                {
                    "query_id": query_id,
                    "query": text,
                    "query_view": query_view,
                    "relevant_doc_ids": sorted(relevant),
                    "ranking": [
                        {"doc_id": doc_id, "score": round(score, 6)}
                        for doc_id, score in ranking
                    ],
                    "first_relevant_rank": first_rank,
                }
            )

        query_count = max(len(queries), 1)
        report["modes"][mode] = {
            "mrr": round(sum(reciprocal_ranks) / query_count, 6),
            "hit_rate_at_k": {
                str(k): round(hit_counts[k] / query_count, 6) for k in k_values
            },
            "mean_recall_at_k": {
                str(k): round(recall_sums[k] / query_count, 6) for k in k_values
            },
            "per_query": per_query,
        }

    report["comparison"] = {
        "expanded_minus_raw_mrr": round(
            report["modes"]["expanded"]["mrr"] - report["modes"]["raw"]["mrr"],
            6,
        ),
        "canonical_minus_raw_mrr": round(
            report["modes"]["canonical"]["mrr"] - report["modes"]["raw"]["mrr"],
            6,
        ),
    }
    return report
