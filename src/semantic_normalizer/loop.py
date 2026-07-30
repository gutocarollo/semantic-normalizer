from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .models import (
    NormalizationResult,
    ResolutionDecision,
    ResolutionRequest,
    ResultStatus,
    Severity,
    ValidationIssue,
)
from .normalizer import SemanticNormalizer


class SemanticResolver(Protocol):
    """Resolve one ambiguous source span to one existing concept or abstain."""

    def resolve(self, request: ResolutionRequest) -> ResolutionDecision:
        ...


class StaticResolver:
    """Deterministic resolver useful for tests and human-approved mapping files."""

    def __init__(self, decisions: Mapping[str, str]) -> None:
        self.decisions = dict(decisions)

    def resolve(self, request: ResolutionRequest) -> ResolutionDecision:
        concept_id = self.decisions.get(request.surface.casefold())
        if concept_id is None:
            return ResolutionDecision(None, 0.0, "No static decision")
        return ResolutionDecision(concept_id, 1.0, "Static approved decision")


class NormalizationLoop:
    """Bounded normalizer loop.

    Deterministic mapping runs first. A resolver sees only ambiguous spans and can select only
    concepts already supplied by the registry. The loop never lets a model invent a concept.
    """

    def __init__(
        self,
        normalizer: SemanticNormalizer,
        resolver: SemanticResolver | None = None,
        *,
        max_attempts: int = 2,
        minimum_resolver_confidence: float = 0.75,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.normalizer = normalizer
        self.resolver = resolver
        self.max_attempts = max_attempts
        self.minimum_resolver_confidence = minimum_resolver_confidence

    def run(
        self,
        text: str,
        *,
        source_language: str = "auto",
        target_language: str = "source",
    ) -> NormalizationResult:
        overrides: dict[str, ResolutionDecision] = {}
        result = self.normalizer.normalize(
            text,
            source_language=source_language,
            target_language=target_language,
            attempts=1,
        )
        if self.resolver is None or not result.unresolved_terms:
            return result

        resolver_issues: list[ValidationIssue] = []
        for attempt in range(2, self.max_attempts + 1):
            changed = False
            for unresolved in result.unresolved_terms:
                concepts = tuple(
                    self.normalizer.registry.get(concept_id)
                    for concept_id in unresolved.candidate_concept_ids
                )
                decision = self.resolver.resolve(
                    ResolutionRequest(
                        text=text,
                        source_language=result.source_language,
                        span_start=unresolved.start,
                        span_end=unresolved.end,
                        surface=unresolved.surface,
                        candidates=concepts,
                    )
                )
                allowed = {concept.concept_id for concept in concepts}
                if decision.concept_id is None:
                    continue
                if decision.concept_id not in allowed:
                    resolver_issues.append(
                        ValidationIssue(
                            code="resolver_invented_concept",
                            severity=Severity.WARNING,
                            message="The resolver selected a concept outside the supplied candidate set.",
                            details={
                                "surface": unresolved.surface,
                                "selected": decision.concept_id,
                                "allowed": sorted(allowed),
                            },
                        )
                    )
                    continue
                if decision.confidence < self.minimum_resolver_confidence:
                    resolver_issues.append(
                        ValidationIssue(
                            code="resolver_low_confidence",
                            severity=Severity.WARNING,
                            message="The resolver decision was below the acceptance threshold.",
                            details={
                                "surface": unresolved.surface,
                                "selected": decision.concept_id,
                                "confidence": decision.confidence,
                                "threshold": self.minimum_resolver_confidence,
                            },
                        )
                    )
                    continue
                overrides[unresolved.key] = decision
                changed = True

            if not changed:
                break
            result = self.normalizer.normalize(
                text,
                source_language=result.source_language,
                target_language=target_language,
                overrides=overrides,
                attempts=attempt,
            )
            if not result.unresolved_terms or result.status is ResultStatus.REJECTED:
                break

        if resolver_issues:
            result.validation_issues.extend(resolver_issues)
            if result.status is ResultStatus.ACCEPTED:
                # Accepted mappings remain valid, but the run contains resolver behavior that
                # should be inspected before promoting it to a gold mapping.
                result.status = ResultStatus.REVIEW
        return result
