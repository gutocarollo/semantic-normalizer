"""Entry point so `python -m semantic_normalizer` keeps working."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
