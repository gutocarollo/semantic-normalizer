"""Re-cut MANIFEST.json from the tree that is actually on disk.

The manifest is evidence, not a hand-maintained list: 0.1.0 and 0.2.0 both shipped a hash
that never matched its file because nothing recomputed it. Run this after any change to the
skill's files; `tests/test_manifest_integrity.py` fails until you do.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "MANIFEST.json"
REGISTRY = ROOT / "src" / "semantic_normalizer" / "data" / "registry.jsonl"
# `runs/` holds lexicon_pipeline run directories: task files, model results, per-round snapshots
# and reports. It is gitignored scratch like `dist/` and `exports/`, and hashing it is actively
# circular — an APPLY writes a snapshot COPY of MANIFEST.json into the run directory and then
# re-cuts the manifest, which now hashes its own backup. The result is a manifest that is stale
# the instant it is written and a fail-closed guard that fires on healthy batches. Same failure
# shape as `.manifest.before`, one directory over.
SKIP_DIRS = {".git", "__pycache__", "dist", "exports", ".venv", ".pytest_cache", "runs"}
# `.DS_Store` is noise; the rest are the delivery gate's own scratch copies. It compares each
# generated artifact against a snapshot taken before a second cut, and a snapshot living in the
# tree would be hashed INTO the artifact it is the baseline for — the gate would then fail every
# run on a clean tree. `.manifest.before` was already listed; extending the gate to the release
# manifest and checksums added two more, and naming them one at a time is how the next one gets
# missed, so the rule is the suffix.
SKIP_NAMES = {"MANIFEST.json", ".DS_Store"}
SKIP_SUFFIXES = (".before",)


def is_tracked(path: Path) -> bool:
    """The single predicate for "this file belongs in the manifest".

    Exported so `tests/test_manifest_integrity.py` asks the generator instead of restating the
    rule. The test carried its own copy of the skip lists and they drifted the moment `runs/`
    was excluded here — the manifest correctly stopped describing run directories and the test
    reported them as missing files. Two implementations of one rule is the exact defect that
    test exists to catch, one level up.
    """
    return (
        path.is_file()
        and not any(part in SKIP_DIRS for part in path.parts)
        and path.name not in SKIP_NAMES
        and not path.name.endswith(SKIP_SUFFIXES)
        and path.suffix != ".pyc"
    )


def main() -> int:
    files = sorted(path for path in ROOT.rglob("*") if is_tracked(path))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # Derive the version rather than trusting whatever is in the file: leaving it manual is
    # how MANIFEST.json drifted from the package in the first place.
    manifest["version"] = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    manifest["files"] = [
        {
            "path": str(path.relative_to(ROOT)),
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in files
    ]
    records = [
        line for line in REGISTRY.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    manifest["validation"]["registry_concepts"] = len(records)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"MANIFEST.json: {len(files)} files, {len(records)} concepts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
