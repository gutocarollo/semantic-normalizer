# Migration crosswalk — 0.2.0 → 0.3.0

Measured on 2026-07-30 against `semantic-normalizer-skill/config/concepts.json` (21 concepts,
tracked at `3c4a26c`) and `.harness/runs/ste-bilingual-normalizer/artifact/.agents/skills/semantic-normalizer/src/semantic_normalizer/data/registry.jsonl`
(73 concepts, untracked because `.gitignore:63` ignores `.harness/runs/`).

## Why the engine moves, not only the data

Same 300 random sentences from `cga-2026-markdown/`, same seed, same length filter:

| Metric | tracked, 21 concepts | run artifact, 73 concepts |
|---|---:|---:|
| `status: accepted` | 300 / 300 (100 %) | 13 / 300 (4.3 %) |
| `status: review` | 0 | 205 / 300 (68.3 %) |
| `status: partial` | — | 82 / 300 (27.3 %) |
| unresolved items emitted | **0** | **680** |
| sentences with 0 concepts | 179 (59.7 %) | 251 (83.7 %) |
| concepts per sentence | 0.43 | 0.19 |

The run artifact has *worse* raw coverage and is the only one that reports it. The false-silence
defect (gap G1) is fixed in `normalizer.py` (893 LOC), not in any data file — so migrating the
registry alone would load 73 concepts into an engine that still accepts everything.

The 680 unresolved items are also the input for gap G3: ranked by frequency, they are the
priority list of which concepts to author next.

## Canary sentences

| Sentence | tracked | run artifact |
|---|---|---|
| `O técnico deve desativar o servidor secundário.` | `accepted`, unresolved 0 | `review`, unresolved 3 |
| `The technician must disable the secondary server.` | `accepted`, unresolved 0 | `review`, unresolved 3 |
| `O gestor deve resgatar as cotas do fundo antes do come-cotas.` | `accepted`, unresolved 0 | `review`, unresolved 3 |

## What is promoted

Core, 3282 LOC: `__init__`, `normalizer`, `registry`, `evaluator`, `exporters`,
`schema_validation`, `reconciliation`, `cli`.

Data: `registry.jsonl`, `registry.schema.json`, `registry.provenance.jsonl`,
`registry.release.json`, `sidecar.schema.json`.

Also gained, closing gap G5: sidecar records already carry `line`, `column`, `byte_start`,
`byte_end`; the CLI already exposes `command_index`, `command_query` and `indexed_paths`.

## What is dropped

Held-out and downstream custody, 3462 LOC — **51.3 % of the runtime**:

| Module | LOC | Imported by |
|---|---:|---|
| `heldout_evaluation_v2.py` | 1773 | nobody |
| `downstream_evaluation_v2.py` | 946 | `heldout_evaluation_v2` |
| `phrase_evaluator_v2.py` | 429 | `heldout_contract_v2`, `heldout_evaluation_v2` |
| `heldout_contract_v2.py` | 314 | `heldout_evaluation_v2` |

Verified closed leaf subgraph: `normalizer`, `registry`, `exporters`, `schema_validation`
import none of them, and neither `cli.py` nor `__init__.py` re-export them. Removing all four
breaks no import. This machinery served four held-out corpora that were each retired without
producing a single retrieval metric (`RUN.md` lines 94, 105, 107, 125).

## Concept crosswalk

73 artifact concepts ∩ 21 tracked concepts = **3 IDs**.

| Shared ID | artifact (en, pt-BR) | tracked (en, pt) | Action |
|---|---|---|---|
| `action.install` | install, instalar | install, instalar | identical, no action |
| `action.verify` | verify, verificar | verify, verificar | identical, no action |
| `action.stop` | stop, **interromper** | stop, **parar** | **conflict — decide the pt preferred label; the loser becomes an alternative** |

Six tracked IDs are the same concept renamed, detected by label collision — map, do not duplicate:

| tracked ID | artifact ID | colliding labels |
|---|---|---|
| `role.operator` | `actor.operator` | operator, operador |
| `artifact.file` | `entity.file` | file, arquivo |
| `state.failure` | `risk.failure` | failure, falha |
| `action.delete_data` | `action.delete` | delete, excluir |
| `action.remove_physical` | `action.remove` | remove, remover |

The artifact keeps the same physical-removal vs data-deletion split the tracked version had; it
only renamed the IDs. Nothing is lost by mapping.

Remaining 13 tracked IDs have no artifact counterpart and are imported as new:
`action.configure`, `action.restart`, `action.start`, `artifact.configuration`,
`artifact.error_code`, `artifact.record`, `component.panel`, `fastener.screw`,
`state.disabled`, `state.enabled`, `system.database`, `system.server`, `system.service`.

**Resulting registry: 73 + 13 = 86 concepts, plus 5 crosswalk renames.** Not 91 — the earlier
73 + 18 estimate double-counted the renames. Corrected twice: this document first said
12 new / 5 renames / 85 total while listing 13 ids. The importer is the authority — run
`scripts/migrate_v02_concepts.py --dry-run` for the counts, and `validate-registry` for the
result. Measured: 13 added, 5 renamed, 3 label-merged, 86 total.

## Language key

Artifact uses `pt-BR`; the tracked version uses `pt`. `pt-BR` wins (it is the more specific tag
and matches the recorded decision in `RUN.md`). Every imported `pt` label is re-keyed.

## Migration order

Sequential, one step per commit, tests green before the next (project rule §0.6):

1. This crosswalk. *(done)*
2. Promote core + data, keep the tracked `CHANGELOG.md`, `LICENSE`, `README.md`. Run the
   suite. *(done — 4 custody modules and their 4 tests dropped, old engine removed)*
3. Re-key `pt` → `pt-BR` and import the new concepts. *(done — 86 concepts, `validate-registry`
   green, `registry.release.json` and `registry.provenance.jsonl` rewritten by the importer)*
4. Apply the renames as crosswalk records, resolve the `action.stop` conflict. *(done —
   `interromper` preferred, `parar` demoted to alternative; renames live in the provenance
   ledger, not in `relations`, because a retired id is not a concept)*
5. `test_rg_executes_fixed_string_sidecar_gate`. *(done — the code was right: `rg` is a shell
   function here, not a binary, so `shutil.which` correctly returns `None` and the gate
   degrades to `not_run` by design. The test asserted an environment precondition; it now
   skips when the binary is absent.)*
6. MANIFEST self-verification. *(done — `scripts/cut_manifest.py` re-cuts it from the tree and
   `tests/test_manifest_integrity.py` fails until it is re-cut. Closes gap G8, which shipped
   broken in both 0.1.0 and 0.2.0.)*
7. Sync `SKILL.md`/`README.md` with the real CLI, remove the withdrawn redistribution clause,
   restore packaging coverage. *(done)*
8. Tag `0.3.0` and update `CHANGELOG.md` with the measured before/after. *(pending)*

## Measured result

| | 0.2.0 | 0.3.0 |
|---|---:|---:|
| concepts | 21 | 86 |
| lexical forms | — | 354 |
| tests | 19 | 114 passed, 2 skipped |
| `accepted` on 300 CGA sentences | 300 (100 %) | 13 (4.3 %) |
| unresolved items emitted | 0 | 934 |

The two skips are declared, not silent: `rg` is not a binary in this environment, and the
auto-match adjudication snapshot is hash-bound to registry 2.0.0, so re-running its generator
against 2.1.0 would fabricate an adjudication that never happened.
