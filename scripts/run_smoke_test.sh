#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"

python3 -m compileall -q src
python3 -m unittest discover -s tests -v
python3 -m semantic_normalizer validate-registry --output reports/registry-validation.json
python3 -m semantic_normalizer normalize \
  --input examples/sample-input.txt \
  --lang pt \
  --pretty \
  --output reports/sample-output.json
python3 -m semantic_normalizer evaluate \
  --documents examples/documents.jsonl \
  --queries examples/queries.jsonl \
  --k 1 3 \
  --output reports/bm25-smoke-test.json
python3 -m semantic_normalizer export-skos --output exports/concepts.ttl
python3 -m semantic_normalizer export-synonyms --output exports/elasticsearch-synonyms.txt

echo "Smoke test completed."
