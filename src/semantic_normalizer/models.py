from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ResultStatus(str, Enum):
    ACCEPTED = "accepted"
    REVIEW = "review"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ContextTerms:
    positive: dict[str, tuple[str, ...]] = field(default_factory=dict)
    negative: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ContextTerms":
        data = data or {}
        return cls(
            positive={
                str(lang): tuple(str(value) for value in values)
                for lang, values in data.get("positive", {}).items()
            },
            negative={
                str(lang): tuple(str(value) for value in values)
                for lang, values in data.get("negative", {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class Concept:
    concept_id: str
    preferred_labels: dict[str, str]
    alternative_labels: dict[str, tuple[str, ...]]
    hidden_labels: dict[str, tuple[str, ...]]
    surface_forms: dict[str, tuple[str, ...]]
    definitions: dict[str, str]
    part_of_speech: str
    domains: tuple[str, ...]
    context_terms: ContextTerms
    source_authority: str
    status: str = "approved"
    broader: tuple[str, ...] = ()
    related: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Concept":
        def tuple_map(key: str) -> dict[str, tuple[str, ...]]:
            return {
                str(lang): tuple(str(value) for value in values)
                for lang, values in data.get(key, {}).items()
            }

        return cls(
            concept_id=str(data["concept_id"]),
            preferred_labels={
                str(lang): str(value) for lang, value in data["preferred_labels"].items()
            },
            alternative_labels=tuple_map("alternative_labels"),
            hidden_labels=tuple_map("hidden_labels"),
            surface_forms=tuple_map("surface_forms"),
            definitions={
                str(lang): str(value) for lang, value in data.get("definitions", {}).items()
            },
            part_of_speech=str(data.get("part_of_speech", "unknown")),
            domains=tuple(str(value) for value in data.get("domains", [])),
            context_terms=ContextTerms.from_dict(data.get("context_terms")),
            source_authority=str(data.get("source_authority", "project")),
            status=str(data.get("status", "approved")),
            broader=tuple(str(value) for value in data.get("broader", [])),
            related=tuple(str(value) for value in data.get("related", [])),
        )

    def all_labels(self, language: str | None = None) -> list[tuple[str, str, str]]:
        """Return (label, language, label_type) tuples."""
        languages = (
            [language]
            if language is not None
            else sorted(
                set(self.preferred_labels)
                | set(self.alternative_labels)
                | set(self.hidden_labels)
                | set(self.surface_forms)
            )
        )
        values: list[tuple[str, str, str]] = []
        for lang in languages:
            preferred = self.preferred_labels.get(lang)
            if preferred:
                values.append((preferred, lang, "preferred"))
            values.extend(
                (label, lang, "alternative")
                for label in self.alternative_labels.get(lang, ())
            )
            values.extend((label, lang, "hidden") for label in self.hidden_labels.get(lang, ()))
            values.extend((label, lang, "surface") for label in self.surface_forms.get(lang, ()))
        return values


@dataclass(frozen=True, slots=True)
class AliasEntry:
    label: str
    normalized_label: str
    language: str
    label_type: str
    concept_id: str


@dataclass(slots=True)
class Candidate:
    start: int
    end: int
    surface: str
    concept_id: str
    language: str
    label_type: str
    lexical_score: float
    context_score: float
    confidence: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Mapping:
    start: int
    end: int
    source_surface: str
    concept_id: str
    preferred_label: str
    canonical_label: str
    source_language: str
    target_language: str
    label_type: str
    confidence: float
    method: str
    rationale: str
    candidate_concept_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class UnresolvedTerm:
    start: int
    end: int
    surface: str
    source_language: str
    candidate_concept_ids: list[str]
    reason: str

    @property
    def key(self) -> str:
        return f"{self.start}:{self.end}"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["key"] = self.key
        return value


@dataclass(slots=True)
class ValidationIssue:
    code: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["severity"] = self.severity.value
        return value


@dataclass(slots=True)
class Segment:
    start: int
    end: int
    original_text: str
    canonical_text: str
    concept_ids: list[str]
    operator_tokens: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Provenance:
    original_sha256: str
    registry_sha256: str
    registry_scheme_id: str
    registry_version: str
    normalizer_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NormalizationResult:
    original_text: str
    source_language: str
    target_language: str
    canonical_text: str
    canonical_search_text: str
    concept_ids: list[str]
    concept_tokens: list[str]
    operator_tokens: list[str]
    mappings: list[Mapping]
    unresolved_terms: list[UnresolvedTerm]
    protected_values: list[str]
    quantities: list[str]
    segments: list[Segment]
    validation_issues: list[ValidationIssue]
    status: ResultStatus
    attempts: int
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_text": self.original_text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "canonical_text": self.canonical_text,
            "canonical_search_text": self.canonical_search_text,
            "concept_ids": self.concept_ids,
            "concept_tokens": self.concept_tokens,
            "operator_tokens": self.operator_tokens,
            "mappings": [mapping.to_dict() for mapping in self.mappings],
            "unresolved_terms": [term.to_dict() for term in self.unresolved_terms],
            "protected_values": self.protected_values,
            "quantities": self.quantities,
            "segments": [segment.to_dict() for segment in self.segments],
            "validation_issues": [issue.to_dict() for issue in self.validation_issues],
            "status": self.status.value,
            "attempts": self.attempts,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ResolutionRequest:
    text: str
    source_language: str
    span_start: int
    span_end: int
    surface: str
    candidates: tuple[Concept, ...]


@dataclass(frozen=True, slots=True)
class ResolutionDecision:
    concept_id: str | None
    confidence: float
    rationale: str
