from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AliasEntry, Candidate, Concept
from .text_utils import normalize_key, overlaps, sentence_spans


@dataclass(frozen=True, slots=True)
class RegistryDiagnostic:
    severity: str
    code: str
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConceptRegistry:
    """In-memory concept scheme with multilingual lexical labels."""

    def __init__(
        self,
        *,
        scheme_id: str,
        version: str,
        default_language: str,
        supported_languages: tuple[str, ...],
        concepts: tuple[Concept, ...],
        raw_bytes: bytes,
    ) -> None:
        self.scheme_id = scheme_id
        self.version = version
        self.default_language = default_language
        self.supported_languages = supported_languages
        self.concepts = concepts
        self.raw_bytes = raw_bytes
        self.sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self._by_id = {concept.concept_id: concept for concept in concepts}
        self._aliases: dict[str, list[AliasEntry]] = {}
        for concept in concepts:
            if concept.status == "draft":
                continue
            for label, language, label_type in concept.all_labels():
                key = normalize_key(label)
                if not key:
                    continue
                self._aliases.setdefault(key, []).append(
                    AliasEntry(
                        label=label,
                        normalized_label=key,
                        language=language,
                        label_type=label_type,
                        concept_id=concept.concept_id,
                    )
                )
        self._sorted_alias_keys = sorted(self._aliases, key=lambda value: (-len(value), value))

    @classmethod
    def from_path(cls, path: str | Path) -> "ConceptRegistry":
        registry_path = Path(path)
        raw_bytes = registry_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
        return cls(
            scheme_id=str(payload["scheme_id"]),
            version=str(payload["version"]),
            default_language=str(payload.get("default_language", "en")),
            supported_languages=tuple(str(value) for value in payload["supported_languages"]),
            concepts=tuple(Concept.from_dict(value) for value in payload["concepts"]),
            raw_bytes=raw_bytes,
        )

    def get(self, concept_id: str) -> Concept:
        try:
            return self._by_id[concept_id]
        except KeyError as exc:
            raise KeyError(f"Unknown concept_id: {concept_id}") from exc

    def contains(self, concept_id: str) -> bool:
        return concept_id in self._by_id

    def preferred_label(self, concept_id: str, language: str) -> str:
        concept = self.get(concept_id)
        return (
            concept.preferred_labels.get(language)
            or concept.preferred_labels.get(self.default_language)
            or next(iter(concept.preferred_labels.values()))
        )

    def find_candidates(
        self,
        text: str,
        *,
        language: str,
        protected_intervals: list[tuple[int, int]],
    ) -> list[Candidate]:
        accepted_languages = (
            set(self.supported_languages)
            if language == "und"
            else {language}
        )
        candidates: list[Candidate] = []
        source_sentence_spans = sentence_spans(text)

        for alias_key in self._sorted_alias_keys:
            entries = [
                entry
                for entry in self._aliases[alias_key]
                if entry.language in accepted_languages
            ]
            if not entries:
                continue
            # Search each source spelling. Entries sharing the same normalized label can have
            # different capitalization but should produce one span/candidate per concept.
            spellings = sorted({entry.label for entry in entries}, key=lambda value: -len(value))
            matched_spans: set[tuple[int, int]] = set()
            for spelling in spellings:
                pattern = re.compile(
                    rf"(?<!\w){re.escape(spelling)}(?!\w)",
                    flags=re.IGNORECASE | re.UNICODE,
                )
                for match in pattern.finditer(text):
                    span = match.span()
                    if span in matched_spans or overlaps(*span, protected_intervals):
                        continue
                    matched_spans.add(span)
                    for entry in entries:
                        if normalize_key(entry.label) != normalize_key(match.group(0)):
                            continue
                        concept = self.get(entry.concept_id)
                        lexical_score = {
                            "preferred": 0.94,
                            "alternative": 0.88,
                            "surface": 0.84,
                            "hidden": 0.70,
                        }.get(entry.label_type, 0.75)
                        context_span = next(
                            (
                                (sentence_start, sentence_end)
                                for sentence_start, sentence_end in source_sentence_spans
                                if sentence_start <= match.start() and match.end() <= sentence_end
                            ),
                            (max(0, match.start() - 160), min(len(text), match.end() + 160)),
                        )
                        context_score, context_reason = self._context_score(
                            concept,
                            language=entry.language,
                            text=text,
                            source_span=span,
                            context_span=context_span,
                        )
                        confidence = max(0.0, min(0.99, lexical_score + context_score))
                        candidates.append(
                            Candidate(
                                start=match.start(),
                                end=match.end(),
                                surface=match.group(0),
                                concept_id=entry.concept_id,
                                language=entry.language,
                                label_type=entry.label_type,
                                lexical_score=round(lexical_score, 4),
                                context_score=round(context_score, 4),
                                confidence=round(confidence, 4),
                                rationale=(
                                    f"{entry.label_type} label match"
                                    + (f"; {context_reason}" if context_reason else "")
                                ),
                            )
                        )
        return candidates

    def _context_score(
        self,
        concept: Concept,
        *,
        language: str,
        text: str,
        source_span: tuple[int, int],
        context_span: tuple[int, int],
    ) -> tuple[float, str]:
        """Score context near one occurrence, not against the whole document.

        For verbs, evidence after the verb receives full weight because it commonly identifies
        the object. Evidence before the verb remains usable but receives a smaller weight. This
        prevents one occurrence from borrowing the object of another occurrence elsewhere.
        """
        positive = concept.context_terms.positive.get(language, ())
        negative = concept.context_terms.negative.get(language, ())
        if not positive and not negative:
            return 0.0, ""

        context_start, context_end = context_span
        local_text = unicodedata.normalize("NFKC", text[context_start:context_end]).casefold()
        local_source_start = source_span[0] - context_start
        local_source_end = source_span[1] - context_start
        directional = concept.part_of_speech == "verb"

        def best_weight(term: str) -> float:
            normalized_term = unicodedata.normalize("NFKC", term).casefold().strip()
            if not normalized_term:
                return 0.0
            escaped = re.escape(normalized_term).replace(r"\ ", r"\s+")
            pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.UNICODE)
            best = 0.0
            for match in pattern.finditer(local_text):
                if match.end() <= local_source_start:
                    gap = local_source_start - match.end()
                    direction_weight = 0.35 if directional else 1.0
                elif match.start() >= local_source_end:
                    gap = match.start() - local_source_end
                    direction_weight = 1.0
                else:
                    gap = 0
                    direction_weight = 0.5
                distance_weight = 1.0 / (1.0 + (gap / 20.0))
                best = max(best, direction_weight * distance_weight)
            return best

        positive_weights = [(term, best_weight(term)) for term in positive]
        negative_weights = [(term, best_weight(term)) for term in negative]
        positive_hits = [(term, weight) for term, weight in positive_weights if weight > 0.0]
        negative_hits = [(term, weight) for term, weight in negative_weights if weight > 0.0]

        strongest_positive = max((weight for _term, weight in positive_hits), default=0.0)
        strongest_negative = max((weight for _term, weight in negative_hits), default=0.0)
        score = (0.16 * strongest_positive) - (0.18 * strongest_negative)

        parts: list[str] = []
        if positive_hits:
            term, weight = max(positive_hits, key=lambda item: item[1])
            parts.append(f"nearest positive context={term!r}:{weight:.2f}")
        if negative_hits:
            term, weight = max(negative_hits, key=lambda item: item[1])
            parts.append(f"nearest negative context={term!r}:{weight:.2f}")
        return score, "; ".join(parts)

    def validate(self) -> list[RegistryDiagnostic]:
        diagnostics: list[RegistryDiagnostic] = []
        if len(self._by_id) != len(self.concepts):
            diagnostics.append(
                RegistryDiagnostic("error", "duplicate_concept_id", "Concept IDs must be unique", {})
            )

        for concept in self.concepts:
            missing = [
                language
                for language in self.supported_languages
                if language not in concept.preferred_labels
            ]
            if missing:
                diagnostics.append(
                    RegistryDiagnostic(
                        "error",
                        "missing_preferred_label",
                        f"{concept.concept_id} has no preferred label for {missing}",
                        {"concept_id": concept.concept_id, "languages": missing},
                    )
                )
            for relation in (*concept.broader, *concept.related):
                if relation not in self._by_id:
                    diagnostics.append(
                        RegistryDiagnostic(
                            "error",
                            "unknown_relation_target",
                            f"{concept.concept_id} references unknown concept {relation}",
                            {"concept_id": concept.concept_id, "target": relation},
                        )
                    )

        for alias, entries in sorted(self._aliases.items()):
            concept_ids = sorted({entry.concept_id for entry in entries})
            languages = sorted({entry.language for entry in entries})
            if len(concept_ids) > 1:
                diagnostics.append(
                    RegistryDiagnostic(
                        "warning",
                        "ambiguous_alias",
                        f"Alias {alias!r} maps to multiple concepts",
                        {
                            "alias": alias,
                            "concept_ids": concept_ids,
                            "languages": languages,
                        },
                    )
                )
        return diagnostics

    def to_skos_turtle(self, base_uri: str = "https://example.org/semantic-normalizer/") -> str:
        base_uri = base_uri.rstrip("/") + "/"

        def quote(value: str) -> str:
            return (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
            )

        lines = [
            "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
            "@prefix dct: <http://purl.org/dc/terms/> .",
            "",
            f"<{base_uri}scheme/{self.scheme_id}> a skos:ConceptScheme ;",
            f'    dct:identifier "{quote(self.scheme_id)}" ;',
            f'    dct:hasVersion "{quote(self.version)}" .',
            "",
        ]
        for concept in sorted(self.concepts, key=lambda item: item.concept_id):
            uri = f"{base_uri}concept/{concept.concept_id}"
            statements: list[str] = [
                "a skos:Concept",
                f"skos:inScheme <{base_uri}scheme/{self.scheme_id}>",
            ]
            for lang, label in sorted(concept.preferred_labels.items()):
                statements.append(f'skos:prefLabel "{quote(label)}"@{lang}')
            for lang, labels in sorted(concept.alternative_labels.items()):
                for label in labels:
                    statements.append(f'skos:altLabel "{quote(label)}"@{lang}')
            for lang, labels in sorted(concept.hidden_labels.items()):
                for label in labels:
                    statements.append(f'skos:hiddenLabel "{quote(label)}"@{lang}')
            for lang, labels in sorted(concept.surface_forms.items()):
                occupied = {
                    concept.preferred_labels.get(lang),
                    *concept.alternative_labels.get(lang, ()),
                    *concept.hidden_labels.get(lang, ()),
                }
                for label in labels:
                    if label not in occupied:
                        statements.append(f'skos:hiddenLabel "{quote(label)}"@{lang}')
            for lang, definition in sorted(concept.definitions.items()):
                statements.append(f'skos:definition "{quote(definition)}"@{lang}')
            for target in concept.broader:
                statements.append(f"skos:broader <{base_uri}concept/{target}>")
            for target in concept.related:
                statements.append(f"skos:related <{base_uri}concept/{target}>")
            lines.append(f"<{uri}> " + " ;\n    ".join(statements) + " .")
            lines.append("")
        return "\n".join(lines)

    def to_elasticsearch_synonyms(self) -> str:
        """Export only unambiguous aliases to stable concept tokens.

        A flat synonym analyzer has no sentence context. Aliases that point to more than one
        concept are deliberately omitted and must pass through the normalizer instead.
        """
        lines: list[str] = []
        for alias, entries in sorted(self._aliases.items()):
            concept_ids = sorted({entry.concept_id for entry in entries})
            if len(concept_ids) != 1:
                continue
            concept_id = concept_ids[0]
            token = "c__" + re.sub(r"[^a-z0-9]+", "__", concept_id.casefold()).strip("_")
            escaped_alias = alias.replace("\\", "\\\\").replace(",", "\\,")
            lines.append(f"{escaped_alias} => {token}")
        return "\n".join(lines) + "\n"
