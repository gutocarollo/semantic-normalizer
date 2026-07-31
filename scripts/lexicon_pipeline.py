#!/usr/bin/env python3
"""One entrypoint that runs the whole dictionary-building loop — the construction counterpart
of `normalize`, shaped after gutocarollo/ui-tokenizer's `tokenize.mjs`.

    python scripts/lexicon_pipeline.py run --domain reasoning \
        --corpus <dir-with-md> --reference <dir-with-md> --run-dir runs/reasoning-01

`normalize` APPLIES a dictionary and is instant. This pipeline BUILDS one, and it is a loop
with AI in the middle. The phases, with their executor declared (the ui-tokenizer bridge —
"fase → executor"; here: 9 deterministic, 2 model, 1 human-optional):

    PREFLIGHT   [det]    corpus/reference resolve, registry loads, or the loop stops
    MATCH       [det]    normalize every corpus file with the CURRENT dictionary
    PARTITION   [det]    per sentence: covered / decided / pending — covered costs NO AI
    RANK        [det]    ATE ranking (G2 keyness + weirdness floor + C-value) of the unknown
    ADJUDICATE  [model]  is each candidate a domain concept? propose the full entry
    REFUTE      [model]  adversarial reviewer, default-refute; a proposal must survive it
    VALIDATE    [det]    anti-hallucination gates: citations byte-verified, enums, collisions
    APPLY       [det]    batch -> config/, import, version bump, FULL TEST SUITE — red reverts
    MEASURE     [det]    coverage and decision accounting per round
    CONVERGE    [det]    loop to MATCH until the candidate pool drains (or 2 dry rounds)
    PRECISION   [model x2, det resolve]  seeded sample of the new pack's matches, two
                         independent readers (one adversarial), Wilson lower bound
    REPORT      [det]    3 chapters: admitted / rejected-with-reason / needs-owner

The dependency graph the driver exploits: tasks inside one wave are INDEPENDENT (run them in
parallel); ADJUDICATE -> REFUTE is an edge (refuters read proposals); rounds are sequential
because the registry that round N+1 matches with is the one round N grew. Sentences already
covered by the dictionary never reach a model — that is the reuse the loop exists for, and it
compounds: `core` + previously built packs short-circuit every next corpus.

THE AI IS A PROPOSER, NEVER A WRITER. The boundary is a file protocol:

    <run-dir>/rounds/NN/tasks/pending/<task>.json   what a model must decide (self-contained)
    <run-dir>/rounds/NN/tasks/results/<task>.json   what the model decided

The pipeline stops with exit code 3 when tasks are pending; ANY driver (Claude/Codex session
spawning subagents, a human, later an API adapter) services them and re-invokes the pipeline.
Everything a model returns passes VALIDATE before touching state: the positive example must be
a VERBATIM substring of the corpus, the surface must be attested in prose sentences, enums come
from the registry schema, ids collide-checked. A hallucinated quote does not enter — it bounces
with the validation error attached, once; the second failure goes to the needs-owner chapter.

Determinism: state lives on disk and every deterministic phase is a pure function of (corpus
bytes, registry bytes, stored AI results, seed). Model outputs are STORED, so replaying
VALIDATE -> APPLY from a run directory is bit-reproducible — the same property the manual
campaign got from batch files. Sampling is seeded. Iteration is sorted. Task ids are content
hashes. Fail-closed everywhere: a red test suite REVERTS the apply and stops the run rather
than continuing with a broken dictionary.

Exit codes: 0 = complete (or advanced to a stable stop), 3 = AI tasks pending (service them,
re-run), 2 = fail-closed stop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA = SRC / "semantic_normalizer" / "data"
REGISTRY_JSONL = DATA / "registry.jsonl"
REGISTRY_PY = SRC / "semantic_normalizer" / "registry.py"
# Everything APPLY can touch, so a red suite can restore the tree exactly. It is not just the
# registry: the generated artifacts (MANIFEST.json, release-manifest.json, checksums.sha256)
# hash the registry, so an import without a re-cut leaves the delivery gate red for a reason
# that has nothing to do with the batch. Restoring the registry and leaving the manifests
# rewritten would be a half-revert — worse than no revert, because the tree would look sealed.
SNAPSHOT_FILES = [
    REGISTRY_JSONL,
    DATA / "registry.release.json",
    DATA / "registry.provenance.jsonl",
    REGISTRY_PY,
    ROOT / "MANIFEST.json",
    ROOT / "reports" / "release-manifest.json",
    ROOT / "reports" / "dev-semantic-gates.json",
    ROOT / "checksums.sha256",
]

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SRC))
import build_oov_queue as oov  # noqa: E402  (stopwords, WORD, IMAGE — the maintained lists)
from semantic_normalizer.normalizer import FUNCTION_WORDS  # noqa: E402

WORD = oov.WORD
IMAGE = oov.IMAGE
HEADING = re.compile(r"^\s{0,3}#")
CONCEPT_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
STOPWORDS = frozenset(
    {w.casefold() for w in oov.NLTK_PT}
    | {w.casefold() for w in oov.LOCAL_PT}
    | {w.casefold() for words in FUNCTION_WORDS.values() for w in words}
)

REJECTION_REASONS = (
    "common-language", "corpus-artifact", "fragment", "heading-only",
    "already-covered", "ambiguous", "other",
)


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def stable_id(prefix: str, payload) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(blob).hexdigest()[:8]}"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def schema_enums() -> dict:
    schema = read_json(DATA / "registry.schema.json")
    props = schema["properties"]
    return {
        "semantic_class": props["semantic_class"]["enum"],
        "pos": props["pos"]["enum"],
    }


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (centre - margin) / denom


# --------------------------------------------------------------------------------------
# Deterministic phases
# --------------------------------------------------------------------------------------

def _env() -> dict:
    """Real environment plus PYTHONPATH, for subprocesses whose FAILURE is a verdict.

    A stripped `{"PYTHONPATH": ..., "PATH": "/usr/bin:/bin"}` looked tidy and made the delivery
    suite fail for reasons that had nothing to do with the batch under test — a build step could
    not find its toolchain, and the guard read that as "this batch broke the dictionary" and
    reverted a healthy one. A guard that fires on its own environment teaches you to ignore it.
    `run_normalize` keeps the stripped env on purpose: there the point is a hermetic,
    reproducible read, and a failure there is a real failure.
    """
    import os
    return {**os.environ, "PYTHONPATH": str(SRC)}


def corpus_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.md"))
    if not files:
        raise SystemExit(f"fail-closed: no .md files in {directory}")
    return files


def corpus_sha(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in corpus_files(directory):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def run_normalize(path: Path, contexts: list[str]) -> list[dict]:
    result = subprocess.run(
        [sys.executable, "-m", "semantic_normalizer", "normalize", str(path),
         "--contexts", *contexts],
        capture_output=True, cwd=ROOT,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
    )
    if result.returncode != 0:
        raise SystemExit(
            f"fail-closed: normalize failed on {path.name}:\n"
            + result.stderr.decode("utf-8", "replace")[:800]
        )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def match_corpus(corpus: Path, contexts: list[str]) -> list[dict]:
    """MATCH: one record per sentence, from the normalizer's own segmentation."""
    segments = []
    for path in corpus_files(corpus):
        for index, record in enumerate(run_normalize(path, contexts)):
            original = record.get("original", "")
            segments.append({
                "source": path.name,
                "index": index,
                "text": original,
                # Captions are not prose — the admission rule's own definition. A segment
                # containing markdown image syntax is excluded from sentence accounting AND
                # from citations rather than half-counted (the caption-as-prose defect).
                "prose": not HEADING.match(original) and "![" not in original,
                "concept_ids": record.get("concept_ids") or [],
                "match_events": record.get("match_events") or [],
                "unresolved": record.get("unresolved") or [],
            })
    return segments


