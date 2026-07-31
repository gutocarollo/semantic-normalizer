# Changelog

All notable changes to `semantic-normalizer-skill`.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

Each release also carries a **score against the anchored objective**, not against its own
release notes. The anchored objective is: *a controlled EN/PT lexicon that makes the ontology
as deterministic as possible* (`.harness/requests/CURRENT-TASK.md`). A release can be
internally consistent and still score low, because the score measures distance to that target.

Every claim below was re-executed on 2026-07-30. The 0.1.0 and 0.2.0 sections were measured
at `HEAD = 3c4a26c`; the 0.3.0 section against the working tree. Commands and raw results are
in [Verification log](#verification-log), and the machine-checkable manifest is
`.harness/evidence/semantic-normalizer-v030-20260730.json`.

---

## [0.4.0] — 2026-07-31

**The registry finally speaks the domain.** 86 → 257 concepts; 171 of them CGA/ANBIMA
vocabulary, which every previous release listed as its largest open gap.

Executed from `docs/plan-cga-domain-lexicon-0.4.0.md` after three rounds of planning review
and one round of execution review.

### Measured on the frozen corpus, 300 sentences, seed 7

| | 0.3.0 | 0.4.0 |
|---|---:|---:|
| concepts | 86 | **257** |
| sentences resolving ≥1 concept | 64 / 300 (21.3 %) | **279 / 300 (93 %)** |
| concepts per sentence | 0.24 | **3.10** |
| `accepted` | 12 | 55 |
| `partial` | 54 | **6** |

### The acceptance gate, and where it failed

| # | Metric | Result |
|---|---|---|
| 1 | OOV occurrences resolved vs the **frozen** baseline | 7397 / 18877 = **39.19 %** against a 41.51 % target — **FAIL** |
| 2 | `auto_match_precision`, blind adjudication | batch 1 **0.983**, 2 **1.00**, 3 **0.95**, 4 **0.967** — PASS |
| 3 | Wrong concept accepted in the sample | 0 after correction — PASS |
| 4 | Concepts with an EN label lacking evidence | 0 — PASS |
| 5 | `validate-registry` | valid — PASS |

Line 1 misses by 2.4 points and is reported as a miss. Getting that number honest took three
attempts, and the two discarded ones are worth naming because each looked fine:

1. Measuring against the **live** queue. Meaningless: the queue shrinks every time a concept
   is added, so it compares a moving population to itself.
2. Measuring against the queue frozen before the corpus cleanup. Inflated: it credited the
   extended stopword list and the removed image paths as if they were concepts.
3. **What ships:** `reports/baseline-queue-86-concepts.json`, built by the same pipeline, on
   the same corpus, with the same cleanup and the same sentence rule, against a registry
   holding only the 86 pre-CGA concepts. Cleanup appears on both sides and nets out, so
   39.19 % is concept contribution alone.

### Added — pipeline

- `scripts/build_oov_queue.py` — freezes the corpus by SHA-256 and emits the queue in three
  strata (`unknown` / `ambiguous` / `function`). Its own gate rejected two of this author's
  implementations before passing: a `($$)` table header paired with a distant `$$` and
  swallowed whole tables, destroying 25 and then 17 real `R$` values. Currency is asserted
  256 → 256 and TeX 438 → 0 on every run.
- `scripts/measure_domain_coverage.py` — written and run **before** any concept existed, so
  the target could not be tuned to a result. Emits the per-N curve the abort trigger needs.
- `scripts/propose_from_wordnet.py` — every OpenWordnet-PT sense with its ILI, never
  `synsets[0]`. For finance that first sense is systematically wrong: `carteira` → `bag`,
  `fundo` → `deep` (an adjective), `retorno` → `homecoming`, `risco` → `danger`.
- `scripts/build_adjudication_fixture.py` — the missing candidate source. The existing scorer
  produced **0** candidates for CGA text and returned the string `"not_run"` instead of a
  precision; it now produces 540.
- `scripts/import_cga_batch.py` — governed batch import with an append-only provenance event
  and automatic demotion of any surface that would resolve to two concepts.

### Changed

- `generate_auto_match_candidates.py`: `case_range` and `snapshot` are parameters. The `gNN`
  cap of 40 was hardcoded, silently dropped `g41`, and made `g100` collide with `g10` because
  the slice read two characters. Defaults preserve the old behaviour exactly.
- `authority` now carries the verification state in one of three closed forms
  (`apostila-cga-2026#…`, `openwordnet-pt:1.0.0 ILI=…`, `project-authored:…`), enforced by a
  test. The schema is `additionalProperties: false`, so a dedicated field was not available.

### Fixed — false positives found by adjudication, not by intention

Nine over-broad labels were removed or demoted after adjudication scored them wrong on real
corpus sentences: `crédito` → `risk.credit`; `valores` → `quantity.value` (matched *Bolsa de
Valores*); `funds` → `entity.resource` (matched *Hedge Funds*); `exposição`/`exposure` →
`risk.financial` (exposure is a quantity); `classes` → `entity.class` (matched asset-class
context); `proteção` → `technical.hedge` (matched regulatory protection); `Banco Central` →
`technical.bacen` (matched the *European* Central Bank); `ajustes` → `artifact.configuration`
(matched daily futures margin).

**One was fixed by modelling instead of suppressing.** `IR` resolved to
`technical.information_ratio` in tax context — 3 of the 7 isolated `IR` occurrences in the
corpus mean *Imposto de Renda* (`DARF`, *IR Come-Cotas*, *tributação do IR*). The collision
demoter could not help, because only one side of the collision existed. Adding
`entity.income_tax` — which also claims `IR` — let the demoter make both ambiguous, which is
what the corpus actually is. `DARF`, `IOF`, withholding and capital gain came with it.

### Known gaps

- **Line 1 of the gate fails at 39.19 % against 41.51 %.** The baseline nets out cleanup, so
  this is concept contribution with nothing flattering left in it. For scale: the 166 CGA
  concepts match ~4500 occurrences across the corpus; the 86 pre-existing ones match 281, being
  procedural-writing vocabulary with almost no overlap with finance.
- Batch 4 was authored after the review showed the reason for skipping it was wrong.
  `exposição`, `valores`, `long` and `proteção` are domain terms, and three of them had been
  *removed* from other concepts during adjudication precisely because they were attached to
  the wrong one — an argument for giving them their own concept, not for dropping them. What
  stayed out is genuinely general: `relação`, `longo`, `ano`, `total`, `alta`, `nome`, `final`.
- A markdown heading is now a hard sentence boundary. Splitting only on `.!?` glued headings
  into 2.5 % of sentences, because a line ending in a colon has no terminator. Fixing it
  changed how much text the corpus yields (1298 → 1682 sentences), which is why the baseline
  had to be rebuilt with the same rule on both sides.
- Still no qrels and no held-out queries. Gap G4 remains open: coverage on the OOV queue is
  not evidence of better retrieval.
- The registry covers one apostila. Nothing here transfers to other Brazilian financial text
  without re-measurement.

---

## [0.3.0] — 2026-07-30

**Score against the anchored objective: 6.2 / 10** (see [Scoring](#scoring)).
Capability delta over 0.2.0: the engine stops lying about what it understood.

The 0.2.0 review found a second, more advanced implementation of this skill sitting in
`.harness/runs/ste-bilingual-normalizer/artifact/`, invisible to git because `.gitignore:63`
ignores `.harness/runs/`. Measurement decided which one survives, and it did not decide the
way the plan assumed: the highest-weight defect lived in the *engine*, not in the data, so
the engine moved too. See `docs/migration-crosswalk-0.3.0.md`.

### Fixed — the defect this release exists for

**False silence on out-of-vocabulary terms.** 0.2.0 returned `status: accepted` with an empty
`unresolved_terms` for any sentence it did not understand. Measured on the same 300 random
sentences from `cga-2026-markdown/`, same seed, same filter:

| | 0.2.0 | 0.3.0 |
|---|---:|---:|
| `accepted` | 300 / 300 (100 %) | 13 / 300 (4.3 %) |
| `review` | 0 | 231 / 300 (77 %) |
| `partial` | — | 56 / 300 (18.7 %) |
| unresolved items emitted | **0** | **934** |
| concepts per sentence | 0.43 | 0.26 |

Raw coverage went **down**. That is the point: 0.43 concepts per sentence with 100 % accepted
is a false claim, and 0.26 with 77 % flagged for review is a work queue. The 934 unresolved
items, ranked by frequency, are the input for choosing which concepts to author next — the
thing gap G3 needs and 0.2.0 could not produce.

### Changed — runtime

- Promoted an 8-module core (3282 LOC): `normalizer`, `registry`, `evaluator`, `exporters`,
  `schema_validation`, `reconciliation`, `cli`, `__init__`. The old engine (`bm25`,
  `evaluation`, `loop`, `models`, `operators`, `protect`, `text_utils`, `validators`,
  `adapters/`) and its 2 tests are gone.
- Dropped 3462 LOC of held-out and downstream custody — 51.3 % of the runtime it came with.
  Verified as a closed leaf subgraph: nothing in the core imports it. It served four held-out
  corpora that were each retired without producing a single retrieval metric.
- Sidecar records now carry `line`, `column`, `byte_start`, `byte_end`, and the CLI gained
  `index`, `query`, `reconcile-request`, `reconcile-apply` and `init-workspace` — corpus
  ingestion, which gap G5 asked for.
- Restored `python -m semantic_normalizer`: the promoted artifact had no `__main__.py`.

### Changed — registry: 21 → 86 concepts, 354 lexical forms

Imported by `scripts/migrate_v02_concepts.py`, idempotent and reproducible:

- 13 concepts new, 5 ids renamed and merged as crosswalk records, 3 label-merged.
- The intersection between the two registries was **3 ids**, not 21 — five of the apparent
  orphans were the same concept renamed, detected by label collision, not by name.
- One conflict resolved: `action.stop` takes `interromper` in pt-BR, with `parar` demoted to
  an alternative.
- 11 labels refused, each naming the label it collides with. Refusing them is a modelling
  decision, not a loss: importing `check` as an alternative of `action.verify` would erase the
  distinction `action.check` (inspect without proving) draws on purpose.
- Language key is `pt-BR`, not `pt`.
- Every surface that resolves to more than one concept carries `policy: review` and is
  excluded from flat synonym export.

### Added — governance that is enforced, not described

0.2.0 promised provenance fields in prose and enforced none of them.

- `registry.provenance.jsonl` is an append-only ledger. The importer **refuses** a second run
  for the same version, and refuses it *before* writing anything.
- `registry.release.json` binds a registry version to the hashes it governs, and is written by
  the same command that writes the registry, so the two cannot drift.
- `tests/test_registry_governance.py` — 11 tests, including one that fails if an event id is
  ever rewritten in place.
- `tests/test_manifest_integrity.py` + `scripts/cut_manifest.py` — closes gap G8. Both 0.1.0
  and 0.2.0 shipped a `MANIFEST.json` declaring a hash for `reports/validation-summary.md`
  that never matched the file. Nothing recomputed it; now something does.
- `tests/test_documented_commands.py` — extracts commands from the ```bash blocks of
  `SKILL.md` and `README.md` and executes them, checks the `Makefile`, and runs the documented
  `reconcile-request` example asserting it still has more than one candidate.
- `scripts/build_release.py` produces a byte-identical wheel and skill ZIP across runs, and
  its installed-wheel smoke asserts the invariant this release exists for: unknown vocabulary
  must not come back `accepted`.

### Changed — documentation

Every command in `SKILL.md` and `README.md` now runs. In 0.2.0 none of them did: `--lang`,
`--pretty`, `--target-lang`, `export-skos`, `export-synonyms` and `evaluate --documents` were
all fictional. The `README` example projection showed `role.operator` and `status: accepted`,
neither of which the engine produced.

The redistribution prohibition is removed. ASD-STE100 is used as a **selection signal** —
which English terms are essential — and as a record template. It is English-only, so every
Portuguese label is authored here regardless; no third-party dictionary text is embedded.

### Removed

`examples/` (the 9×9 fixture whose queries shared no content token with their targets, so its
raw-BM25 baseline of 0.000 was construction rather than measurement), `config/concepts.json`,
`schemas/concept-registry.schema.json`, `scripts/run_smoke_test.sh`.

### Known gaps

- **The registry has no CGA/ANBIMA vocabulary.** 86 general operations concepts. 234 of 300
  real sentences from the project's own corpus still resolve zero concepts. This release makes
  that visible and queueable; it does not fix it. Gap G3 remains open.
- Two tests skip, both declared: `rg` is a shell function in this environment rather than a
  binary, so the fixed-string gate degrades to `not_run` by design; and the auto-match
  adjudication snapshot is hash-bound to registry 2.0.0 — re-running its generator against
  2.1.0 would fabricate a blind adjudication that never happened.
- `action.verify.pt-BR:cheque` was dropped rather than attached: the c→qu spelling change in
  Portuguese is invisible to a prefix comparator. Refused for safety, not misattributed.
- No production retrieval claim. There is still no real corpus with held-out relevance
  judgements.

---

## [0.2.0] — 2026-07-30 — `3c4a26c`

**Score against the anchored objective: 2.9 / 10** (see [Scoring](#scoring)).
Capability delta over 0.1.0: **zero**. Documentation-only release.

### Added

- `docs/data-governance.md` (28 lines): declares that the concept registry and the controlled
  lexicon are two artifacts with two different completeness claims, and that every imported
  lexical batch must record source, version, license, retrieval date, SHA-256, import command
  and approval state.
- `SKILL.md` section *Controlled lexicon and source policy* (20 lines): same policy, plus the
  rule that staged records cannot emit automatic mappings.

### Changed

- Version string in `SKILL.md`, `MANIFEST.json`, `pyproject.toml`, `src/semantic_normalizer/__init__.py`.
- `README.md`: one sentence describing the registry/lexicon split.
- `MANIFEST.json`: refreshed hashes for the four files above; registered the new doc.

### Not changed — verified

- **No executable code changed.** The only diff under `src/` is `__version__ = "0.1.0"` →
  `"0.2.0"`. Total commit: 6 files, +64 −11.
- **No lexicon shipped.** The release is titled *governed lexicon foundation*, but it contains
  no lexicon file, no lexical-form schema, no importer, no validator and no test. The governed
  fields (`source`, `version`, `license`, `retrieved_at`, `sha256`) exist only as prose;
  `schemas/concept-registry.schema.json` does not require any of them.
- **Concept count unchanged: 21.**
- **Test count unchanged: 19.**

### Known defects carried into this release

1. **False silence on out-of-vocabulary terms.** A sentence whose main verb is absent from the
   registry returns `status: accepted` with `unresolved_terms: []`. This contradicts SKILL.md
   invariant 5 (*abstain*), workflow step 6 (`unresolved_terms`: spans that require review) and
   the acceptance gate (*explicit unresolved-rate threshold*). Measured on 300 real sentences:
   **0 unresolved terms, 300/300 accepted, 179/300 with zero concepts**.
2. **Stale `MANIFEST.json` hash.** `reports/validation-summary.md` is declared as
   `9b7f240e58…`; the committed file hashes to `9e327ac70d…`. The mismatch was already present
   in 0.1.0 and survived the 0.2.0 hash refresh. The artifact-integrity mechanism fails on its
   own file set.
3. **Reinstated a constraint the requester explicitly withdrew.** The user instruction
   preceding this work was *"desconsidere completamente isso 'Ponto jurídico bloqueante para
   redistribuição'"*. `SKILL.md` (*"do not embed or redistribute a protected vocabulary"*) and
   `docs/data-governance.md` (*"do not redistribute its dictionary without explicit
   authorization"*) restate it as governing policy. The provenance metadata is sound
   engineering and should stay; the redistribution prohibition needs the requester's decision,
   not a silent reinstatement.

---

## [0.1.0] — 2026-07-30 — `cc18ee1`

**Score against the anchored objective: 3.0 / 10.**
First versioned snapshot of the working prototype. 45 files, +8063 lines.

### Added

- **Runtime** (`src/semantic_normalizer/`): `normalizer` (mention → sense → concept),
  `registry` (load, validate, SKOS/synonym export), `protect` (code, identifiers, paths, URLs,
  quoted strings), `operators` (negation, modality, condition, exception), `text_utils`,
  `validators` (invariant checks), `bm25`, `evaluation`, `loop` (bounded ambiguity loop),
  `cli` (`normalize`, `audit`, `validate-registry`, `export-skos`, `export-synonyms`,
  `evaluate`), `adapters/instructor_resolver`.
- **Registry**: 21 concepts, EN/PT, with preferred / alternative / hidden labels, surface
  forms, definitions, part of speech, domains, positive and negative context terms, source
  authority and relations.
- **Schemas**: `concept-registry.schema.json`, `normalization-result.schema.json`.
- **Docs**: architecture, evaluation plan, governance, integration, references, rollout plan.
- **Prompts**: `disambiguate`, `propose-concept`, `semantic-validation`.
- **Tests**: 19 (`tests/test_normalizer.py`, `tests/test_cli.py`) — re-run: **19 passed**.
- **Reports**: registry validation, sample output, BM25 smoke test, validation summary.

### What works — verified

- Source text is preserved byte-for-byte; the canonical form is a parallel projection.
- Protected spans survive: `APP-01` is returned unchanged and recorded as a protected value.
- Operator extraction fires on real input: `polarity__negative` on *"Não inicie o servidor."*,
  `modality__obligation` + `condition__before` on a CGA sentence with zero concept hits.
- Cross-language mapping works inside the registry's 21 concepts: *"Remove the screw from the
  panel."* → `action.remove_physical`, `fastener.screw`, `component.panel`.
- Ambiguity is modelled rather than flattened: `action.remove_physical` and `action.delete_data`
  are separate concepts, and the flat synonym export deliberately omits the ambiguous
  `remove`/`remover` aliases.

### Limits — verified, and correctly disclosed by the release itself

`README.md` and `reports/validation-summary.md` already state that the benchmark is synthetic
and proves the pipeline, not production effectiveness. Measurements confirm and quantify that:

- **Benchmark circularity.** 9 documents × 9 queries. 7 of the 9 queries share *no* content
  token with their relevant document; the remaining two share only `log` and `software`. The
  raw-BM25 baseline of MRR `0.000` is a property of how the fixtures were written, not a
  measurement. The reported lift (`+0.9` MRR) is a self-test of the synonym table.
- **Lexical coverage.** 86 English surface forms in the registry intersect the ASD-STE100
  approved vocabulary in **10 words** — 1.34 % of the 746 approved entries extractable from
  `ASD-STE100_ISSUE9_conciliado.md` (1.14 % of the 875 the standard declares).
- **Domain coverage.** On 300 random sentences from `cga-2026-markdown/`, the project's own
  corpus: mean **0.43** concepts per sentence, **59.7 %** of sentences with zero concepts,
  **100 %** returned `accepted`.

### Packaging defects

- `exports/` is listed in `.gitignore` (line 7), so `exports/concepts.ttl` and
  `exports/elasticsearch-synonyms.txt` exist on disk but are **not tracked**. The release
  description mentions exports; git carries 46 files and none of them are exports.
- `MANIFEST.json` records a hash for `reports/validation-summary.md` that never matched the
  committed file (see 0.2.0 defect 2).

---

## Scoring

Weights are set by distance to the anchored objective, not by engineering effort.

| Criterion | Weight | 0.1.0 | 0.2.0 | 0.3.0 | Evidence for 0.3.0 |
|---|---:|---:|---:|---:|---|
| EN/PT lexical coverage (the "complete dictionary") | 25 % | 0.5 | 0.5 | **1.5** | 86 concepts, 354 forms — 4.1× the inventory, still ~10 % of STE scale and **zero** domain vocabulary; 78 % of real sentences resolve nothing |
| Non-silence: OOV detection and abstention | 20 % | 1.0 | 1.0 | **9.0** | 934 unresolved items, 77 % flagged for review, 4.3 % accepted; enforced by test and by the installed-wheel smoke |
| Conceptual architecture (mention → sense → ID) | 15 % | 8.5 | 9.0 | **9.5** | `lexical_forms` with `auto`/`review` policy, `forbidden_variants`, relations, provenance ledger, sidecars with line and byte offsets |
| Evidence on a real corpus | 12 % | 1.0 | 1.0 | **2.0** | A reproducible 300-sentence baseline on the project's own corpus, independently reproduced by the reviewer — but still no qrels and no held-out queries |
| Executable data governance | 8 % | 3.0 | 4.0 | **9.0** | Append-only ledger that refuses a rerun before writing; release record bound to hashes; 15 governance and manifest tests |
| Engineering quality | 10 % | 6.5 | 6.0 | **8.5** | 122 passed / 2 skipped; byte-identical rebuild; `make check`; both skips declared with a named cause |
| Fidelity to the request and its amendment | 10 % | 4.0 | 2.0 | **8.0** | D1 and D2 executed as decided; the anchor gap declared rather than claimed; two of the author's own errors caught by review and converted into permanent guards |
| **Weighted total** | **100 %** | **3.0** | **2.9** | **6.2** | |

0.1.0 and 0.2.0 are a tie within the noise of these weights — 0.2.0 moved documentation, not
capability. 0.3.0 is the first release where the number moves, and it moves for one reason:
the engine stopped claiming to understand text it did not.

It is not an 8 or a 9, and the reason is the criterion with the largest weight. The anchored
objective is a *dictionary*; 86 general concepts with no domain vocabulary is a working
mechanism with an empty tank. What 0.3.0 buys is that the tank gauge now reads true.

A prior assessment scored this skill **7.0/10**. That score answered a different question —
*"how good is this as a prototype?"* — and is defensible on its own terms. Against the anchored
objective (*a complete EN/PT dictionary that makes the ontology deterministic*), the score is
~3. The gap between 7.0 and 3.0 is a change of rubric, not a change of artifact.

---

## Gaps to 10.0

Status after 0.3.0. **Closed: G1, G5, G7, G8, G9.** **Partially closed: G2, G6.**
**Open, and they are the ones that matter: G3 and G4 — 37 % of the score between them.**

| ID | Status after 0.3.0 |
|---|---|
| G1 | **Closed.** 934 unresolved items on 300 real sentences; `accepted` fell from 100 % to 4.3 %; asserted in the suite and in the installed-wheel smoke. |
| G2 | **Partially closed.** No separate `lexicon.jsonl`, but `lexical_forms` inside the registry now carries an explicit `auto`/`review` policy, and the governance fields are enforced by `registry.release.json` + the append-only ledger + 15 tests. What remains is the human-facing controlled lexicon (rejected variants, sense, worked examples per form). |
| G3 | **Open — the main gap.** 86 concepts, none of them CGA/ANBIMA. The target is ≥100 concepts of one real domain, chosen by marginal retrieval gain. The 934 unresolved items are the ranked input this needs. |
| G4 | **Open.** Still no qrels and no held-out queries. A reproducible 300-sentence coverage baseline exists, which is not the same thing as a retrieval evaluation. |
| G5 | **Closed.** `index` walks a corpus; records carry `line`, `column`, `byte_start`, `byte_end`. |
| G6 | **Partially closed.** Inflections are now attributed to the label they inflect instead of being dropped wholesale, and unverified ones carry `policy: review`. Still hand-listed, not generated: `cheque`/`checar` fails a prefix comparator because of the c→qu change. |
| G7 | **Closed.** One tracked implementation. The 73-concept engine was promoted, the custody half dropped, and the 21-concept registry merged into it. |
| G8 | **Closed.** `scripts/cut_manifest.py` + `tests/test_manifest_integrity.py`. The test caught its own file on first run, and caught a doc edited after the cut — it has teeth. |
| G9 | **Closed.** The prohibition is out of every document; the provenance fields stayed and are now enforced rather than described. |

The original table follows, unedited, as the 0.2.0 baseline.

Ordered by weighted impact. G1–G4 carry 57 % of the score.

| ID | Gap | Current | Target | Weight |
|---|---|---|---|---:|
| G1 | **False silence.** An unknown term must never be silently accepted. Emit `unresolved_terms` and `status: review`. | 0 unresolved in 300 sentences | `false_silence_rate` measured and gated in CI | 20 % |
| G2 | **No lexicon artifact.** Ship `lexicon.jsonl` + schema + importer + validator: `form`, `lang`, `pos`, `sense`, `policy` (approved/rejected/staged), `concept_id`, and the provenance fields 0.2.0 declares. | prose only | schema-enforced, test-covered | 25 % |
| G3 | **Registry scale.** Plan Fase 1 targets 100–300 concepts chosen by marginal retrieval gain. | 21 demo concepts, 0 CGA domain | ≥100 concepts of one real domain | 25 % |
| G4 | **No real evaluation.** Plan Fase 0 targets 200–500 real queries with qrels, split by identifier / synonym / abbreviation / paraphrase / ambiguity / negation / modality / number / rare term. | 9 synthetic queries | held-out A/B with confidence intervals | 12 % |
| G5 | **No batch ingestion.** The CLI takes one text, file or stdin; it cannot walk a corpus or emit sidecars with `path`, `line_start`, `line_end`. | single input | corpus walker + sidecars | 12 % |
| G6 | **PT morphology.** Surface forms are hand-listed. `state.disabled` carries `desativado`, `desativada`, `desabilitada` but not the infinitive `desativar`, and `disable` is absent in EN — so the exact morphological neighbour of a registered concept is a silent miss. | manual lists | generated or lemmatised forms, with an OOV queue | 20 % (shared with G1) |
| G7 | **Two divergent implementations.** `.harness/runs/ste-bilingual-normalizer/artifact/.agents/skills/semantic-normalizer/` holds a second normalizer with 73 concepts, 13 test files (147 collected: 140 pass, 1 fail, 6 skip), `lexical_forms.policy`, `forbidden_variants`, relations and a provenance JSONL. `.harness/runs/` is git-ignored (`.gitignore` line 63), so none of it is versioned. Reconcile into one artifact. | 21-concept version tracked, 73-concept version invisible | one tracked implementation | 10 % |
| G8 | **MANIFEST is not self-verifying.** No test recomputes the hashes it declares. | stale hash shipped twice | integrity test in CI | 10 % |
| G9 | **Withdrawn constraint reinstated.** Keep the provenance fields; remove the redistribution prohibition or have the requester reaffirm it explicitly. | reinstated silently | explicit decision recorded | 10 % |

---

## Verification log

Executed 2026-07-30 at `HEAD = 3c4a26c`, from `semantic-normalizer-skill/`.

```bash
# 19/19 tests pass
PYTHONPATH=src python -m pytest tests -q
# → 19 passed in 0.24s

# v0.2.0 touches no executable code
git diff cc18ee1 3c4a26c -- src/
# → only __version__ = "0.1.0" -> "0.2.0"

# False silence, PT — state.disabled exists in the registry
PYTHONPATH=src python -m semantic_normalizer normalize \
  --text "O técnico deve desativar o servidor secundário." --lang pt --pretty
# → status=accepted  concept_ids=['system.server']  unresolved_terms=[]

# False silence, EN — same shape
PYTHONPATH=src python -m semantic_normalizer normalize \
  --text "The technician must disable the secondary server." --pretty
# → status=accepted  concept_ids=['system.server']  unresolved_terms=[]

# Zero domain coverage, still accepted
PYTHONPATH=src python -m semantic_normalizer normalize \
  --text "O gestor deve resgatar as cotas do fundo antes do come-cotas." --lang pt --pretty
# → status=accepted  concept_ids=[]  unresolved_terms=[]
#   operator_tokens=['modality__obligation','condition__before']

# 300 random sentences from ../cga-2026-markdown/
# → 300/300 accepted | 179/300 (59.7%) zero concepts | 0.43 concepts/sentence
#   | 0 unresolved_terms

# ASD-STE100 overlap, from ../ASD-STE100_ISSUE9_conciliado.md (dictionary at line 4901)
# → 746 approved entries extracted, 1202 not-approved
# → 86 EN surface forms in the registry ∩ approved = 10 words = 1.34%
#   ['active','check','erase','failure','install','malfunction','record','remove','start','stop']

# Benchmark circularity: content-token overlap, query vs relevant document
# → q01,q02,q03,q05,q06,q07,q09 = [] ; q04 = ['log'] ; q08 = ['software']

# MANIFEST integrity
# → 47 files declared, 1 hash mismatch: reports/validation-summary.md
#   declared 9b7f240e58… / actual 9e327ac70d… (already wrong in cc18ee1)

# exports/ is git-ignored
git check-ignore -v exports/concepts.ttl
# → .gitignore:7:exports/
```

The repository has no configured remote, so releases are referenced by local commit SHA:
`0.1.0 = cc18ee1`, `0.2.0 = 3c4a26c`.
