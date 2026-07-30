from __future__ import annotations

import math
from collections import Counter

from .text_utils import tokenise


class BM25Index:
    """Small dependency-free BM25 implementation for local A/B tests."""

    def __init__(
        self,
        documents: dict[str, str],
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if not documents:
            raise ValueError("documents must not be empty")
        self.k1 = k1
        self.b = b
        self.doc_ids = list(documents)
        self.tokens = {doc_id: tokenise(text) for doc_id, text in documents.items()}
        self.term_frequencies = {
            doc_id: Counter(tokens) for doc_id, tokens in self.tokens.items()
        }
        self.doc_lengths = {doc_id: len(tokens) for doc_id, tokens in self.tokens.items()}
        self.average_length = sum(self.doc_lengths.values()) / len(self.doc_lengths)
        document_frequency: Counter[str] = Counter()
        for tokens in self.tokens.values():
            document_frequency.update(set(tokens))
        total = len(self.doc_ids)
        self.idf = {
            term: math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def score(self, query: str, doc_id: str) -> float:
        query_terms = Counter(tokenise(query))
        frequencies = self.term_frequencies[doc_id]
        doc_length = self.doc_lengths[doc_id]
        score = 0.0
        for term, query_frequency in query_terms.items():
            term_frequency = frequencies.get(term, 0)
            if term_frequency == 0:
                continue
            denominator = term_frequency + self.k1 * (
                1.0 - self.b + self.b * doc_length / max(self.average_length, 1e-9)
            )
            score += (
                self.idf.get(term, 0.0)
                * (term_frequency * (self.k1 + 1.0) / denominator)
                * query_frequency
            )
        return score

    def search(self, query: str, *, top_k: int = 10) -> list[tuple[str, float]]:
        scored = [
            (doc_id, self.score(query, doc_id))
            for doc_id in self.doc_ids
        ]
        # Zero-score documents are not retrieval results. This avoids arbitrary tie success.
        positive = [item for item in scored if item[1] > 0.0]
        return sorted(positive, key=lambda item: (-item[1], item[0]))[:top_k]