def undecided_tokens(segment: dict, decided: dict) -> list[str]:
    """PARTITION helper: content words in this sentence the loop has not yet ruled on."""
    tokens = []
    for span in segment["unresolved"]:
        for token in WORD.findall(span.get("original", "")):
            key = fold(token)
            if key in STOPWORDS or key in decided:
                continue
            tokens.append(key)
    return sorted(set(tokens))


def partition(segments: list[dict], decided: dict) -> dict:
    """Per-sentence terminal states. `covered` and `decided` sentences cost no AI."""
    prose = [s for s in segments if s["prose"] and s["text"].strip()]
    covered = decided_count = 0
    pending_tokens: dict[str, int] = {}
    pending_sentences = 0
    for segment in prose:
        remaining = undecided_tokens(segment, decided)
        if not remaining:
            if segment["concept_ids"]:
                covered += 1
            else:
                decided_count += 1
            continue
        pending_sentences += 1
        for token in remaining:
            pending_tokens[token] = pending_tokens.get(token, 0) + 1
    return {
        "prose_sentences": len(prose),
        "covered_with_concepts": covered,
        "decided_all_common": decided_count,
        "pending_sentences": pending_sentences,
        "distinct_undecided_tokens": len(pending_tokens),
        "pending_tokens": pending_tokens,
    }


def registry_surfaces(contexts: list[str]) -> set[str]:
    """Every surface (any policy) of every concept whose contexts intersect the scope."""
    scope = {c.casefold() for c in contexts}
    surfaces: set[str] = set()
    for line in REGISTRY_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record_ctx = {str(c).casefold() for c in record.get("contexts", [])}
        if record_ctx and not (record_ctx & scope):
            continue
        for entries in record["lexical_forms"].values():
            for item in entries:
                surfaces.add(fold(item["form"]))
    return surfaces


def existing_concepts_digest(contexts: list[str]) -> list[dict]:
    """Compact list the AI nodes use for the already-covered check."""
    scope = {c.casefold() for c in contexts}
    out = []
    for line in REGISTRY_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record_ctx = {str(c).casefold() for c in record.get("contexts", [])}
        if record_ctx and not (record_ctx & scope):
            continue
        out.append({
            "id": record["concept_id"],
            "pt": record["labels"]["pt-BR"]["pref"],
            "en": record["labels"]["en"]["pref"],
            "def": record["definition"][:120],
        })
    return sorted(out, key=lambda item: item["id"])


def rank_candidates(state: dict, round_dir: Path) -> list[dict]:
    """RANK: ATE ranking, then the deterministic admissibility filter."""
    raw_path = round_dir / "candidates-raw.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "rank_term_candidates.py"),
         "--corpus", state["corpus"], "--reference", state["reference"],
         "--output", str(raw_path)],
        capture_output=True, cwd=ROOT,
    )
    if result.returncode != 0:
        raise SystemExit(
            "fail-closed: rank_term_candidates failed:\n"
            + result.stderr.decode("utf-8", "replace")[:800]
        )
    return read_json(raw_path)["candidates"]


def citations_for(term: str, segments: list[dict], cap: int = 6) -> list[dict]:
    """Sentences where the term really occurs — on WORD BOUNDARIES, not as a substring.

    Found by the adversarial node, which is the only reason it was found: it reported that four
    of the six citations offered for `trair` did not contain the word at all. They were substring
    hits inside `extrair` and `atrair`. The ranking is token-based and was right; the evidence
    handed to the adjudicator was wrong, which is the worse failure of the two — a model asked to
    decide a sense from sentences that do not contain the term is being asked to hallucinate, and
    the entry it produced was refuted on exactly that ground.

    The boundary excludes letters and digits on both sides but NOT the hyphen, because hyphenated
    surfaces are single terms here (`come-cotas`, `pós-fixado`) and treating `-` as a boundary
    would make `cotas` match inside `come-cotas`.
    """
    needle = re.escape(fold(term))
    pattern = re.compile(rf"(?<![a-zà-ÿ0-9\-]){needle}(?![a-zà-ÿ0-9\-])")
    found = []
    for segment in segments:
        if not segment["prose"]:
            continue
        if pattern.search(fold(segment["text"])):
            found.append({
                "source": segment["source"],
                "sentence": segment["index"],
                "text": segment["text"].strip(),
            })
            if len(found) >= cap:
                break
    return found


# --------------------------------------------------------------------------------------
# AI task protocol
# --------------------------------------------------------------------------------------

