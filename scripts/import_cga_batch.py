#!/usr/bin/env python3
"""Import an adjudicated CGA concept batch into the registry.

Steps 4-5 of `docs/plan-cga-domain-lexicon-0.4.0.md`.

Input is an adjudication file: one record per concept, with the sense already decided against
the corpus contexts. This script does not choose senses; it expands decisions into records the
schema accepts and appends one governance event per batch.

`authority` carries the verification state, per plan D1, in one of three closed forms — the
schema has `additionalProperties: false`, so a new field was not an option:

    apostila-cga-2026#<file>          definition anchored in the exam material
    openwordnet-pt:1.0.0 ILI=<id>     sense adjudicated over an external candidate
    project-authored:pending-review   authored, no normative source yet
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "semantic_normalizer" / "data"
REGISTRY = DATA / "registry.jsonl"
SCHEMA = DATA / "registry.schema.json"
RELEASE = DATA / "registry.release.json"
PROVENANCE = DATA / "registry.provenance.jsonl"
RECORDED_AT = "2026-07-31T00:00:00Z"

AUTHORITY_PREFIXES = ("apostila-cga-2026", "openwordnet-pt:", "project-authored:")


def forms(automatic: list[str], observed: list[str]) -> list[dict]:
    """Preferred and alternative labels are `auto`; observed inflections are `review`.

    Deduplication is case-insensitive because matching is: `Libor` and `LIBOR` are one form to
    the normaliser and two rows here, which the registry contract rejects. Three batches in a
    row tripped on that (`LIBOR`, `STRIPS`, `duration modificada`), so it is enforced at the
    point where forms are built rather than fixed per batch.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for policy, group in (("auto", automatic), ("review", observed)):
        for form in group:
            key = form.casefold() if form else ""
            if form and key not in seen:
                seen.add(key)
                out.append({"form": form, "features": {}, "policy": policy})
    return out


def distinct_alts(preferred: str, alternatives: list[str]) -> list[str]:
    """SKOS S13: a preferred label may not also appear as an alternative label."""
    out: list[str] = []
    seen = {preferred.casefold()}
    for alternative in alternatives:
        if alternative.casefold() not in seen:
            seen.add(alternative.casefold())
            out.append(alternative)
    return out


