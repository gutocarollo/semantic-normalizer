"""Deterministic bilingual semantic normalizer."""

from .evaluator import (
    evaluate_ablations,
    evaluate_auto_matches,
    evaluate_phrase_gold,
    evaluate_rg_gate,
)
from .exporters import export_skos, export_synonym_graph
from .normalizer import (
    SCHEMA_VERSION,
    TOOL_VERSION,
    analyzer,
    ascii_fold,
    bm25_rank,
    canonical_json,
    evaluate_golden,
    evaluate_retrieval,
    infer_kind,
    normalize_text,
    normalized_search,
    read_utf8,
    sha256,
)
from .reconciliation import (
    ReconciliationResult,
    apply_response,
    make_request,
    validate_request,
)
from .registry import (
    ContractError,
    init_workspace,
    load_lexicon,
    load_registry,
    package_data,
)

__version__ = "0.4.0"

__all__ = [
    "ContractError",
    "ReconciliationResult",
    "SCHEMA_VERSION",
    "TOOL_VERSION",
    "analyzer",
    "apply_response",
    "ascii_fold",
    "bm25_rank",
    "canonical_json",
    "evaluate_golden",
    "evaluate_ablations",
    "evaluate_auto_matches",
    "evaluate_phrase_gold",
    "evaluate_rg_gate",
    "evaluate_retrieval",
    "export_skos",
    "export_synonym_graph",
    "infer_kind",
    "init_workspace",
    "load_lexicon",
    "load_registry",
    "make_request",
    "normalize_text",
    "normalized_search",
    "package_data",
    "read_utf8",
    "sha256",
    "validate_request",
]
