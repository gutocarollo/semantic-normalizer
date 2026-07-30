# Constrained concept disambiguation

Use this prompt only after deterministic matching produces two or more candidate concepts for one source span.

## Input contract

```json
{
  "source_text": "complete source sentence or small paragraph",
  "source_language": "en | pt",
  "surface": "exact matched source text",
  "span": [0, 6],
  "candidates": [
    {
      "concept_id": "action.remove_physical",
      "definition": {"en": "...", "pt": "..."},
      "part_of_speech": "verb",
      "domains": ["technical documentation"],
      "preferred_labels": {"en": "remove", "pt": "remover"}
    }
  ]
}
```

## Decision rules

1. Select only a supplied `concept_id`.
2. Return `null` when context does not separate the candidates.
3. Preserve the source actor, action, object, condition, result, polarity, modality, time, scope, and domain.
4. Do not choose a broader concept only because it has higher lexical overlap.
5. Do not infer an unstated object, purpose, or consequence.
6. Treat numbers, identifiers, code, paths, URLs, and quotations as immutable evidence.
7. Give a concise rationale based on explicit source context.

## Output schema

```json
{
  "concept_id": "supplied ID or null",
  "confidence": 0.0,
  "rationale": "context evidence or reason for abstention"
}
```
