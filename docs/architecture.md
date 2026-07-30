# Architecture

## 1. Problem definition

Lexical retrieval fails when the query and source use different surface forms for the same concept. The problem becomes larger across English and Portuguese.

A global synonym table is insufficient because:

- words are polysemous;
- translations can be exact, partial, broader, narrower, or one-to-many;
- grammatical role changes meaning;
- domain terminology overrides general-language equivalence;
- negation and modality can reverse the proposition;
- numbers, identifiers, and quoted strings require exact preservation.

The architecture therefore normalizes **references to concepts**, not arbitrary strings.

## 2. Data layers

### Layer A — immutable source

Store the original document, source offsets, source URI, version, language, and content hash. Never regenerate citations from canonical text.

### Layer B — lexical registry

Each concept contains:

- stable concept ID;
- preferred label for English;
- preferred label for Portuguese;
- alternative labels;
- hidden labels;
- morphological surface forms;
- definitions;
- part of speech;
- domain scope;
- positive and negative context terms;
- source authority;
- broader and related concepts;
- lifecycle status.

### Layer C — assertion operators

Keep proposition-changing signals outside ordinary synonym mapping:

- negative polarity;
- obligation;
- recommendation;
- permission or capability;
- `if`, `when`, `before`, and `after` conditions;
- exceptions and exclusions;
- quantities and units.

### Layer D — projections

Generate parallel fields:

| Field | Purpose |
|---|---|
| `text_raw` | exact phrase, identifier, citation, and forensic retrieval |
| `text_canonical` | conservative source-syntax projection; never force a lemma into an incompatible inflection |
| `concept_tokens` | authoritative language-neutral canonical bridge |
| `operator_tokens` | preserve high-impact logical signals |
| `text_expanded` | recall-oriented BM25 field |
| dense vector | paraphrase and semantic recall |
| metadata | authority, document type, time, product, jurisdiction, section |

## 3. Mapping algorithm

```text
1. Detect protected source spans.
2. Match the longest registered lexical forms.
3. Generate all concepts for each matched span.
4. Score preferred, alternative, surface, and hidden labels differently.
5. Score context locally for each occurrence, with distance and verb-object direction.
6. Add positive context evidence and subtract negative evidence.
7. Accept a single or clearly separated candidate.
8. Quarantine unresolved candidates.
9. Preserve an inflected source form when a safe canonical inflection is unavailable.
10. Apply accepted replacements to a parallel projection.
11. Validate semantic invariants.
```

The default candidate thresholds are configuration values, not universal constants.

## 4. Bounded resolver

A language model is useful only for the residual ambiguous set. The resolver receives:

- the complete source text;
- one ambiguous source span;
- its language and offsets;
- candidate IDs;
- definitions, domains, parts of speech, and preferred labels.

The resolver can return one supplied ID or `null`. The runtime rejects invented IDs, low-confidence decisions, and validation failures.

This design keeps deterministic coverage cheap and inspectable. It also creates a clean dataset of difficult cases for later classifier training.

## 5. Reversibility

The projection is reversible by reference, not by linguistic regeneration. Reversal means:

- the original source is retained;
- every mapping retains source offsets and source surface;
- every result retains source and registry hashes;
- citations always point to source spans;
- canonical fields can be deleted and rebuilt from the source plus registry version.

## 6. Failure states

| State | Meaning | Index policy |
|---|---|---|
| `accepted` | all invariants passed; no unresolved mapping | index all projection fields |
| `review` | semantic ambiguity or questionable resolver behavior | index raw; quarantine or down-weight canonical fields |
| `rejected` | protected content or proposition signals changed | index raw only; open defect |

## 7. Why the source field remains necessary

Canonicalization can improve recall but can also erase distinctions. Exact source remains necessary for:

- legal language;
- safety requirements;
- product and account identifiers;
- source-code symbols;
- quotations;
- document-specific defined terms;
- dates, values, and units;
- debugging a mapping regression.

## 8. Extension points

- spaCy or Stanza morphology and dependency parsing;
- multilingual entity linking;
- SKOS-XL or OntoLex-Lemon lexical forms;
- TBX terminology exchange;
- OpenRefine reconciliation endpoint;
- learned candidate retriever and cross-encoder reranker;
- LangGraph only when persistent human review and distributed workflow become necessary;
- DSPy optimization only after a labeled mapping dataset and scoring metric exist.
