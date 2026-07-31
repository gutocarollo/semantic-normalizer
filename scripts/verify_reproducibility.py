#!/usr/bin/env python3
"""Prove that a fresh checkout produces byte-identical output to this one.

The registry ships with hashes, the release record seals every file, and the test suite is green —
and none of that proves the thing a consumer actually depends on: that installing this package
somewhere else and running it over the same corpus yields the same annotations. Every guard in this
repo checks an INPUT. This checks the OUTPUT.

It is worth having as a script rather than as a one-off command because the failure it catches is
silent. A change to matching order, to `dict` iteration, to how a report is serialised, or to a
default argument would leave every existing test green while altering what downstream consumers
index. The first symptom would be a search that stopped finding something, months later.

Two modes:

    --against <path/to/other/src>   run BOTH installs over the corpus and compare
    (default)                        run this install and print the digest

The digest is over the concatenated JSONL of every corpus file, in sorted filename order, with the
same `source` string passed to both runs — because `source` is echoed into every record, and
comparing two runs that were given different paths would compare the paths rather than the
behaviour.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"


def digest_of(src: Path, corpus: Path, contexts: list[str]) -> tuple[str, int, int]:
    """Normalize every corpus file with the install at `src`; return (sha256, records, files)."""
    chunks: list[bytes] = []
    files = sorted(corpus.glob("*.md"))
    for path in files:
        result = subprocess.run(
            [sys.executable, "-m", "semantic_normalizer", "normalize", str(path),
             "--contexts", *contexts],
            capture_output=True,
            env={"PYTHONPATH": str(src), "PATH": "/usr/bin:/bin"},
        )
        if result.returncode != 0:
            raise SystemExit(
                f"normalize failed on {path.name} using {src}:\n"
                f"{result.stderr.decode('utf-8', 'replace')[:800]}"
            )
        chunks.append(result.stdout)
    blob = b"".join(chunks)
    records = sum(1 for line in blob.splitlines() if line.strip())
    return hashlib.sha256(blob).hexdigest(), records, len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--against", metavar="SRC",
                        help="path to another checkout's src/ to compare against")
    parser.add_argument("--contexts", nargs="+", default=["core", "cga"])
    parser.add_argument("--expect", help="fail unless the digest equals this sha256")
    args = parser.parse_args()

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        raise SystemExit(
            f"corpus not found at {corpus}. It is not versioned in this repository — point "
            f"--corpus at your copy. Without it this check cannot run, which is a fact about the "
            f"corpus and not a pass."
        )

    mine, records, files = digest_of(ROOT / "src", corpus, args.contexts)
    print(f"this install : {mine}  ({records} records over {files} files)")

    failed = False
    if args.against:
        other, other_records, _ = digest_of(Path(args.against), corpus, args.contexts)
        print(f"other install: {other}  ({other_records} records)")
        if other == mine:
            print("\nIDENTICAL — the two installs produce byte-identical output.")
        else:
            print("\nDIVERGED — same corpus, same flags, different bytes. Something in the "
                  "matching, the registry or the serialisation is not deterministic across "
                  "checkouts.")
            failed = True

    if args.expect:
        if args.expect == mine:
            print(f"\nmatches the expected digest {args.expect}")
        else:
            print(f"\nEXPECTED {args.expect}\nGOT      {mine}")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