ADJUDICATE_INSTRUCTIONS = """You are the ADJUDICATE node of lexicon_pipeline: given ranked term
candidates from a corpus, decide for EACH one whether it is a domain concept that belongs in
the dictionary, and if so propose the complete entry. Statistics ranked these candidates; you
decide. Rules, all enforced downstream by a deterministic validator — violating them bounces
the decision back to you with the error:

1. ADMIT only units that carry the DOMAIN's meaning. If a general newspaper text would use the
   word in the same sense, it is common language: reject as `common-language`.
2. The sense comes from the CITATIONS provided, never from world knowledge alone. If the
   citations use the word differently from its textbook sense, the citations win.
3. One concept = ONE sense. Never bundle two senses, never opposite senses. If the surface has
   two live readings in the citations, either reject as `ambiguous` or admit with the surface
   listed under `obs_pt` (review policy) instead of `pt`/`alt_pt`.
4. If the term is mostly a FRAGMENT of a longer expression in the citations, reject as
   `fragment` — the longer expression is or will be its own candidate.
5. If an existing concept (list provided) already means this, reject as `already-covered`.
6. OCR noise, file names, markdown furniture: reject as `corpus-artifact`.
7. `id` = "<domain>.<english_snake_case>". `class` and `pos` from the enums provided.
8. `def`: English, one sentence, >= 40 chars, stating the sense AS USED in the citations.
9. `pt` = the corpus lemma (what a rewrite should substitute — singular unless the corpus only
   inflects it); `en` = the English equivalent. `alt_pt` = other attested spellings;
   `obs_pt` = attested inflections (plurals etc.). Every surface you list must actually occur
   in the corpus except `en`/`alt_en` when the corpus is Portuguese-only.
10. `pos_pt`: copy ONE sentence VERBATIM from the citations (character-for-character, accents
    included) that uses the term in the admitted sense. A fabricated or edited quote is
    rejected by a byte-comparison against the corpus. `neg_pt`: AUTHOR a short sentence using
    the term in the wrong (non-domain) sense — do not copy it from anywhere.
11. `authority`: "corpus:<source-file>#<term-slug>" using the citation's source file.
12. Decide EVERY candidate in the task. No silent drops.
"""

REFUTE_INSTRUCTIONS = """You are the REFUTE node of lexicon_pipeline: the adversarial reviewer
of dictionary admissions. Your default verdict is REFUTE — a proposal survives only if you FAIL
to break it. You are judging someone else's work; do not extend it charity. For each proposal
run every check:

a. COMMON-LANGUAGE: would a general text about an unrelated subject use this word in the same
   sense? If yes, refute.
b. SENSE-VS-CITATIONS: does the definition state how the CITATIONS use the term? A correct
   dictionary definition of a different sense is a refutation.
c. FRAGMENT: do the citations show the term mostly inside a longer expression? Refute.
d. DUPLICATE: does any existing concept (list provided) already cover it? Refute.
e. LABELS: is `pt` a real surface in the citations (lemma, not a truncation)? Is `en` a real
   equivalent? A wrong lemma poisons every rewrite.
f. ONE-SENSE: does the entry bundle two senses, or opposite senses? Refute.
Uphold ONLY if every check passes, and say which citation convinced you. Answer EVERY proposal.
"""

PRECISION_A_INSTRUCTIONS = """You are precision reader A. For each match event: the dictionary
matched `alias` inside `sentence` and mapped it to the concept whose definition is given.
Judge CORRECT if the sentence uses the alias in the sense of that definition, INCORRECT
otherwise. Read the sentence, not the concept name. Answer every event."""

PRECISION_B_INSTRUCTIONS = """You are precision reader B, the adversarial one. START from the
assumption that each match is WRONG and try to prove it: another reading of the sentence, a
fragment of a longer expression, a sense the definition does not cover. Mark CORRECT only when
you fail to build the wrong-reading case. Answer every event."""


def emit_tasks(round_dir: Path, kind: str, groups: list[list[dict]], shared: dict) -> list[str]:
    pending = round_dir / "tasks" / "pending"
    ids = []
    for number, group in enumerate(groups, 1):
        payload = {"kind": kind, "group": number, **shared, "items": group}
        task_id = stable_id(f"{kind}-g{number:02d}", payload)
        payload["task_id"] = task_id
        write_json(pending / f"{task_id}.json", payload)
        ids.append(task_id)
    return ids


def pending_without_results(round_dir: Path, kind: str) -> list[Path]:
    # `-g` anchors the kind: "adjudicate-g*" must not swallow "adjudicate-retry-g*".
    pending = sorted((round_dir / "tasks" / "pending").glob(f"{kind}-g*.json"))
    results = round_dir / "tasks" / "results"
    return [path for path in pending if not (results / path.name).exists()]