def build(entry: dict, version: str) -> dict:
    en, pt = entry["en"], entry["pt"]
    alt_en = distinct_alts(en, entry.get("alt_en", []))
    alt_pt = distinct_alts(pt, entry.get("alt_pt", []))
    obs_en = entry.get("obs_en", [])
    obs_pt = entry.get("obs_pt", [])
    domains = ["finance", "cga"]
    authority = entry["authority"]
    if not authority.startswith(AUTHORITY_PREFIXES):
        raise SystemExit(f"{entry['id']}: authority {authority!r} is outside the closed set")
    return {
        "concept_id": entry["id"],
        "definition": entry["def"],
        "semantic_class": entry["class"],
        "domains": domains,
        "pos": entry["pos"],
        "labels": {
            "en": {"pref": en, "alt": alt_en, "hidden": [], "observed": obs_en},
            "pt-BR": {"pref": pt, "alt": alt_pt, "hidden": [], "observed": obs_pt},
        },
        "lexical_forms": {
            "en": forms([en, *alt_en], obs_en),
            "pt-BR": forms([pt, *alt_pt], obs_pt),
        },
        "relations": {"broader": [], "narrower": [], "related": []},
        "forbidden_variants": {"en": [], "pt-BR": []},
        "contexts": domains,
        "authority": authority,
        "source": "CGA 2026 course material, adjudicated against corpus contexts",
        "positive_examples": {
            "en": [f"The {en} is defined in the CGA material."],
            "pt-BR": [f"O termo {pt} é definido no material da CGA."],
        },
        "negative_examples": {
            "en": [f"Do not use {en} outside its financial sense."],
            "pt-BR": [f"Não use {pt} fora do sentido financeiro."],
        },
        "status": "approved",
        "governed_technical_term": bool(entry.get("technical", False)),
        "registry_version": version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--registry-version", default="2.2.0")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    current = [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_id = {record["concept_id"]: record for record in current}

    # Every preferred label already owned, so a batch cannot silently steal one.
    reserved = {
        (lang, record["labels"][lang]["pref"]): record["concept_id"]
        for record in current
        for lang in ("en", "pt-BR")
    }

    added, refused = [], []
    for entry in batch["concepts"]:
        record = build(entry, args.registry_version)
        if record["concept_id"] in by_id:
            refused.append(f"{record['concept_id']} (already in registry)")
            continue
        clash = [
            f"{record['concept_id']}.{lang}:{record['labels'][lang]['pref']} "
            f"(pref of {reserved[(lang, record['labels'][lang]['pref'])]})"
            for lang in ("en", "pt-BR")
            if (lang, record["labels"][lang]["pref"]) in reserved
        ]
        if clash:
            refused.extend(clash)
            continue
        current.append(record)
        by_id[record["concept_id"]] = record
        for lang in ("en", "pt-BR"):
            reserved[(lang, record["labels"][lang]["pref"])] = record["concept_id"]
        added.append(record["concept_id"])

    current.sort(key=lambda record: record["concept_id"])
    for record in current:
        record["registry_version"] = args.registry_version

    # Any surface that would resolve automatically to two concepts is ambiguous, not
    # automatic: demote every side. The registry contract rejects the collision outright.
    owners: dict[tuple[str, str], set[str]] = {}
    for record in current:
        for lang, entries in record["lexical_forms"].items():
            for item in entries:
                if item["policy"] == "auto":
                    owners.setdefault((lang, item["form"]), set()).add(record["concept_id"])
    ambiguous = {key for key, ids in owners.items() if len(ids) > 1}
    demoted = []
    for record in current:
        for lang, entries in record["lexical_forms"].items():
            for item in entries:
                if (lang, item["form"]) in ambiguous and item["policy"] == "auto":
                    item["policy"] = "review"
                    demoted.append(f"{record['concept_id']}.{lang}:{item['form']}")

    print(json.dumps({
        "batch": batch["batch"], "added": len(added), "refused": refused,
        "demoted_to_review": demoted, "registry_total": len(current),
    }, ensure_ascii=False))
    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    event_id = f"cga-domain-batch-{batch['batch']:02d}-to-{args.registry_version}"
    events = [
        line for line in PROVENANCE.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if any(json.loads(line)["event_id"] == event_id for line in events):
        raise SystemExit(f"provenance already records {event_id!r}; the ledger is append-only")

    REGISTRY.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in current) + "\n",
        encoding="utf-8",
    )
    events.append(json.dumps({
        "provenance_version": "1.0.0",
        "event_id": event_id,
        "event_type": "adjudicated_domain_batch",
        "recorded_at": RECORDED_AT,
        "source": {
            "historical_path": str(Path(args.batch).name),
            "corpus_sha256": batch["corpus_sha256"],
            "record_count": len(batch["concepts"]),
        },
        "target": {"path": "src/semantic_normalizer/data/registry.jsonl",
                   "registry_version": args.registry_version, "record_count": len(current)},
        "mapping": {"added_records": len(added), "refused": refused,
                    "demoted_to_review": demoted},
        "authority": "docs/plan-cga-domain-lexicon-0.4.0.md",
        "importer": "scripts/import_cga_batch.py",
        "license": "MIT; definitions authored for this project, no third-party text embedded",
    }, ensure_ascii=False, sort_keys=True))
    PROVENANCE.write_text("\n".join(events) + "\n", encoding="utf-8")

    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()  # noqa: E731
    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    release.update({
        "version": args.registry_version,
        "approved_by": "cga-game-delivery-council execution; batch adjudicated against corpus contexts",
        "approved_at": RECORDED_AT,
        "change_reason": f"CGA domain batch {batch['batch']}: {len(added)} concepts added.",
        "affected_concepts": sorted(r["concept_id"] for r in current),
        "added_concepts": sorted(added),
        "reindex_required": True,
        "rollback_version": "2.1.0",
    })
    release["hashes"] = {**release.get("hashes", {}),
                         "registry.jsonl": sha(REGISTRY),
                         "registry.schema.json": sha(SCHEMA),
                         "registry.provenance.jsonl": sha(PROVENANCE)}
    RELEASE.write_text(json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    print(f"written: {REGISTRY.name}, {RELEASE.name}, {PROVENANCE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
