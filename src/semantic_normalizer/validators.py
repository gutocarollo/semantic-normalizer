from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .models import Mapping, Severity, UnresolvedTerm, ValidationIssue
from .operators import operator_counter
from .protect import collect_protected_spans
from .registry import ConceptRegistry
from .text_utils import overlaps


_QUANTITY_RE = re.compile(
    r"(?<!\w)(?:R\$\s*)?[+-]?\d+(?:[.,]\d+)?(?:\s*(?:%|°[CF]|[kMGT]?B|ms|s|min|h|mm|cm|m|km|g|kg|V|A|W|Hz))?(?!\w)",
    re.IGNORECASE | re.UNICODE,
)


def extract_quantities(
    text: str,
    *,
    excluded_intervals: Iterable[tuple[int, int]] = (),
) -> list[str]:
    intervals = tuple(excluded_intervals)
    return [
        match.group(0)
        for match in _QUANTITY_RE.finditer(text)
        if not overlaps(match.start(), match.end(), intervals)
    ]


def validate_projection(
    *,
    original_text: str,
    canonical_text: str,
    protected_values: list[str],
    mappings: list[Mapping],
    unresolved_terms: list[UnresolvedTerm],
    registry: ConceptRegistry,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    missing_protected = [value for value in protected_values if value not in canonical_text]
    if missing_protected:
        issues.append(
            ValidationIssue(
                code="protected_value_changed",
                severity=Severity.ERROR,
                message="The canonical projection changed or removed protected source content.",
                details={"missing_values": missing_protected},
            )
        )

    original_protected = collect_protected_spans(original_text)
    canonical_protected = collect_protected_spans(canonical_text)
    original_quantities = extract_quantities(
        original_text,
        excluded_intervals=((span.start, span.end) for span in original_protected),
    )
    canonical_quantities = extract_quantities(
        canonical_text,
        excluded_intervals=((span.start, span.end) for span in canonical_protected),
    )
    if Counter(original_quantities) != Counter(canonical_quantities):
        issues.append(
            ValidationIssue(
                code="quantity_changed",
                severity=Severity.ERROR,
                message="A number, amount, or unit changed during canonicalization.",
                details={
                    "original": original_quantities,
                    "canonical": canonical_quantities,
                },
            )
        )

    original_operators = operator_counter(original_text)
    canonical_operators = operator_counter(canonical_text)
    changed_operators = {
        key: {"original": original_operators[key], "canonical": canonical_operators[key]}
        for key in sorted(set(original_operators) | set(canonical_operators))
        if original_operators[key] != canonical_operators[key]
    }
    if changed_operators:
        issues.append(
            ValidationIssue(
                code="semantic_operator_changed",
                severity=Severity.ERROR,
                message="Negation, modality, condition, or exception markers changed.",
                details=changed_operators,
            )
        )

    ordered = sorted(mappings, key=lambda mapping: (mapping.start, mapping.end))
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            issues.append(
                ValidationIssue(
                    code="overlapping_mappings",
                    severity=Severity.ERROR,
                    message="Two accepted mappings overlap in the source text.",
                    details={
                        "first": previous.to_dict(),
                        "second": current.to_dict(),
                    },
                )
            )

    missing_concepts = sorted(
        {mapping.concept_id for mapping in mappings if not registry.contains(mapping.concept_id)}
    )
    if missing_concepts:
        issues.append(
            ValidationIssue(
                code="unknown_concept",
                severity=Severity.ERROR,
                message="A mapping references a concept absent from the registry.",
                details={"concept_ids": missing_concepts},
            )
        )

    for mapping in mappings:
        original_surface = original_text[mapping.start : mapping.end]
        if original_surface != mapping.source_surface:
            issues.append(
                ValidationIssue(
                    code="source_span_mismatch",
                    severity=Severity.ERROR,
                    message="A mapping span does not point to its recorded source surface.",
                    details={
                        "mapping": mapping.to_dict(),
                        "actual_surface": original_surface,
                    },
                )
            )

    if unresolved_terms:
        issues.append(
            ValidationIssue(
                code="unresolved_ambiguity",
                severity=Severity.WARNING,
                message="At least one surface form has multiple plausible concepts.",
                details={"terms": [term.to_dict() for term in unresolved_terms]},
            )
        )

    return issues
