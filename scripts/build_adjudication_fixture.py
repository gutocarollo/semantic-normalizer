#!/usr/bin/env python3
"""Build a golden-shaped adjudication fixture from real CGA corpus sentences.

Step 3b of `docs/plan-cga-domain-lexicon-0.4.0.md`.

`generate_auto_match_candidates.py` scores precision, and that half is pure reuse. But it only
ever read `golden.jsonl` (software-domain cases) and `dev_retrieval.json` (the project README):
neither contains financial vocabulary, so a batch of domain concepts produced `automatic == 0`
and `evaluate_auto_matches` returned the string `"not_run"` instead of a number. The precision
gate was unevaluable. This script supplies the missing source.

Shape, read from `generate_auto_match_candidates.py`:

    {"id": "gNN", "type": "normalize", "kind": "text",
     "input": "<corpus sentence>",
     "expected": {"contains_concepts": ["<concept_id>"]}}

`expected.contains_concepts` drives the auto-seed: a prediction that is both expected and
unique in a case is credited without human review. Leaving it empty is legitimate and means
every occurrence in that case goes to blind adjudication.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "tests" / "fixtures" / "cga_adjudication.jsonl"
SEED = 7


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--cases", type=int, default=120, help="sentences sampled")
    parser.add_argument("--concepts", nargs="*", default=[],
                        help="concept ids expected in the sample; drives auto-seed")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        "build_oov_queue", ROOT / "scripts" / "build_oov_queue.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon

    lexicon = load_lexicon()
    sentences: list[str] = []
    corpus_hash = hashlib.sha256()
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        corpus_hash.update(Path(path).read_bytes())
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    # Only sentences the normalizer actually matches something in can be adjudicated: a case
    # with no occurrence contributes nothing to precision, it just pads the file.
    scored = []
    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"f{index}", kind="text", lexicon=lexicon):
            found = record.get("concept_ids") or []
            if found:
                scored.append((sentence, found))
                break
    random.seed(SEED)
    sample = random.sample(scored, min(args.cases, len(scored)))

    wanted = set(args.concepts)
    cases = []
    for number, (sentence, found) in enumerate(sample, start=1):
        expected = sorted(set(found) & wanted) if wanted else []
        cases.append({
            "id": f"g{number:03d}",
            "type": "normalize",
            "kind": "text",
            "input": sentence,
            "expected": {"contains_concepts": expected},
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output.relative_to(ROOT)),
        "corpus_sha256": corpus_hash.hexdigest()[:16],
        "sentences_with_a_match": len(scored),
        "cases": len(cases),
        "case_range": [1, len(cases)],
        "auto_seeded_cases": sum(1 for c in cases if c["expected"]["contains_concepts"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
