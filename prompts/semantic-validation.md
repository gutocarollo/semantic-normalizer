# Semantic projection audit

Compare the immutable source and the canonical projection. This prompt is a secondary audit; deterministic validators remain authoritative for protected values and operators.

## Reject when the projection changes

- actor or responsible party;
- action or object;
- polarity or negation;
- obligation, recommendation, permission, or capability;
- condition, exception, sequence, or temporal relation;
- quantity, unit, identifier, code, path, URL, or quotation;
- domain-specific scope;
- causal relation or consequence;
- certainty, approximation, or evidential status.

## Output schema

```json
{
  "verdict": "equivalent | uncertain | changed",
  "changed_dimensions": [],
  "source_evidence": [],
  "canonical_evidence": [],
  "explanation": "",
  "requires_human_review": true
}
```
