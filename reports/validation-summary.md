# Validation Summary

**Release:** `0.1.0`
**Validation date:** 2026-07-30
**Runtime:** Python 3.13 during validation; package requires Python 3.11 or later.

## Result

The prototype passed its executable release checks.

- Unit and CLI regression tests: **19 passed**.
- Source compilation: **passed**.
- Complete smoke script: **passed**.
- JSON and JSONL parsing: **passed**.
- Concept-registry JSON Schema: **passed**.
- Normalization-result JSON Schema: **passed**.
- Registry: **21 concepts**, **0 errors**, **4 intentional ambiguity warnings**.
- Flat synonym export: ambiguous `remove/remover` variants were omitted as designed.
- Wheel build: **passed**.
- Clean virtual-environment installation and installed CLI execution: **passed**.
- Editable and packaged registry copies have the same SHA-256.

## Synthetic BM25 smoke benchmark

| Projection | MRR | HitRate@1 | HitRate@3 |
|---|---:|---:|---:|
| Raw | 0.000 | 0.000 | 0.000 |
| Canonical English lexical pivot | 0.778 | 0.556 | 1.000 |
| Expanded concept projection | 0.944 | 0.889 | 1.000 |

The benchmark contains nine documents and nine cross-language queries designed to exercise the registry. It validates the pipeline, not production effectiveness. It does not establish hallucination reduction.

## Defects found and corrected during validation

1. Renamed the generated `build/` artifact directory to `exports/` so it cannot shadow Python's build tooling.
2. Restricted context scoring to each occurrence and sentence, preventing one mention from borrowing another mention's object.
3. Added distance and verb-object direction to disambiguate repeated terms inside one sentence.
4. Preserved source-language inflection when a preferred lemma would damage grammar.
5. Kept preferred labels in mapping metadata while using concept tokens as canonical identity.
6. Excluded ambiguous aliases from flat Elasticsearch synonym rules.
7. Added surface forms to the SKOS export as hidden labels.
8. Confirmed that the installed wheel finds its packaged registry outside the source tree.

## Artifact integrity

- Wheel: `semantic_normalizer-0.1.0-py3-none-any.whl`
- Wheel SHA-256: `0e9863b6a4942d889127ea2ff6efc15bcc0a962d224c72fecbede6290e13ada6`
- Registry SHA-256: `e7b0373168740ee1caf6f71540ae81505fb77b4d8a64c6f88458745b8cf516e1`

## Remaining production gates

A production claim still requires a real domain corpus, held-out query relevance judgments, mapping labels, exact-match regression tests, and end-to-end grounded-answer evaluation.
