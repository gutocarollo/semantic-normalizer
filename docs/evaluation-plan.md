# Evaluation Plan

## 1. Separate three claims

Test these claims independently:

1. **Mapping quality** — the normalizer assigns the correct concept without losing source meaning.
2. **Retrieval quality** — canonical fields improve relevant-source retrieval.
3. **Answer quality** — the generation layer uses retrieved evidence faithfully.

A gain in one layer does not prove a gain in the next.

## 2. Build a real gold set

Sample production queries and documents across:

- English query to English source;
- Portuguese query to Portuguese source;
- English query to Portuguese source;
- Portuguese query to English source;
- exact identifiers;
- abbreviations;
- synonyms;
- paraphrases;
- polysemous terms;
- negation;
- requirements and permissions;
- conditions and exceptions;
- numbers and units;
- code and quoted strings;
- document-specific defined terms;
- rare domain terminology.

For each query, label all relevant source chunks when feasible. Keep development and held-out test sets separate.

## 3. Mapping metrics

Measure:

- exact concept accuracy;
- precision, recall, and F1 by concept;
- top-k candidate recall;
- abstention rate;
- selective accuracy at each confidence threshold;
- false canonicalization rate;
- protected-span preservation rate;
- negation and modality preservation rate;
- mapping accuracy by language and domain;
- agent override accuracy;
- human-review agreement.

False canonicalization is more dangerous than abstention. Optimize confidence thresholds accordingly.

## 4. Retrieval ablations

Run all variants against the same corpus and qrels:

| Run | Document field | Query treatment |
|---|---|---|
| A | raw | raw |
| B | raw | synonym-expanded |
| C | raw + canonical | canonical |
| D | raw + concept tokens | concept-expanded |
| E | canonical only | canonical |
| F | dense only | multilingual embedding |
| G | BM25 + dense | reciprocal-rank fusion |
| H | learned sparse | model tokenizer |
| I | best first stage | cross-encoder reranker |

Run E as a deliberate risk control. It quantifies what is lost when raw vocabulary is removed.

Measure:

- Recall@1, @3, @5, @10, and @50;
- MRR;
- nDCG@10;
- MAP where relevance is graded or exhaustive;
- zero-result rate;
- latency and index growth;
- query-class breakdown;
- worst-case regressions.

## 5. Answer-grounding evaluation

For each retrieval run, execute the same generation prompt and model. Measure:

- answer correctness;
- citation correctness;
- citation completeness;
- unsupported-claim count;
- source-entailment rate;
- abstention when evidence is insufficient;
- contradictions with retrieved source;
- sensitivity to irrelevant retrieved chunks.

Do not use only an LLM judge. Include deterministic citation-span checks and sampled human review.

## 6. Promotion criteria

A registry or normalizer release should require:

- no regression on protected values;
- no regression on negation, modality, or quantities;
- mapping precision above the domain-defined minimum;
- acceptable unresolved rate;
- statistically credible retrieval improvement on held-out queries;
- no unacceptable regression in exact-match query classes;
- reviewed examples for all newly ambiguous aliases;
- reproducible source and registry versions.

## 7. Continuous error taxonomy

Assign every failure to one category:

- missing concept;
- missing lexical form;
- incorrect preferred label;
- wrong concept granularity;
- polysemy not resolved;
- morphology missed;
- phrase boundary error;
- protected-span error;
- operator loss;
- chunking failure;
- BM25 field-weight failure;
- dense retrieval failure;
- reranking failure;
- generation unsupported claim.

Fix the responsible layer instead of adding broad prompt instructions.
