# Rollout Plan

## Phase 0 — Baseline

Deliverables:

- frozen raw corpus;
- stable chunk identifiers and source offsets;
- real query sample;
- raw BM25 and dense baselines;
- qrels and query-class labels.

Exit gate: retrieval metrics can be reproduced from a clean environment.

## Phase 1 — Terminology discovery

Deliverables:

- frequency and keyness reports by language;
- candidate multi-word terms;
- cross-language candidate pairs;
- synonym and abbreviation clusters;
- ambiguity inventory;
- protected-token inventory.

Exit gate: domain owners approve the first high-value concept tranche.

## Phase 2 — Deterministic canonicalizer

Deliverables:

- versioned registry;
- preferred and alternative labels;
- source-span mappings;
- protected-span handling;
- operator validators;
- raw/canonical/concept fields;
- query normalization.

Exit gate: mapping precision and preservation thresholds pass on held-out data.

## Phase 3 — Retrieval A/B

Deliverables:

- raw, canonical, expanded, dense, and hybrid runs;
- field-weight search;
- error breakdown by query class;
- index-size and latency report.

Exit gate: held-out retrieval improves without unacceptable exact-match regressions.

## Phase 4 — Bounded resolver

Deliverables:

- unresolved-span queue;
- constrained structured-output resolver;
- maximum two attempts;
- human approval interface;
- full trace and model version;
- resolver accuracy report.

Exit gate: resolver increases coverage while maintaining the required selective precision.

## Phase 5 — Answer grounding

Deliverables:

- citation-span verifier;
- unsupported-claim detector;
- source-entailment sample review;
- answer abstention policy;
- end-to-end comparison against baseline.

Exit gate: answer quality improves and unsupported claims decrease on the held-out set.

## Phase 6 — Operations

Deliverables:

- registry release workflow;
- reindex migration plan;
- rollback;
- drift dashboard;
- unresolved and false-mapping queues;
- scheduled regression suite.

Exit gate: a registry update can be deployed and rolled back without source loss.