def group(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# --------------------------------------------------------------------------------------
# Validators — the anti-hallucination boundary
# --------------------------------------------------------------------------------------

def validate_adjudication(decision: dict, candidate: dict, state: dict, enums: dict,
                          attested_fold: str, surfaces: set[str],
                          existing_ids: set[str]) -> list[str]:
    """Gate between what a model SAID and what enters state.

    `attested_fold` is the casefolded PROSE of the corpus (headings and captions already
    excluded), so "occurs in the corpus" below always means "attested in prose" — the
    admission rule's own definition. A verbatim-looking quote that only exists in a heading
    fails here by construction.
    """
    errors = []
    if not isinstance(decision.get("admit"), bool):
        return ["`admit` must be true or false"]
    if not decision.get("why"):
        errors.append("`why` is required")
    if not decision["admit"]:
        if decision.get("rejected_as") not in REJECTION_REASONS:
            errors.append(f"`rejected_as` must be one of {REJECTION_REASONS}")
        return errors
    concept = decision.get("concept") or {}
    cid = concept.get("id", "")
    if not CONCEPT_ID.fullmatch(cid):
        errors.append(f"id {cid!r} does not match <domain>.<snake_case>")
    elif not cid.startswith(state["domain"] + "."):
        errors.append(f"id {cid!r} must start with {state['domain']!r}.")
    elif cid in existing_ids:
        errors.append(f"id {cid!r} already in registry")
    if concept.get("class") not in enums["semantic_class"]:
        errors.append(f"class must be one of {enums['semantic_class']}")
    if concept.get("pos") not in enums["pos"]:
        errors.append(f"pos must be one of {enums['pos']}")
    definition = concept.get("def", "")
    if len(definition) < 40:
        errors.append("def must be >= 40 chars, English, one sentence")
    pt = concept.get("pt", "")
    en = concept.get("en", "")
    if not pt or not en:
        errors.append("pt and en prefs are required")
    term_fold = fold(candidate["term"])
    proposal_surfaces = [pt, *concept.get("alt_pt", []), *concept.get("obs_pt", []),
                         en, *concept.get("alt_en", []), *concept.get("obs_en", [])]
    if term_fold not in {fold(s) for s in proposal_surfaces if s}:
        errors.append(
            f"the candidate term {candidate['term']!r} is not among the proposed surfaces — "
            "admitting this concept would not decide the term"
        )
    for surface in (pt, *concept.get("alt_pt", []), *concept.get("obs_pt", [])):
        if surface and fold(surface) not in attested_fold:
            errors.append(f"surface {surface!r} does not occur in the corpus")
    for surface in proposal_surfaces:
        if surface and fold(surface) in surfaces:
            errors.append(f"surface {surface!r} already owned in this scope; "
                          "reject as already-covered or drop the surface")
    quotes = concept.get("pos_pt") or []
    if not quotes:
        errors.append("pos_pt with one verbatim corpus sentence is required")
    # ANY declared Portuguese surface, not the pref alone. The first version demanded the pref
    # and refused `premissa oculta` because its corpus sentences say `premissas ocultas` — the
    # attested plural, declared in `obs_pt`. That is a lemma meeting a real corpus, which is the
    # normal case, not a defect: forcing every example into the singular would either reject
    # correct entries or push adjudicators to pick a worse quote. What the check is actually for
    # — the example must SHOW the term — survives intact, because a quote using none of the
    # declared surfaces still fails.
    pt_surfaces = [s for s in (pt, *concept.get("alt_pt", []), *concept.get("obs_pt", [])) if s]
    for quote in quotes:
        if fold(quote.strip()) not in attested_fold:
            errors.append(f"pos_pt is not a verbatim corpus quote: {quote[:60]!r}…")
        elif pt_surfaces and not any(fold(s) in fold(quote) for s in pt_surfaces):
            errors.append(
                f"pos_pt uses none of the declared surfaces {pt_surfaces}: {quote[:60]!r}…")
    negatives = concept.get("neg_pt") or []
    if not negatives:
        errors.append("neg_pt (authored wrong-sense sentence) is required")
    for negative in negatives:
        if fold(negative.strip()) in attested_fold:
            errors.append("neg_pt must be AUTHORED, not copied from the corpus")
    authority = concept.get("authority", "")
    if not authority.startswith("corpus:"):
        errors.append("authority must be corpus:<source-file>#<slug>")
    else:
        source_file = authority[len("corpus:"):].split("#", 1)[0]
        if not (Path(state["corpus"]) / source_file).is_file():
            errors.append(f"authority cites {source_file!r}, not a corpus file")
    return errors


def merge_proposals(proposals: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Collapse proposals that decide ONE concept, and split the ones that only look alike.

    Two candidates in the same round can resolve to the same concept — `argumento` and
    `argumentos` both produced `reasoning.argument`, from two agents that never saw each other.
    Three ways to handle it and only one is correct:

    * drop the duplicate — what the importer does on its own, and it breaks CONVERGENCE: the
      dropped term is never recorded as decided, so it returns as a candidate every round and
      the loop never drains. This is the Newton analogue of a step that undoes itself.
    * refuse both — throws away a correct admission because two agents agreed.
    * MERGE — one concept, union of the surfaces, and BOTH terms recorded as decided by it.

    Same id with a DIFFERENT preferred label is not a merge; it is two models disagreeing about
    what the concept is, which is exactly the case a human should see. Those go to needs-owner.
    The winner among mergeable duplicates is chosen by sorted term, never by arrival order, so
    the result does not depend on which agent finished first.
    """
    by_id: dict[str, list[dict]] = {}
    for proposal in sorted(proposals, key=lambda p: p["candidate"]["term"]):
        by_id.setdefault(proposal["decision"]["concept"]["id"], []).append(proposal)
    merged, conflicts, notes = [], [], []
    for cid, group in sorted(by_id.items()):
        prefs = {fold(item["decision"]["concept"]["pt"]) for item in group}
        terms = [item["candidate"]["term"] for item in group]
        if len(group) > 1 and len(prefs) > 1:
            conflicts.extend(group)
            notes.append(f"{cid}: {terms} proposed with different prefs {sorted(prefs)} "
                         "— sent to needs-owner")
            continue
        winner = group[0]
        if len(group) > 1:
            concept = dict(winner["decision"]["concept"])
            for field in ("alt_pt", "alt_en", "obs_pt", "obs_en"):
                seen, union = set(), []
                for item in group:
                    for surface in item["decision"]["concept"].get(field, []) or []:
                        if fold(surface) not in seen:
                            seen.add(fold(surface))
                            union.append(surface)
                concept[field] = union
            winner = {**winner, "decision": {**winner["decision"], "concept": concept}}
            notes.append(f"{cid}: merged {terms} into one concept "
                         f"(pref {concept['pt']!r}, surfaces unioned)")
        merged.append({**winner, "terms": terms})
    return merged, conflicts, notes


def validate_refutation(verdicts: dict, proposals: list[dict]) -> list[str]:
    errors = []
    for proposal in proposals:
        verdict = verdicts.get(fold(proposal["term"]))
        if verdict is None:
            errors.append(f"missing verdict for {proposal['term']!r}")
        elif verdict.get("verdict") not in ("uphold", "refute"):
            errors.append(f"{proposal['term']!r}: verdict must be uphold|refute")
        elif not verdict.get("reason"):
            errors.append(f"{proposal['term']!r}: reason required")
    return errors


# --------------------------------------------------------------------------------------
# APPLY — the only writer, snapshot + suite gate
# --------------------------------------------------------------------------------------

def current_registry_version() -> str:
    match = re.search(r'REGISTRY_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"',
                      REGISTRY_PY.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("fail-closed: REGISTRY_VERSION not found in registry.py")
    return ".".join(match.groups())


def bump_minor(version: str) -> str:
    major, minor, _ = version.split(".")
    return f"{major}.{int(minor) + 1}.0"


def set_registry_version(version: str) -> None:
    text = REGISTRY_PY.read_text(encoding="utf-8")
    updated = re.sub(r'REGISTRY_VERSION\s*=\s*"\d+\.\d+\.\d+"',
                     f'REGISTRY_VERSION = "{version}"', text, count=1)
    if updated == text:
        raise SystemExit("fail-closed: could not update REGISTRY_VERSION")
    REGISTRY_PY.write_text(updated, encoding="utf-8")


def next_batch_number(domain: str) -> int:
    numbers = [
        int(match.group(1))
        for path in (ROOT / "config").glob(f"{domain}-batch-*.json")
        if (match := re.search(r"batch-(\d+)\.json$", path.name))
    ]
    return max(numbers, default=0) + 1


def apply_batch(state: dict, round_dir: Path, entries: list[dict], note: str) -> dict:
    number = next_batch_number(state["domain"])
    batch = {
        "batch": number,
        "domain": state["domain"],
        "contexts": [state["domain"]],
        "corpus_sha256": state["corpus_sha256"],
        "adjudication_note": note,
        "concepts": entries,
    }
    batch_path = ROOT / "config" / f"{state['domain']}-batch-{number:02d}.json"
    write_json(batch_path, batch)
    write_json(round_dir / "batch.json", batch)

    backup = round_dir / "snapshot"
    backup.mkdir(exist_ok=True)
    for path in SNAPSHOT_FILES:
        if path.exists():
            shutil.copy2(path, backup / path.name)

    version = bump_minor(current_registry_version())
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_cga_batch.py"),
         "--batch", str(batch_path), "--registry-version", version],
        capture_output=True, cwd=ROOT, text=True,
    )
    outcome = {"registry_version": version, "stdout": result.stdout.strip().splitlines()}
    if result.returncode != 0:
        _restore(backup)
        batch_path.unlink(missing_ok=True)
        raise SystemExit(f"fail-closed: import failed:\n{result.stderr[:800]}")
    imported = json.loads(result.stdout.splitlines()[0])
    outcome["import"] = imported
    set_registry_version(version)

    # The generated artifacts hash the registry, so they must be re-cut BEFORE the suite runs —
    # in the Makefile's order (release, then manifest; MANIFEST.json hashes release-manifest.json,
    # so the reverse order describes a file that no longer exists in that form). Skipping this
    # makes the manifest test fail on every apply and the revert below fire on a healthy batch:
    # a fail-closed guard that closes on success is just a broken loop.
    for step, command in (
        # `dev-semantic-gates.json` pins the registry hash it was measured against, so it is a
        # generated artifact of the registry exactly like the manifests. It is re-cut FIRST:
        # build_release hashes the reports directory into the release manifest, so cutting the
        # release before the report seals a report that is about to change.
        ("semantic-gates",
         [sys.executable, str(ROOT / "scripts" / "evaluate_semantic_gates.py")]),
        ("release", [sys.executable, str(ROOT / "scripts" / "build_release.py"), "--json"]),
        ("manifest", [sys.executable, str(ROOT / "scripts" / "cut_manifest.py")]),
    ):
        cut = subprocess.run(command, capture_output=True, cwd=ROOT, text=True, env=_env())
        if cut.returncode != 0:
            _restore(backup)
            batch_path.unlink(missing_ok=True)
            raise SystemExit(f"fail-closed: re-cutting {step} failed:\n{cut.stderr[:800]}")

    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        capture_output=True, cwd=ROOT, text=True, env=_env(),
    )
    outcome["suite_tail"] = tests.stdout.strip().splitlines()[-3:]
    if tests.returncode != 0:
        _restore(backup)
        batch_path.unlink(missing_ok=True)
        outcome["reverted"] = True
        write_json(round_dir / "applied.json", outcome)
        raise SystemExit(
            "fail-closed: test suite went red after the import — batch REVERTED. Tail:\n"
            + "\n".join(tests.stdout.splitlines()[-15:])
        )
    write_json(round_dir / "applied.json", outcome)
    return outcome


def _restore(backup: Path) -> None:
    for path in SNAPSHOT_FILES:
        source = backup / path.name
        if source.exists():
            shutil.copy2(source, path)


# --------------------------------------------------------------------------------------
# The state machine
# --------------------------------------------------------------------------------------

def load_state(run_dir: Path, args) -> dict:
    state_path = run_dir / "state.json"
    if state_path.exists():
        return read_json(state_path)
    if not (args.domain and args.corpus and args.reference):
        raise SystemExit("fail-closed: new run needs --domain, --corpus and --reference")
    corpus = Path(args.corpus).resolve()
    reference = Path(args.reference).resolve()
    for directory in (corpus, reference):
        corpus_files(directory)  # PREFLIGHT: raises when empty
    state = {
        "schema_version": "lexicon-run-v1",
        "domain": args.domain,
        "corpus": str(corpus),
        "reference": str(reference),
        "corpus_sha256": corpus_sha(corpus),
        "contexts": ["core", args.domain],
        "round": 1,
        "phase": "match",
        "dry_rounds": 0,
        "registry_version_at_start": current_registry_version(),
        "config": {"top": args.top, "group_size": args.group_size,
                   "max_rounds": args.max_rounds, "seed": args.seed,
                   "sample": args.sample},
        "needs_owner": [],
        "status": "running",
    }
    write_json(state_path, state)
    return state


def save_state(run_dir: Path, state: dict) -> None:
    write_json(run_dir / "state.json", state)


def load_decided(run_dir: Path) -> dict:
    path = run_dir / "decided.json"
    return read_json(path) if path.exists() else {}


def phase_match(state: dict, run_dir: Path, round_dir: Path) -> str:
    decided = load_decided(run_dir)
    segments = match_corpus(Path(state["corpus"]), state["contexts"])
    # Persisted so the validator later checks quotes and surfaces against PROSE — a quote from
    # a heading or a caption must not count as attestation.
    (round_dir / "prose.txt").write_text(
        "\n".join(s["text"].strip() for s in segments if s["prose"] and s["text"].strip()),
        encoding="utf-8",
    )
    candidates = rank_candidates(state, round_dir)
    surfaces = registry_surfaces(state["contexts"])
    admissible, auto_rejected = [], []
    for candidate in sorted(candidates, key=lambda c: (-c["keyness_g2"], c["term"])):
        key = fold(candidate["term"])
        if key in decided or key in surfaces:
            continue
        cites = citations_for(candidate["term"], segments)
        if not cites:
            decided[key] = {"state": "no-prose-attestation", "round": state["round"],
                            "reason": "never occurs in a prose sentence — headings, "
                                      "captions or cross-line artifact only"}
            auto_rejected.append(candidate["term"])
            continue
        admissible.append({**candidate, "citations": cites})
        if len(admissible) >= state["config"]["top"]:
            break
    write_json(run_dir / "decided.json", decided)
    write_json(round_dir / "round.json", {
        "round": state["round"],
        "pool_admissible": len(admissible),
        "auto_rejected_no_prose": auto_rejected,
        "coverage_at_round_start": partition(segments, decided),
    })
    if not admissible:
        state["phase"] = "precision"
        return "converged: candidate pool drained"
    shared = {
        "domain": state["domain"],
        "round": state["round"],
        "instructions": ADJUDICATE_INSTRUCTIONS,
        "enums": schema_enums(),
        "existing_concepts": existing_concepts_digest(state["contexts"]),
        "output_contract": {
            "decisions": [{
                "term": "<candidate term verbatim>", "admit": "bool", "why": "str",
                "rejected_as": f"one of {REJECTION_REASONS} when admit=false",
                "concept": {"id": "<domain>.<snake>", "class": "enum", "pos": "enum",
                            "en": "str", "pt": "str", "alt_en": [], "alt_pt": [],
                            "obs_en": [], "obs_pt": [], "def": "str >=40 chars",
                            "authority": "corpus:<file>#<slug>",
                            "pos_pt": ["verbatim corpus sentence"],
                            "neg_pt": ["authored wrong-sense sentence"]},
            }],
        },
    }
    ids = emit_tasks(round_dir, "adjudicate",
                     group(admissible, state["config"]["group_size"]), shared)
    state["phase"] = "adjudicate"
    return f"emitted {len(ids)} adjudicate task(s)"


def collect_results(round_dir: Path, kind: str) -> tuple[list[dict], list[Path]]:
    missing = pending_without_results(round_dir, kind)
    if missing:
        return [], missing
    results = []
    # `-g` again: without it, kind="adjudicate" swallows the "adjudicate-retry" wave and reads
    # a result file that does not exist yet — a crash instead of a wait.
    for path in sorted((round_dir / "tasks" / "pending").glob(f"{kind}-g*.json")):
        task = read_json(path)
        result = read_json(round_dir / "tasks" / "results" / path.name)
        results.append({"task": task, "result": result})
    return results, []


def phase_adjudicate(state: dict, run_dir: Path, round_dir: Path) -> str:
    pairs, missing = collect_results(round_dir, "adjudicate")
    if missing:
        return f"waiting on {len(missing)} adjudicate result(s)"
    retry_pairs, retry_missing = collect_results(round_dir, "adjudicate-retry")
    if retry_missing:
        return f"waiting on {len(retry_missing)} adjudicate-retry result(s)"
    enums = schema_enums()
    prose_fold = fold((round_dir / "prose.txt").read_text(encoding="utf-8"))
    surfaces = registry_surfaces(state["contexts"])
    existing_ids = {
        json.loads(line)["concept_id"]
        for line in REGISTRY_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()
    }
    decided = load_decided(run_dir)

    # One decision per term; a retry decision (attempt 2) overrides the original. Reprocessing
    # after the retry wave is therefore idempotent instead of re-bouncing the originals.
    candidates: dict[str, dict] = {}
    latest: dict[str, tuple[int, dict]] = {}
    for pair in pairs + retry_pairs:
        attempt = pair["task"].get("attempt", 1)
        for candidate in pair["task"]["items"]:
            candidates.setdefault(fold(candidate["term"]), candidate)
        for decision in pair["result"].get("decisions", []):
            key = fold(decision.get("term", ""))
            if key in candidates and (key not in latest or attempt >= latest[key][0]):
                latest[key] = (attempt, decision)
    retried_already = bool(retry_pairs)

    proposals, retry = [], []
    for key, candidate in sorted(candidates.items()):
        attempt, decision = latest.get(key, (1 if not retried_already else 2, None))
        errors = (["candidate was not decided"] if decision is None else
                  validate_adjudication(decision, candidate, state, enums, prose_fold,
                                        surfaces, existing_ids))
        if not errors and decision is not None:
            if decision["admit"]:
                proposals.append({"candidate": candidate, "decision": decision})
            else:
                decided[key] = {"state": decision["rejected_as"],
                                "round": state["round"], "reason": decision["why"]}
        elif attempt >= 2 or retried_already:
            state["needs_owner"].append({
                "term": candidate["term"], "round": state["round"],
                "reason": "adjudication failed validation twice", "errors": errors})
            decided[key] = {"state": "needs-owner", "round": state["round"],
                            "reason": "validation failed twice"}
        else:
            retry.append({"candidate": candidate, "errors": errors})
    proposals, conflicting, merge_notes = merge_proposals(proposals)
    for item in conflicting:
        key = fold(item["candidate"]["term"])
        state["needs_owner"].append({
            "term": item["candidate"]["term"], "round": state["round"],
            "reason": "two proposals share a concept id with different preferred labels",
            "concept_id": item["decision"]["concept"]["id"]})
        decided[key] = {"state": "needs-owner", "round": state["round"],
                        "reason": "duplicate concept id, conflicting pref"}
    write_json(run_dir / "decided.json", decided)
    if retry:
        template = read_json(sorted((round_dir / "tasks" / "pending")
                                    .glob("adjudicate-g*.json"))[0])
        shared = {k: template[k] for k in ("domain", "round", "instructions", "enums",
                                           "existing_concepts", "output_contract")}
        shared["attempt"] = 2
        shared["validation_errors"] = [
            {"term": item["candidate"]["term"], "errors": item["errors"]} for item in retry
        ]
        ids = emit_tasks(round_dir, "adjudicate-retry",
                         group([item["candidate"] for item in retry],
                               state["config"]["group_size"]), shared)
        return f"emitted {len(ids)} retry task(s) with validation errors attached"
    write_json(round_dir / "proposals.json",
               [{"term": p["candidate"]["term"], "terms": p["terms"],
                 **p["decision"]["concept"], "why": p["decision"]["why"]} for p in proposals])
    if merge_notes:
        write_json(round_dir / "merges.json", merge_notes)
    if not proposals:
        return _close_round(state, round_dir, [], [],
                            note="round produced no admissions")
    shared = {
        "domain": state["domain"],
        "round": state["round"],
        "instructions": REFUTE_INSTRUCTIONS,
        "existing_concepts": existing_concepts_digest(state["contexts"]),
        "output_contract": {"verdicts": [{"term": "<term>", "verdict": "uphold|refute",
                                          "reason": "str, cite the citation that decided"}]},
    }
    items = [{
        "term": p["candidate"]["term"],
        "citations": p["candidate"]["citations"],
        "statistics": {k: p["candidate"][k] for k in
                       ("frequency", "keyness_g2", "weirdness", "c_value")},
        "proposal": p["decision"]["concept"],
        "adjudicator_why": p["decision"]["why"],
    } for p in proposals]
    ids = emit_tasks(round_dir, "refute", group(items, state["config"]["group_size"]), shared)
    state["phase"] = "refute"
    return f"{len(proposals)} proposal(s) → emitted {len(ids)} refute task(s)"


def phase_refute(state: dict, run_dir: Path, round_dir: Path) -> str:
    pairs, missing = collect_results(round_dir, "refute")
    if missing:
        return f"waiting on {len(missing)} refute result(s)"
    proposals = read_json(round_dir / "proposals.json")
    verdicts: dict[str, dict] = {}
    for pair in pairs:
        for verdict in pair["result"].get("verdicts", []):
            verdicts[fold(verdict.get("term", ""))] = verdict
        errors = validate_refutation(verdicts, pair["task"]["items"])
        if errors:
            raise SystemExit("fail-closed: refute results invalid: " + "; ".join(errors[:5]))
    decided = load_decided(run_dir)
    upheld, refuted = [], []
    for proposal in proposals:
        verdict = verdicts[fold(proposal["term"])]
        # EVERY term the concept decides is recorded, not just the one the refuter judged.
        # A merged concept covers several candidates; leaving the others undecided would put
        # them back in the pool next round and the loop would never drain.
        for term in proposal.get("terms") or [proposal["term"]]:
            decided[fold(term)] = (
                {"state": "admitted", "round": state["round"],
                 "concept": proposal["id"], "reason": proposal["why"]}
                if verdict["verdict"] == "uphold" else
                {"state": "refuted", "round": state["round"], "reason": verdict["reason"]})
        if verdict["verdict"] == "uphold":
            upheld.append(proposal)
        else:
            refuted.append({"term": proposal["term"], "reason": verdict["reason"]})
    write_json(run_dir / "decided.json", decided)
    return _close_round(state, round_dir, upheld, refuted,
                        note=f"{len(upheld)} upheld, {len(refuted)} refuted by the "
                             "adversarial node")


def _close_round(state, round_dir, upheld, refuted, note: str) -> str:
    applied = None
    if upheld:
        entries = []
        for proposal in upheld:
            entry = {k: v for k, v in proposal.items() if k not in ("term", "terms", "why")}
            entry["source"] = (f"{state['domain']} corpus, adjudicated by an AI node under "
                              "lexicon_pipeline, upheld by an independent adversarial "
                              "refuter, citations byte-verified")
            entries.append(entry)
        lines = [f"Round {state['round']} of lexicon_pipeline run. Adjudication and "
                 "adversarial refutation by AI nodes; every citation and example "
                 "byte-verified against the corpus by the deterministic validator. "
                 "Per-term reasons live in the run directory.",
                 f"Refuted this round: "
                 + ("; ".join(f"{r['term']} ({r['reason'][:80]})" for r in refuted)
                    if refuted else "none")]
        applied = apply_batch(state, round_dir, entries, "\n".join(lines))
    summary = read_json(round_dir / "round.json")
    summary.update({
        "admitted": [p["term"] for p in upheld],
        "refuted": refuted,
        "note": note,
        "applied": applied,
    })
    write_json(round_dir / "round.json", summary)
    state["dry_rounds"] = state["dry_rounds"] + 1 if not upheld else 0
    if state["dry_rounds"] >= 2:
        state["phase"] = "precision"
        return f"round {state['round']} closed ({note}); converged after 2 dry rounds"
    if state["round"] >= state["config"]["max_rounds"]:
        state["phase"] = "precision"
        return f"round {state['round']} closed ({note}); max rounds reached"
    state["round"] += 1
    state["phase"] = "match"
    return f"round {state['round'] - 1} closed: {note}"


def phase_precision(state: dict, run_dir: Path) -> str:
    precision_dir = run_dir / "precision"
    pending = precision_dir / "tasks" / "pending"
    if not pending.exists() or not list(pending.glob("*.json")):
        segments = match_corpus(Path(state["corpus"]), state["contexts"])
        events = []
        for segment in segments:
            if not segment["prose"]:
                continue
            for number, event in enumerate(segment["match_events"]):
                if event["concept_id"].startswith(state["domain"] + "."):
                    events.append({
                        "event_id": f"{segment['source']}:{segment['index']}:{number}",
                        "sentence": segment["text"].strip(),
                        "alias": event["alias"],
                        "concept_id": event["concept_id"],
                        "definition": event.get("sense", ""),
                    })
        events.sort(key=lambda e: e["event_id"])
        population = len(events)
        sample_size = min(state["config"]["sample"], population)
        sample = (random.Random(state["config"]["seed"]).sample(events, sample_size)
                  if sample_size else [])
        write_json(precision_dir / "population.json",
                   {"population": population, "sample": sample_size,
                    "seed": state["config"]["seed"]})
        if not sample:
            state["phase"] = "report"
            return "no domain match events to sample; skipping precision"
        for kind, instructions in (("precision-a", PRECISION_A_INSTRUCTIONS),
                                   ("precision-b", PRECISION_B_INSTRUCTIONS)):
            emit_tasks(precision_dir, kind, group(sample, 30),
                       {"domain": state["domain"], "instructions": instructions,
                        "output_contract": {"verdicts": [{
                            "event_id": "str", "verdict": "correct|incorrect",
                            "reason": "str"}]}})
        return "emitted precision reader tasks (A and B)"
    verdicts: dict[str, dict[str, str]] = {}
    for kind in ("precision-a", "precision-b"):
        pairs, missing = collect_results(precision_dir, kind)
        if missing:
            return f"waiting on {len(missing)} {kind} result(s)"
        for pair in pairs:
            for verdict in pair["result"].get("verdicts", []):
                if verdict.get("verdict") not in ("correct", "incorrect"):
                    raise SystemExit("fail-closed: precision verdict outside enum")
                verdicts.setdefault(verdict["event_id"], {})[kind] = verdict["verdict"]
    sample = read_json(precision_dir / "population.json")
    events = {item["event_id"]
              for group_path in sorted((precision_dir / "tasks" / "pending")
                                       .glob("precision-a-*.json"))
              for item in read_json(group_path)["items"]}
    agreed_correct = disagreements = 0
    for event_id in sorted(events):
        pair = verdicts.get(event_id, {})
        if pair.get("precision-a") == pair.get("precision-b") == "correct":
            agreed_correct += 1
        elif pair.get("precision-a") != pair.get("precision-b"):
            disagreements += 1
    n = len(events)
    write_json(precision_dir / "precision.json", {
        "events_read": n,
        "agreed_correct": agreed_correct,
        "reader_disagreements_counted_as_errors": disagreements,
        "point_estimate": round(agreed_correct / n, 4) if n else None,
        "wilson_lower_95": round(wilson_lower(agreed_correct, n), 4) if n else None,
        "population": sample["population"],
        "seed": sample["seed"],
    })
    state["phase"] = "report"
    return f"precision resolved: {agreed_correct}/{n} agreed correct"


def phase_report(state: dict, run_dir: Path) -> str:
    decided = load_decided(run_dir)
    segments = match_corpus(Path(state["corpus"]), state["contexts"])
    final = partition(segments, decided)
    admitted = {k: v for k, v in decided.items() if v["state"] == "admitted"}
    rejected = {k: v for k, v in decided.items()
                if v["state"] in REJECTION_REASONS + ("refuted", "no-prose-attestation")}
    ceiling = {
        token: count for token, count in sorted(final["pending_tokens"].items(),
                                                key=lambda kv: (-kv[1], kv[0]))
    }
    precision_path = run_dir / "precision" / "precision.json"
    report = {
        "domain": state["domain"],
        "corpus_sha256": state["corpus_sha256"],
        "rounds": state["round"],
        "registry_version": {"start": state["registry_version_at_start"],
                            "end": current_registry_version()},
        "chapter_1_admitted": {k: v for k, v in sorted(admitted.items())},
        "chapter_2_rejected_with_reason": {k: v for k, v in sorted(rejected.items())},
        "chapter_3_needs_owner": state["needs_owner"],
        "coverage_final": {k: v for k, v in final.items() if k != "pending_tokens"},
        "ceiling_undecided_tokens": ceiling,
        "ceiling_note": (
            "Tokens the loop never had to rule on: below the ATE floors (weirdness < 8 or "
            "frequency < 3) or never surfaced as candidates. They are general vocabulary by "
            "the statistics; declaring them is the stop discipline — registering them to make "
            "a number rise would make the metric measure itself."),
        "precision": read_json(precision_path) if precision_path.exists() else None,
    }
    write_json(run_dir / "report.json", report)
    state["status"] = "complete"
    state["phase"] = "complete"
    return "report written"


def cmd_run(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(run_dir, args)
    if state["status"] == "failed":
        raise SystemExit("fail-closed: run is marked failed; inspect state.json")
    messages = []
    for _ in range(64):  # advance as far as possible in one invocation
        round_dir = run_dir / "rounds" / f"{state['round']:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        phase = state["phase"]
        if phase == "match":
            message = phase_match(state, run_dir, round_dir)
        elif phase == "adjudicate":
            message = phase_adjudicate(state, run_dir, round_dir)
        elif phase == "refute":
            message = phase_refute(state, run_dir, round_dir)
        elif phase == "precision":
            message = phase_precision(state, run_dir)
        elif phase == "report":
            message = phase_report(state, run_dir)
        else:
            message = "complete"
        messages.append(f"[r{state['round']:02d}/{phase}] {message}")
        save_state(run_dir, state)
        if "waiting" in message or "emitted" in message:
            break
        if state["phase"] == "complete":
            break
    pending = [str(p.relative_to(run_dir))
               for p in run_dir.rglob("tasks/pending/*.json")
               if not (p.parent.parent / "results" / p.name).exists()]
    payload = {"status": state["status"], "phase": state["phase"],
               "round": state["round"], "log": messages, "pending_tasks": pending}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if state["phase"] == "complete":
        return 0
    return 3 if pending else 0


def cmd_status(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    state = read_json(run_dir / "state.json")
    pending = [str(p.relative_to(run_dir))
               for p in run_dir.rglob("tasks/pending/*.json")
               if not (p.parent.parent / "results" / p.name).exists()]
    print(json.dumps({"state": state, "pending_tasks": pending},
                     ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="advance the loop as far as it can go")
    run.add_argument("--run-dir", required=True)
    run.add_argument("--domain")
    run.add_argument("--corpus")
    run.add_argument("--reference")
    run.add_argument("--top", type=int, default=24,
                     help="candidates adjudicated per round")
    run.add_argument("--group-size", type=int, default=8,
                     help="candidates per AI task (tasks in a wave run in parallel)")
    run.add_argument("--max-rounds", type=int, default=8)
    run.add_argument("--seed", type=int, default=20260731)
    run.add_argument("--sample", type=int, default=60,
                     help="precision sample size over the new pack's match events")
    run.set_defaults(func=cmd_run)
    status = sub.add_parser("status")
    status.add_argument("--run-dir", required=True)
    status.set_defaults(func=cmd_status)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
