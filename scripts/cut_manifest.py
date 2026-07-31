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
SKIP_DIRS = {".git", "__pycache__", "dist", "exports", ".venv", ".pytest_cache"}
SKIP_NAMES = {"MANIFEST.json", ".DS_Store", ".manifest.before"}


def main() -> int:
    files = sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRS for part in path.parts)
        and path.name not in SKIP_NAMES
        and path.suffix != ".pyc"
    )
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
