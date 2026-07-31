#!/usr/bin/env python3
"""Compatibility shim for the semantic_normalizer package."""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from semantic_normalizer import *  # noqa: F401,F403,E402
from semantic_normalizer.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
