#!/usr/bin/env python3
"""Tag the domain-agnostic operators with the `core` context, so every pack inherits them.

`load_registry(contexts=[...])` makes `contexts` a contract: the matcher only sees forms in the
requested scope. That immediately exposed a hole the field had been hiding. Fifty-one concepts are
pure operators — negation, prohibition, conditionals, temporal markers, quantifiers — and they
carry whatever context the batch that happened to import them was about: thirty say `cga/finance`,
fifteen say `controlled_instruction/documentation`, the rest a mixture.

Nothing about `polarity.negation` is financial. But under a scoped load, `contexts=["medicina"]`
would return a pack with no way to express `não`, `exceto`, `desde que` or `vencimento` — the
operators that carry the MEANING of a regulatory sentence, stranded in whichever domain imported
them first. A domain pack without them is not a smaller dictionary, it is a broken one.

So they get a `core` context in addition to what they already have, and a pack is loaded as
`contexts=["core", "<domain>"]`. Adding rather than replacing is deliberate: `contexts=["cga"]`
must keep returning exactly what it returned before this script ran, so the change cannot silently
alter the existing scoped behaviour it was written to enable.

Selection is by concept-id prefix rather than by hand-listing 51 ids, because a hand-list goes
stale the next time a batch adds an operator, and the id prefix IS the registry's own statement
about what kind of thing a concept is. Anything outside those prefixes is left alone and reported.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "semantic_normalizer" / "data"
REGISTRY = DATA / "registry.jsonl"

# The semantic classes that mean the same thing in any domain. `quantity.` is included because its
# members are comparators and measures — `no máximo`, `média`, `diferença` — not domain quantities;
# a financial amount lives under `entity.` or `technical.`.
CORE_PREFIXES = ("polarity.", "condition.", "temporal.", "modality.", "quantity.")
CORE = "core"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    records = [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    tagged, already = [], 0
    before: collections.Counter[tuple[str, ...]] = collections.Counter()
    for record in records:
        if not record["concept_id"].startswith(CORE_PREFIXES):
            continue
        before[tuple(sorted(record["contexts"]))] += 1
        if CORE in record["contexts"]:
            already += 1
            continue
        record["contexts"] = sorted(set(record["contexts"]) | {CORE})
        tagged.append(record["concept_id"])

    print(json.dumps({
        "operators_found": sum(before.values()),
        "already_core": already,
        "newly_tagged": len(tagged),
        "contexts_they_carried_before": {
            " + ".join(key): count for key, count in sorted(before.items())
        },
        "sample": tagged[:8],
    }, ensure_ascii=False, indent=2))

    if not args.apply:
        print("\ndry-run: nothing written (pass --apply)")
        return 0

    REGISTRY.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records)
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwritten: {REGISTRY.relative_to(ROOT)}")

    # Reseal. A previous script in this repo rewrote the registry and left the release record's
    # hashes pointing at the old bytes, so every run left four governance tests red until an
    # unrelated amendment was run for its reseal side effect. Reseal EVERY declared file, not the
    # one this script touched: a seal covering only what the sealer edited lets a file changed by
    # another path keep a stale hash indefinitely.
    release_path = DATA / "registry.release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    resealed, missing = {}, []
    for name in release.get("hashes", {}):
        for base in (DATA, ROOT, ROOT / "src" / "semantic_normalizer"):
            candidate = base / name
            if candidate.is_file():
                resealed[name] = hashlib.sha256(candidate.read_bytes()).hexdigest()
                break
        else:
            missing.append(name)
    if missing:
        raise SystemExit(f"release declares hashes for files that do not exist: {missing}")
    release["hashes"] = resealed
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"resealed: {release_path.relative_to(ROOT)} ({len(resealed)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
