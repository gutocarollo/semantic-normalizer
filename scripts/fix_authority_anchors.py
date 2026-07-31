#!/usr/bin/env python3
"""Point every `authority` anchor at the corpus file that actually contains the term.

An adversarial review found that all fifty concepts added in the precision campaign carry an
anchor whose fragment does not exist. The definitions are sound and the terms are real, but the
citations were written from a chapter numbering invented at authoring time — `08-matematica-
financeira`, `13-avaliacao-de-desempenho` — while the corpus files are `08-gestao-de-carteiras-
de-renda-fixa` and `07-avaliacao-de-desempenho`. A citation that does not resolve is worse than
no citation, because it reads as verified and is not.

The repair is mechanical rather than editorial, which is the point: for each concept, find the
corpus file where its labels actually occur and cite that. Authoring the anchor by hand is what
produced the defect, so this derives it from the corpus and prints what it could not resolve
instead of guessing.

Ties are broken by occurrence count, then by filename, so the result is deterministic. A concept
whose labels appear nowhere gets no anchor and is reported — that is a finding about the
concept, not something to paper over with a plausible-looking file name.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DATA = ROOT / "src" / "semantic_normalizer" / "data"
REGISTRY = DATA / "registry.jsonl"


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    files = {}
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        stem = Path(path).stem
        if stem == "index":
            continue
        files[stem] = fold(Path(path).read_text(encoding="utf-8", errors="replace"))

    records = [
        json.loads(line)
        for line in REGISTRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    rewritten, unresolved, already_valid = [], [], 0
    for record in records:
        authority = record["authority"]
        if not authority.startswith("apostila-cga-2026"):
            continue
        fragment = authority.split("#", 1)[1] if "#" in authority else None
        if fragment in files:
            already_valid += 1
            continue

        # Count occurrences of every label of the concept, in both languages, per file. A whole
        # label rather than a token: `yield to worst` locates the chapter, `yield` does not.
        counts: collections.Counter[str] = collections.Counter()
        labels = {
            entry["form"]
            for language in ("en", "pt-BR")
            for entry in record["lexical_forms"][language]
        }
        for label in labels:
            needle = fold(label)
            if len(needle) < 3:
                continue
            pattern = re.compile(rf"(?<![a-zà-ÿ0-9]){re.escape(needle)}(?![a-zà-ÿ0-9])")
            for stem, text in files.items():
                found = len(pattern.findall(text))
                if found:
                    counts[stem] += found

        if not counts:
            unresolved.append(record["concept_id"])
            continue
        best = min(counts.items(), key=lambda item: (-item[1], item[0]))
        record["authority"] = f"apostila-cga-2026#{best[0]}"
        rewritten.append({
            "concept_id": record["concept_id"],
            "from": authority,
            "to": record["authority"],
            "occurrences_in_file": best[1],
        })

    print(json.dumps({
        "already_valid": already_valid,
        "rewritten": len(rewritten),
        "unresolved": unresolved,
        "sample": rewritten[:6],
    }, ensure_ascii=False, indent=2))

    if unresolved:
        print(f"\n{len(unresolved)} concept(s) have no label anywhere in the corpus. Their "
              "anchors are left alone: an unresolvable anchor is a fact about the concept.")
    if not args.apply:
        print("\ndry-run: nothing written (pass --apply)")
        return 0

    REGISTRY.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    print(f"\nwritten: {REGISTRY.relative_to(ROOT)}")

    # Reseal. This script rewrites a file whose hash the release record declares, and until now it
    # stopped at the write — so every `--apply` left the tree failing four governance tests with a
    # seal pointing at the previous bytes, and the only way back was to run an unrelated amendment
    # for its reseal side effect. Same reasoning as the reseal in `amend_registry.py`: reseal EVERY
    # declared file rather than the one this script touched, because a seal that covers only what
    # the sealer edited lets a file changed by another path keep a stale hash indefinitely.
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
        raise SystemExit(
            f"release declares hashes for files that do not exist: {missing}. "
            "A seal naming an absent file is worse than no seal."
        )
    release["hashes"] = resealed
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"resealed: {release_path.relative_to(ROOT)} ({len(resealed)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
