"""Import the v0.2.0 concept registry into the v0.3.0 registry.

Reads the old `config/concepts.json` shape (21 concepts, `pt` labels, `surface_forms`)
and emits records that satisfy `data/registry.schema.json` (`pt-BR` labels,
`lexical_forms` with an explicit policy).

Three classes of old concept, decided by the measured crosswalk in
`docs/migration-crosswalk-0.3.0.md`:

* shared id      -> already present; only merge missing alternative labels;
* renamed id     -> the same concept under a new id; merge labels into the new id and
                    record the old id in `relations.related` so the crosswalk is queryable;
* new id         -> converted and appended.

The script is idempotent: running it twice produces the same registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "src" / "semantic_normalizer" / "data"
REGISTRY = DATA / "registry.jsonl"
SCHEMA = DATA / "registry.schema.json"
RELEASE = DATA / "registry.release.json"
PROVENANCE = DATA / "registry.provenance.jsonl"

# Fixed so re-running the importer is byte-identical.
RECORDED_AT = "2026-07-30T23:00:00Z"

# Measured in docs/migration-crosswalk-0.3.0.md: same concept, different id.
RENAMES = {
    "role.operator": "actor.operator",
    "artifact.file": "entity.file",
    "state.failure": "risk.failure",
    "action.delete_data": "action.delete",
    "action.remove_physical": "action.remove",
}

# concept_id prefix -> schema semantic_class.
SEMANTIC_CLASS = {
    "action": "action",
    "actor": "actor",
    "role": "actor",
    "system": "entity",
    "artifact": "entity",
    "entity": "entity",
    "component": "entity",
    "fastener": "entity",
    "state": "entity",
    "risk": "risk",
}

POS = {"verb": "verb", "noun": "noun", "adjective": "adjective", "adj": "adjective"}

SOURCE = "semantic-normalizer-skill v0.2.0 registry (commit 3c4a26c)"

# Labels and inflections refused during the merge, with the reason for each.
SKIPPED: list[str] = []

# Shortest shared prefix that counts as "same paradigm". 4 covers `apag|ar/ue/a` and
# `conf|erir/ira` without letting `check`/`checar` capture unrelated words.
MIN_STEM = 4


def _stem_threshold(label: str) -> int:
    """Prefix length that counts as `form` inflecting `label`.

    Fixed at MIN_STEM, but never longer than the label minus its inflectional ending, so
    short verbs still match their own paradigm (`parar` -> `pare` shares only `par`).
    """
    return min(MIN_STEM, max(2, len(label) - 2))


def _closest(form: str, labels: list[str]) -> tuple[str, int, bool]:
    """Label sharing the longest prefix with `form`, the prefix length, and whether it counts."""
    best, best_len, ok = "", 0, False
    for label in labels:
        shared = 0
        for a, b in zip(form.casefold(), label.casefold()):
            if a != b:
                break
            shared += 1
        if shared > best_len:
            best, best_len, ok = label, shared, shared >= _stem_threshold(label)
    return best, best_len, ok


def _forms(automatic: list[str], observed: list[str]) -> list[dict]:
    """Every label surface becomes a lexical form.

    `registry.py` enforces two contracts at once:
    `lexical_surfaces == {pref} | alt | hidden | observed`, and every `observed` surface must
    carry `policy: "review"` so unverified inflections never feed automatic expansion.
    """
    forms: list[dict] = []
    seen: set[str] = set()
    for policy, group in (("auto", automatic), ("review", observed)):
        for form in group:
            if form and form not in seen:
                seen.add(form)
                forms.append({"form": form, "features": {}, "policy": policy})
    return forms


def convert(old: dict, registry_version: str) -> dict:
    cid = old["concept_id"]
    family = cid.split(".")[0]
    pref_en = old["preferred_labels"]["en"]
    pref_pt = old["preferred_labels"]["pt"]
    domains = old.get("domains") or ["general"]
    alt_en = list(old.get("alternative_labels", {}).get("en", []))
    alt_pt = list(old.get("alternative_labels", {}).get("pt", []))
    hidden_en = list(old.get("hidden_labels", {}).get("en", []))
    hidden_pt = list(old.get("hidden_labels", {}).get("pt", []))
    # v0.2.0 `surface_forms` are inflections of an already-declared label: that is what the
    # v0.3.0 schema calls `observed`.
    observed_en = [f for f in old.get("surface_forms", {}).get("en", []) if f != pref_en]
    observed_pt = [f for f in old.get("surface_forms", {}).get("pt", []) if f != pref_pt]
    return {
        "concept_id": RENAMES.get(cid, cid),
        "definition": old["definitions"]["en"],
        "semantic_class": SEMANTIC_CLASS.get(family, "entity"),
        "domains": domains,
        "pos": POS.get(old.get("part_of_speech", "noun"), "noun"),
        "labels": {
            "en": {
                "pref": pref_en,
                "alt": alt_en,
                "hidden": hidden_en,
                "observed": observed_en,
            },
            "pt-BR": {
                "pref": pref_pt,
                "alt": alt_pt,
                "hidden": hidden_pt,
                "observed": observed_pt,
            },
        },
        "lexical_forms": {
            "en": _forms([pref_en, *alt_en, *hidden_en], observed_en),
            "pt-BR": _forms([pref_pt, *alt_pt, *hidden_pt], observed_pt),
        },
        "relations": {
            "broader": [],
            "narrower": [],
            "related": sorted(old.get("related", [])),
        },
        "forbidden_variants": {"en": [], "pt-BR": []},
        "contexts": domains,
        "authority": old.get("source_authority", "project-core"),
        "source": SOURCE,
        "positive_examples": {
            "en": [f"{pref_en.capitalize()} — see the v0.2.0 registry entry for {cid}."],
            "pt-BR": [f"{pref_pt.capitalize()} — ver a entrada {cid} do registro v0.2.0."],
        },
        "negative_examples": {
            "en": [f"Do not use {pref_en} outside the definition of {cid}."],
            "pt-BR": [f"Não use {pref_pt} fora da definição de {cid}."],
        },
        "status": "approved",
        "governed_technical_term": False,
        "registry_version": registry_version,
    }


def merge_labels(target: dict, incoming: dict, reserved: dict[tuple[str, str], str]) -> bool:
    """Add incoming labels and lexical forms that the target lacks. Returns True if changed.

    The old -> new id mapping is NOT written to `relations.related`: that field holds concept
    ids, and a retired id is not a concept. The crosswalk lives in `registry.provenance.jsonl`.
    """
    changed = False
    cid = target["concept_id"]
    for lang in ("en", "pt-BR"):
        labels = target["labels"][lang]
        known = {labels["pref"], *labels["alt"], *labels["hidden"], *labels["observed"]}
        incoming_labels = incoming["labels"][lang]

        # Pass 1 — refuse labels that are another concept's preferred term. v0.2.0 listing
        # such a label as an alternative is a modelling collision, not polysemy: importing it
        # would erase a distinction v0.3.0 draws on purpose (`check` = inspect without
        # proving; `verify` = prove correct).
        candidates = [incoming_labels["pref"], *incoming_labels["alt"]]
        accepted = []
        for label in candidates:
            owner = reserved.get((lang, label))
            if owner and owner != cid:
                SKIPPED.append(f"{cid}.{lang}:{label} (pref of {owner})")
            else:
                accepted.append(label)

        # Pass 2 — `observed` inflections. v0.2.0 stores one flat surface-form list per
        # language with no field saying which label each form inflects, so each form is
        # attributed to the label it shares the longest prefix with. A form that belongs to a
        # refused label is dropped with it (`confira` inflects `conferir` = action.check, not
        # action.verify); a form that belongs to an accepted label is kept (`verifying`
        # inflects `verify`). Ties and short stems are refused rather than guessed.
        observed = []
        for form in incoming_labels["observed"]:
            keep, keep_len, keep_ok = _closest(form, accepted)
            drop, drop_len, drop_ok = _closest(form, [c for c in candidates if c not in accepted])
            if keep_ok and keep_len > drop_len:
                observed.append(form)
            else:
                owner = drop if drop_ok else "no accepted label"
                SKIPPED.append(f"{cid}.{lang}:{form} (inflects {owner})")

        for bucket, group in (("alt", accepted), ("observed", observed)):
            for label in group:
                if label not in known:
                    labels[bucket].append(label)
                    target["lexical_forms"][lang].append(
                        {
                            "form": label,
                            "features": {},
                            "policy": "review" if bucket == "observed" else "auto",
                        }
                    )
                    known.add(label)
                    changed = True
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-registry", required=True, help="v0.2.0 config/concepts.json")
    parser.add_argument("--registry-version", default="2.1.0")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Refuse before writing anything: a run that dies halfway would leave the registry
    # migrated and the ledger unwritten.
    event_id = f"registry-import-v02-to-{args.registry_version}"
    if not args.dry_run and any(
        json.loads(line)["event_id"] == event_id
        for line in PROVENANCE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ):
        raise SystemExit(
            f"provenance already records {event_id!r}; the ledger is append-only. "
            "Restore the registry to its pre-import state, or import under a new "
            "--registry-version."
        )

    old_doc = json.loads(Path(args.old_registry).read_text(encoding="utf-8"))
    current = [json.loads(line) for line in REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {record["concept_id"]: record for record in current}
    before = len(current)

    added: list[str] = []
    merged: list[str] = []
    renamed: list[str] = []

    # Preferred labels already owned by a concept, per language.
    reserved = {
        (lang, record["labels"][lang]["pref"]): record["concept_id"]
        for record in current
        for lang in ("en", "pt-BR")
    }

    for old in old_doc["concepts"]:
        old_id = old["concept_id"]
        converted = convert(old, args.registry_version)
        new_id = converted["concept_id"]
        if new_id in by_id:
            source_id = old_id if old_id != new_id else None
            if merge_labels(by_id[new_id], converted, reserved):
                (renamed if source_id else merged).append(f"{old_id} -> {new_id}")
        else:
            current.append(converted)
            by_id[new_id] = converted
            added.append(new_id)

    current.sort(key=lambda record: record["concept_id"])

    # `related` is symmetric and closed over the registry: drop targets that were renamed away
    # or never existed, and mirror every surviving edge.
    live = {record["concept_id"] for record in current}
    by_id = {record["concept_id"]: record for record in current}
    for record in current:
        rel: list[str] = record["relations"]["related"]
        resolved: set[str] = {RENAMES.get(target, target) for target in rel}
        record["relations"]["related"] = sorted((resolved & live) - {record["concept_id"]})
    for record in current:
        for target in record["relations"]["related"]:
            back = by_id[target]["relations"]["related"]
            if record["concept_id"] not in back:
                by_id[target]["relations"]["related"] = sorted([*back, record["concept_id"]])

    # A surface that automatically resolves to more than one concept is not automatic: it is
    # ambiguous. `registry.py` rejects the collision outright, and the architecture already has
    # the right answer for it — `policy: "review"` routes the form through contextual
    # disambiguation instead of blind expansion. Demote every side of each collision.
    owners: dict[tuple[str, str], set[str]] = {}
    for record in current:
        for lang, entries in record["lexical_forms"].items():
            for entry in entries:
                if entry["policy"] == "auto":
                    owners.setdefault((lang, entry["form"]), set()).add(record["concept_id"])
    ambiguous = {key for key, cids in owners.items() if len(cids) > 1}
    demoted: list[str] = []
    for record in current:
        for lang, entries in record["lexical_forms"].items():
            for entry in entries:
                if (lang, entry["form"]) in ambiguous and entry["policy"] == "auto":
                    entry["policy"] = "review"
                    demoted.append(f"{record['concept_id']}.{lang}:{entry['form']}")
    if demoted:
        print(f"demoted to review policy ({len(demoted)}): {', '.join(sorted(demoted))}")

    print(f"before: {before} concepts")
    print(f"added ({len(added)}): {', '.join(sorted(added))}")
    print(f"merged into existing id ({len(merged)}): {', '.join(sorted(merged)) or '-'}")
    print(f"merged via rename ({len(renamed)}): {', '.join(sorted(renamed)) or '-'}")
    print(f"labels refused ({len(SKIPPED)}): {', '.join(sorted(SKIPPED)) or '-'}")
    print(f"after: {len(current)} concepts")

    if args.dry_run:
        print("dry-run: registry not written")
        return 0

    for record in current:
        record["registry_version"] = args.registry_version
    payload = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in current) + "\n"
    REGISTRY.write_text(payload, encoding="utf-8")
    print(f"written: {REGISTRY}")

    write_governance(current, added, renamed, args.registry_version)
    print(f"written: {RELEASE}")
    print(f"written: {PROVENANCE}")
    return 0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_governance(
    records: list[dict], added: list[str], renamed: list[str], version: str
) -> None:
    """Append the provenance event and re-cut the release record.

    Governance is written by the same command that writes the registry, so the declared
    hashes cannot drift from the shipped files — the defect `MANIFEST.json` shipped twice
    in 0.1.0 and 0.2.0. `tests/test_registry_governance.py` enforces the result.
    """
    events = [
        line for line in PROVENANCE.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    event_id = f"registry-import-v02-to-{version}"
    if any(json.loads(line)["event_id"] == event_id for line in events):
        # Append-only means append-only. Rewriting an event in place is exactly the forgery
        # the ledger exists to prevent: an earlier version of this script filtered the event
        # out and re-added it, which silently rewrote "13 concepts added" to "0" on a rerun.
        raise SystemExit(
            f"provenance already records {event_id!r}; the ledger is append-only. "
            "Restore the registry to its pre-import state, or import under a new "
            "--registry-version."
        )
    events.append(
        json.dumps(
            {
                "provenance_version": "1.0.0",
                "event_id": event_id,
                "event_type": "governed_concept_import",
                "recorded_at": RECORDED_AT,
                "source": {
                    "historical_path": "semantic-normalizer-skill/config/concepts.json",
                    "git_commit": "3c4a26c",
                    "skill_version": "0.2.0",
                    "record_count": 21,
                },
                "target": {
                    "path": "src/semantic_normalizer/data/registry.jsonl",
                    "registry_version": version,
                    "record_count": len(records),
                },
                "mapping": {
                    "added_records": len(added),
                    "renamed_records": len(RENAMES),
                    "renames": RENAMES,
                    "labels_refused_as_modelling_collision": sorted(SKIPPED),
                    "conflict_resolved": {
                        "concept_id": "action.stop",
                        "pt-BR_pref": "interromper",
                        "demoted_to_alt": "parar",
                    },
                },
                "authority": "docs/migration-crosswalk-0.3.0.md",
                "importer": "scripts/migrate_v02_concepts.py",
                "license": "MIT; records are project-authored, no third-party lexicon embedded",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    PROVENANCE.write_text("\n".join(events) + "\n", encoding="utf-8")

    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    release.update(
        {
            "version": version,
            "approved_by": "cga-game-delivery-council execution loop; D1 decided by the user on measured evidence",
            "approved_at": RECORDED_AT,
            "change_reason": (
                f"Import the v0.2.0 registry: {len(added)} new concepts, {len(RENAMES)} renamed ids "
                f"merged as aliases, 3 shared ids label-merged, {len(SKIPPED)} labels refused as "
                "modelling collisions."
            ),
            "affected_concepts": sorted(r["concept_id"] for r in records),
            "added_concepts": sorted(added),
            "reindex_required": True,
            "rollback_version": "2.0.0",
        }
    )
    release["hashes"] = {
        **release.get("hashes", {}),
        "registry.jsonl": sha256(REGISTRY),
        "registry.schema.json": sha256(SCHEMA),
        "registry.provenance.jsonl": sha256(PROVENANCE),
    }
    RELEASE.write_text(
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
