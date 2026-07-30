from __future__ import annotations

from typing import Any

from ..models import ResolutionDecision, ResolutionRequest


class InstructorResolver:
    """Optional LLM resolver using Instructor and a provider-specific structured output.

    Install the optional dependencies with `pip install -e '.[agent]'`. The model sees only the
    source span, local text, definitions, and an allow-list of concept IDs.
    """

    def __init__(self, model: str, *, max_retries: int = 1) -> None:
        try:
            import instructor
            from pydantic import BaseModel, Field
        except ImportError as exc:  # pragma: no cover - optional path
            raise RuntimeError(
                "Install optional agent dependencies: pip install -e '.[agent]'"
            ) from exc

        class ResolverOutput(BaseModel):
            concept_id: str | None = Field(
                description="One supplied concept ID, or null when the context is insufficient"
            )
            confidence: float = Field(ge=0.0, le=1.0)
            rationale: str = Field(min_length=1, max_length=500)

        self._output_model: type[Any] = ResolverOutput
        self._client = instructor.from_provider(model)
        self._max_retries = max_retries

    def resolve(self, request: ResolutionRequest) -> ResolutionDecision:
        candidate_rows = []
        for concept in request.candidates:
            candidate_rows.append(
                {
                    "concept_id": concept.concept_id,
                    "part_of_speech": concept.part_of_speech,
                    "domains": list(concept.domains),
                    "definitions": concept.definitions,
                    "preferred_labels": concept.preferred_labels,
                }
            )
        prompt = {
            "task": "Disambiguate one source span without rewriting the source text.",
            "rules": [
                "Select only one supplied concept_id or return null.",
                "Do not invent a concept.",
                "Use the whole sentence, definition, domain, part of speech, actor, action, and object.",
                "Return null when the evidence does not separate the candidates.",
            ],
            "source_language": request.source_language,
            "surface": request.surface,
            "span": [request.span_start, request.span_end],
            "text": request.text,
            "candidates": candidate_rows,
        }
        output = self._client.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a constrained terminology resolver. You must abstain instead of "
                        "guessing and may select only a candidate concept ID supplied by the user."
                    ),
                },
                {"role": "user", "content": str(prompt)},
            ],
            response_model=self._output_model,
            max_retries=self._max_retries,
        )
        return ResolutionDecision(
            concept_id=output.concept_id,
            confidence=float(output.confidence),
            rationale=str(output.rationale),
        )
