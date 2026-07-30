from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Iterable

from . import __version__
from .models import (
    Candidate,
    Mapping,
    NormalizationResult,
    Provenance,
    ResolutionDecision,
    ResultStatus,
    Segment,
    Severity,
    UnresolvedTerm,
)
from .operators import extract_operator_tokens
from .protect import collect_protected_spans
from .registry import ConceptRegistry
from .text_utils import (
    concept_token,
    detect_language,
    sentence_spans,
    unique_preserving_order,
)
from .validators import extract_quantities, validate_projection


class SemanticNormalizer:
    """Create a reversible canonical projection without overwriting source text."""

    def __init__(
        self,
        registry: ConceptRegistry,
        *,
        minimum_confidence: float = 0.82,
        ambiguity_margin: float = 0.10,
    ) -> None:
        self.registry = registry
        self.minimum_confidence = minimum_confidence
        self.ambiguity_margin = ambiguity_margin

    def normalize(
        self,
        text: str,
        *,
        source_language: str = "auto",
        target_language: str = "source",
        overrides: dict[str, ResolutionDecision] | None = None,
        attempts: int = 1,
    ) -> NormalizationResult:
        overrides = overrides or {}
        detected_language = detect_language(text) if source_language == "auto" else source_language
        if detected_language not in {*self.registry.supported_languages, "und"}:
            raise ValueError(
                f"Unsupported source language {detected_language!r}; "
                f"supported={self.registry.supported_languages}"
            )
        resolved_target_language = (
            detected_language
            if target_language == "source" and detected_language in self.registry.supported_languages
            else self.registry.default_language
            if target_language == "source"
            else target_language
        )
        if resolved_target_language not in self.registry.supported_languages:
            raise ValueError(
                f"Unsupported target language {target_language!r}; "
                f"supported=('source', *self.registry.supported_languages)"
            )

        protected = collect_protected_spans(text)
        protected_intervals = [(span.start, span.end) for span in protected]
        candidates = self.registry.find_candidates(
            text,
            language=detected_language,
            protected_intervals=protected_intervals,
        )
        groups = self._select_non_overlapping_groups(candidates)
        mappings: list[Mapping] = []
        unresolved: list[UnresolvedTerm] = []

        for group in groups:
            group = self._deduplicate_candidates(group)
            if not group:
                continue
            exemplar = group[0]
            key = f"{exemplar.start}:{exemplar.end}"
            chosen: Candidate | None = None
            method = "deterministic"
            rationale = ""
            confidence = 0.0

            override = overrides.get(key)
            if override is not None and override.concept_id is not None:
                chosen = next(
                    (candidate for candidate in group if candidate.concept_id == override.concept_id),
                    None,
                )
                if chosen is not None:
                    method = "resolver_override"
                    rationale = override.rationale
                    confidence = override.confidence

            if chosen is None and len(group) == 1:
                chosen = group[0]
                rationale = chosen.rationale
                confidence = chosen.confidence
            elif chosen is None:
                ranked = sorted(group, key=lambda candidate: candidate.confidence, reverse=True)
                top = ranked[0]
                second = ranked[1]
                if (
                    top.confidence >= self.minimum_confidence
                    and top.confidence - second.confidence >= self.ambiguity_margin
                ):
                    chosen = top
                    rationale = (
                        f"context-separated candidate by "
                        f"{top.confidence - second.confidence:.3f}; {top.rationale}"
                    )
                    confidence = top.confidence
                else:
                    unresolved.append(
                        UnresolvedTerm(
                            start=exemplar.start,
                            end=exemplar.end,
                            surface=exemplar.surface,
                            source_language=detected_language,
                            candidate_concept_ids=[
                                candidate.concept_id for candidate in ranked
                            ],
                            reason=(
                                "Candidate scores are too close or below the acceptance threshold: "
                                + ", ".join(
                                    f"{candidate.concept_id}={candidate.confidence:.3f}"
                                    for candidate in ranked
                                )
                            ),
                        )
                    )

            if chosen is not None:
                preferred_label = self.registry.preferred_label(
                    chosen.concept_id,
                    resolved_target_language,
                )
                concept = self.registry.get(chosen.concept_id)
                preserve_surface_form = (
                    (
                        chosen.label_type == "surface"
                        and resolved_target_language == detected_language
                    )
                    or concept.status == "deprecated"
                )
                emitted_label = chosen.surface if preserve_surface_form else preferred_label
                if preserve_surface_form:
                    rationale = (
                        f"{rationale}; preserved source inflection in canonical_text; "
                        "concept token carries canonical identity"
                    ).strip("; ")
                mappings.append(
                    Mapping(
                        start=chosen.start,
                        end=chosen.end,
                        source_surface=chosen.surface,
                        concept_id=chosen.concept_id,
                        preferred_label=preferred_label,
                        canonical_label=self._preserve_initial_case(
                            chosen.surface,
                            emitted_label,
                        ),
                        source_language=detected_language,
                        target_language=resolved_target_language,
                        label_type=chosen.label_type,
                        confidence=round(confidence, 4),
                        method=method,
                        rationale=rationale,
                        candidate_concept_ids=sorted(
                            {candidate.concept_id for candidate in group}
                        ),
                    )
                )

        mappings.sort(key=lambda mapping: (mapping.start, mapping.end))
        canonical_text = self._apply_mappings(text, mappings)
        concept_ids = unique_preserving_order(mapping.concept_id for mapping in mappings)
        concept_tokens = [concept_token(concept_id) for concept_id in concept_ids]
        operator_tokens = extract_operator_tokens(text)
        expansion_labels = self._expansion_labels(concept_ids)
        canonical_search_text = "\n".join(
            value
            for value in (
                text,
                canonical_text,
                " ".join(concept_tokens),
                " ".join(expansion_labels),
                " ".join(operator_tokens),
            )
            if value.strip()
        )
        segments = self._segments(
            original_text=text,
            mappings=mappings,
            concept_ids=concept_ids,
        )
        validation_issues = validate_projection(
            original_text=text,
            canonical_text=canonical_text,
            protected_values=[span.value for span in protected],
            mappings=mappings,
            unresolved_terms=unresolved,
            registry=self.registry,
        )
        if any(issue.severity is Severity.ERROR for issue in validation_issues):
            status = ResultStatus.REJECTED
        elif unresolved:
            status = ResultStatus.REVIEW
        else:
            status = ResultStatus.ACCEPTED

        return NormalizationResult(
            original_text=text,
            source_language=detected_language,
            target_language=resolved_target_language,
            canonical_text=canonical_text,
            canonical_search_text=canonical_search_text,
            concept_ids=concept_ids,
            concept_tokens=concept_tokens,
            operator_tokens=operator_tokens,
            mappings=mappings,
            unresolved_terms=unresolved,
            protected_values=[span.value for span in protected],
            quantities=extract_quantities(
                text,
                excluded_intervals=protected_intervals,
            ),
            segments=segments,
            validation_issues=validation_issues,
            status=status,
            attempts=attempts,
            provenance=Provenance(
                original_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                registry_sha256=self.registry.sha256,
                registry_scheme_id=self.registry.scheme_id,
                registry_version=self.registry.version,
                normalizer_version=__version__,
            ),
        )

    @staticmethod
    def _deduplicate_candidates(candidates: list[Candidate]) -> list[Candidate]:
        by_concept: dict[str, Candidate] = {}
        for candidate in candidates:
            existing = by_concept.get(candidate.concept_id)
            if existing is None or candidate.confidence > existing.confidence:
                by_concept[candidate.concept_id] = candidate
        return sorted(by_concept.values(), key=lambda candidate: candidate.confidence, reverse=True)

    @staticmethod
    def _select_non_overlapping_groups(candidates: list[Candidate]) -> list[list[Candidate]]:
        grouped: dict[tuple[int, int, str], list[Candidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[(candidate.start, candidate.end, candidate.surface)].append(candidate)

        selected_keys: list[tuple[int, int, str]] = []
        occupied: list[tuple[int, int]] = []
        for key in sorted(
            grouped,
            key=lambda item: (-(item[1] - item[0]), item[0], item[1]),
        ):
            start, end, _surface = key
            if any(start < old_end and end > old_start for old_start, old_end in occupied):
                continue
            occupied.append((start, end))
            selected_keys.append(key)
        return [grouped[key] for key in sorted(selected_keys, key=lambda item: item[0])]

    @staticmethod
    def _apply_mappings(text: str, mappings: Iterable[Mapping]) -> str:
        parts: list[str] = []
        cursor = 0
        for mapping in mappings:
            parts.append(text[cursor : mapping.start])
            parts.append(mapping.canonical_label)
            cursor = mapping.end
        parts.append(text[cursor:])
        return "".join(parts)

    @staticmethod
    def _preserve_initial_case(source: str, canonical: str) -> str:
        if source[:1].isupper() and canonical[:1].isalpha():
            return canonical[:1].upper() + canonical[1:]
        return canonical

    def _expansion_labels(self, concept_ids: list[str]) -> list[str]:
        labels: list[str] = []
        for concept_id in concept_ids:
            concept = self.registry.get(concept_id)
            for language in self.registry.supported_languages:
                label = concept.preferred_labels.get(language)
                if label:
                    labels.append(label)
        return unique_preserving_order(labels)

    def _segments(
        self,
        *,
        original_text: str,
        mappings: list[Mapping],
        concept_ids: list[str],
    ) -> list[Segment]:
        del concept_ids  # IDs are recalculated by source span for each segment.
        output: list[Segment] = []
        for start, end in sentence_spans(original_text):
            local_mappings = [
                mapping for mapping in mappings if mapping.start >= start and mapping.end <= end
            ]
            shifted = [
                Mapping(
                    start=mapping.start - start,
                    end=mapping.end - start,
                    source_surface=mapping.source_surface,
                    concept_id=mapping.concept_id,
                    preferred_label=mapping.preferred_label,
                    canonical_label=mapping.canonical_label,
                    source_language=mapping.source_language,
                    target_language=mapping.target_language,
                    label_type=mapping.label_type,
                    confidence=mapping.confidence,
                    method=mapping.method,
                    rationale=mapping.rationale,
                    candidate_concept_ids=mapping.candidate_concept_ids,
                )
                for mapping in local_mappings
            ]
            source = original_text[start:end]
            output.append(
                Segment(
                    start=start,
                    end=end,
                    original_text=source,
                    canonical_text=self._apply_mappings(source, shifted),
                    concept_ids=unique_preserving_order(
                        mapping.concept_id for mapping in local_mappings
                    ),
                    operator_tokens=extract_operator_tokens(source),
                )
            )
        return output
