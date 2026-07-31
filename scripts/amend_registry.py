#!/usr/bin/env python3
"""Apply an adjudicated amendment to concepts that already exist in the registry.

`import_cga_batch.py` only adds. Everything an exhaustive sweep produces is a change to a
concept already present — a form that must stop matching automatically, a collocation that
should have been the anchor, a fixed expression that must never match, or a sense the registry
was missing entirely. Editing `registry.jsonl` by hand would do all of that and record none of
it, so this does it under the same contract as an import: append-only provenance, the collision
demoter re-run over the whole registry, and a release record rebound to the new hashes.

An amendment file is a list of operations, each naming the corpus evidence that justifies it:

    {"op": "demote",  "concept": "...", "language": "pt-BR", "form": "principal",
     "wrong": 18, "total": 28, "why": "adjective 'main', not the principal of a bond"}
    {"op": "add_form", "concept": "...", "language": "pt-BR", "form": "valor principal"}
    {"op": "forbid",  "concept": "...", "language": "pt-BR", "variants": ["à medida que"]}
    {"op": "add_concept", "record": {...}}

`demote` is the repair for a bare token whose financial reading is a minority of its corpus
uses, and it is never used alone: the collocation that marks the financial sense is added in the
same amendment, so the concept keeps an automatic anchor instead of going silent. `forbid` is
for the other class, a fixed expression that merely contains the label — `à medida que` contains
`medida` and means `as`. `add_concept` is for the case the sweeps kept finding: the label was
not too broad, a second sense was simply missing, and once it exists the collision demoter
resolves the contention on its own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "semantic_normalizer" / "data"
REGISTRY = DATA / "registry.jsonl"
RELEASE = DATA / "registry.release.json"
SCHEMA = DATA / "registry.schema.json"
PROVENANCE = DATA / "registry.provenance.jsonl"
RECORDED_AT = "2026-07-31T00:00:00Z"
LANGUAGES = ("en", "pt-BR")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amendment", required=True)
    parser.add_argument("--registry-version", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    amendment = json.loads(Path(args.amendment).read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_id = {record["concept_id"]: record for record in records}
    previous_version = records[0]["registry_version"] if records else "0.0.0"

    applied: dict[str, list[str]] = {
        "demoted": [], "promoted": [], "redefined": [], "forms_added": [], "forms_removed": [],
        "variants_forbidden": [], "concepts_added": [],
    }
    refused: list[str] = []

    # An amendment with no operations is a reseal: the records are already correct and what is
    # stale is the release record's hashes. It exists so that repairing a hash is a recorded
    # event with a stated reason rather than an edit to the file that is supposed to be the
    # evidence — patching `hashes` directly would make the binding self-certifying.
    for operation in amendment["operations"]:
        kind = operation["op"]

        if kind == "add_concept":
            record = operation["record"]
            if record["concept_id"] in by_id:
                refused.append(f"{record['concept_id']} (already in registry)")
                continue
            taken = [
                f"{record['concept_id']}.{language}:{record['labels'][language]['pref']}"
                for language in LANGUAGES
                for other in records
                if other["labels"][language]["pref"] == record["labels"][language]["pref"]
            ]
            if taken:
                refused.extend(f"{item} (preferred label already owned)" for item in taken)
                continue
            record["registry_version"] = args.registry_version
            records.append(record)
            by_id[record["concept_id"]] = record
            applied["concepts_added"].append(record["concept_id"])
            continue

        record = by_id.get(operation["concept"])
        if record is None:
            refused.append(f"{operation['concept']} (not in registry)")
            continue
        language = operation.get("language", "pt-BR")

        if kind == "demote":
            entries = [
                entry for entry in record["lexical_forms"][language]
                if entry["form"] == operation["form"]
            ]
            if not entries:
                refused.append(f"{operation['concept']}.{language}:{operation['form']} (no such form)")
                continue
            # Matching is by surface, not by language: a form spelled the same in both is
            # tagged `shared`, so leaving the twin automatic in the other language undoes the
            # demotion silently. `principal` found this — demoted in pt-BR, still matching
            # through its English twin. A demotion is a statement about the surface.
            for twin in LANGUAGES:
                for entry in record["lexical_forms"][twin]:
                    if entry["form"].casefold() != operation["form"].casefold():
                        continue
                    if entry["policy"] == "auto":
                        entry["policy"] = "review"
                        applied["demoted"].append(
                            f"{operation['concept']}.{twin}:{entry['form']}"
                            + (f" ({operation['wrong']}/{operation['total']} wrong)"
                               if twin == language and "wrong" in operation else " (shared surface)")
                        )
                    # A demoted label stops being an advertised alternative and becomes an
                    # observed inflection, so `pref`/`alt` keep meaning "safe to emit".
                    labels = record["labels"][twin]
                    if entry["form"] in labels["alt"]:
                        labels["alt"].remove(entry["form"])
                        if entry["form"] not in labels["observed"]:
                            labels["observed"].append(entry["form"])

        elif kind == "remove_form":
            # Stronger than `demote`, and sometimes the only thing that works. A review entry
            # dominates an automatic mapping by design (normalizer.py: "Any review ambiguity
            # dominates an automatic mapping for safety"), so a concept that merely *could*
            # claim a surface still suppresses the concept that owns it. Removal is the correct
            # act when the claim is wrong in this domain rather than merely uncertain — and it
            # is the destructive one, so it must name the corpus count that justifies it.
            before = len(record["lexical_forms"][language])
            record["lexical_forms"][language] = [
                entry for entry in record["lexical_forms"][language]
                if entry["form"] != operation["form"]
            ]
            if len(record["lexical_forms"][language]) == before:
                refused.append(f"{operation['concept']}.{language}:{operation['form']} (no such form)")
                continue
            labels = record["labels"][language]
            for group in ("alt", "hidden", "observed"):
                if operation["form"] in labels[group]:
                    labels[group].remove(operation["form"])
            if labels["pref"] == operation["form"]:
                refused.append(
                    f"{operation['concept']}.{language}:{operation['form']} "
                    "(preferred label cannot be removed; rename the concept instead)"
                )
                continue
            applied["forms_removed"].append(f"{operation['concept']}.{language}:{operation['form']}")

        elif kind == "promote":
            # The inverse of `demote`, and it exists because a form can be held at `review` by
            # a collision that a later amendment resolves. `ativo` sat at `review` for the most
            # central concept in the domain because a generic system-state concept also claimed
            # it; once that claim is withdrawn, nothing would restore the form on its own.
            entries = [
                entry for entry in record["lexical_forms"][language]
                if entry["form"] == operation["form"]
            ]
            if not entries:
                refused.append(f"{operation['concept']}.{language}:{operation['form']} (no such form)")
                continue
            for entry in entries:
                entry["policy"] = "auto"
            labels = record["labels"][language]
            if operation["form"] in labels["observed"]:
                labels["observed"].remove(operation["form"])
            if operation["form"] != labels["pref"] and operation["form"] not in labels["alt"]:
                labels["alt"].append(operation["form"])
            applied["promoted"].append(
                f"{operation['concept']}.{language}:{operation['form']} "
                f"({operation.get('right', '?')}/{operation.get('total', '?')} right)"
            )

        elif kind == "redefine":
            applied["redefined"].append(
                f"{operation['concept']}: {record['definition']!r} -> {operation['definition']!r}"
            )
            record["definition"] = operation["definition"]

        elif kind == "add_form":
            existing = {entry["form"] for entry in record["lexical_forms"][language]}
            if operation["form"] in existing:
                refused.append(f"{operation['concept']}.{language}:{operation['form']} (already present)")
                continue
            record["lexical_forms"][language].append(
                {"form": operation["form"], "features": {}, "policy": operation.get("policy", "auto")}
            )
            labels = record["labels"][language]
            if operation.get("policy", "auto") == "auto" and operation["form"] not in labels["alt"]:
                labels["alt"].append(operation["form"])
            applied["forms_added"].append(f"{operation['concept']}.{language}:{operation['form']}")

        elif kind == "forbid":
            bucket = record.setdefault("forbidden_variants", {}).setdefault(language, [])
            for variant in operation["variants"]:
                if variant not in bucket:
                    bucket.append(variant)
                    applied["variants_forbidden"].append(
                        f"{operation['concept']}.{language}:{variant}"
                    )

        else:
            refused.append(f"unknown op {kind!r}")

    records.sort(key=lambda record: record["concept_id"])
    for record in records:
        record["registry_version"] = args.registry_version

    # Re-run over the whole registry, not only over the touched records: a concept added here
    # can collide with a form that was automatic before this amendment existed.
    owners: dict[tuple[str, str], set[str]] = {}
    for record in records:
        for language, entries in record["lexical_forms"].items():
            for entry in entries:
                if entry["policy"] == "auto":
                    owners.setdefault((language, entry["form"]), set()).add(record["concept_id"])
    ambiguous = {key for key, ids in owners.items() if len(ids) > 1}
    collided = []
    for record in records:
        for language, entries in record["lexical_forms"].items():
            for entry in entries:
                if (language, entry["form"]) in ambiguous and entry["policy"] == "auto":
                    entry["policy"] = "review"
                    collided.append(f"{record['concept_id']}.{language}:{entry['form']}")

    summary = {
        "amendment": amendment["id"],
        **{key: len(value) for key, value in applied.items()},
        "demoted_by_collision": collided,
        "refused": refused,
        "registry_total": len(records),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.dry_run:
        print("dry-run: nothing written")
        return 0
    if refused:
        raise SystemExit("amendment refused operations; fix the file rather than writing a partial state")

    event_id = f"{amendment['id']}-to-{args.registry_version}"
    events = [line for line in PROVENANCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(json.loads(line)["event_id"] == event_id for line in events):
        raise SystemExit(f"provenance already records {event_id!r}; the ledger is append-only")

    REGISTRY.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    events.append(json.dumps({
        "provenance_version": "1.0.0",
        "event_id": event_id,
        "event_type": "adjudicated_precision_amendment",
        "recorded_at": RECORDED_AT,
        "source": {
            "historical_path": str(Path(args.amendment).name),
            "corpus_sha256": amendment["corpus_sha256"],
            "method": amendment["method"],
            "operation_count": len(amendment["operations"]),
        },
        "target": {"path": "src/semantic_normalizer/data/registry.jsonl",
                   "registry_version": args.registry_version, "record_count": len(records)},
        "mapping": {**applied, "demoted_by_collision": collided},
        "authority": amendment["authority"],
        "importer": "scripts/amend_registry.py",
        "license": "MIT; definitions authored for this project, no third-party text embedded",
    }, ensure_ascii=False, sort_keys=True))
    PROVENANCE.write_text("\n".join(events) + "\n", encoding="utf-8")

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    release.update({
        "version": args.registry_version,
        "approved_by": "cga-game-delivery-council execution; exhaustive corpus sweep adjudication",
        "approved_at": RECORDED_AT,
        "change_reason": amendment["reason"],
        "affected_concepts": sorted(record["concept_id"] for record in records),
        "added_concepts": sorted(applied["concepts_added"]),
        "reindex_required": True,
        "rollback_version": previous_version,
    })
    release["hashes"] = {
        **release.get("hashes", {}),
        "registry.jsonl": sha(REGISTRY),
        "registry.schema.json": sha(SCHEMA),
        "registry.provenance.jsonl": sha(PROVENANCE),
    }
    RELEASE.write_text(json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
