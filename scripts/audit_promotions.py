#!/usr/bin/env python3
"""Read every occurrence of every form this campaign promoted, so the 50 % rule has counts.

Phase two promoted thirty forms from `review` to `auto` and justified each one morphologically:
"Plural of `serviço`", "Nominalisation of `investir`". Eleven carried no justification at all.
An adversarial review named it for what it is — the identical defect I had just removed from the
`vendido` guard entry in the same phase, where a form was rejected for RESEMBLING one that had
been measured. Rejecting by resemblance costs recall; promoting by resemblance costs precision;
neither is a measurement.

The review falsified it by contraexample immediately: `serviços` was promoted as "plural of
`serviço`" onto a concept defined as *a software process*, and all 24 of its corpus occurrences
are financial or legal services.

So this reads them. For each promoted form it emits every corpus occurrence with its sentence,
grouped by form, so the 50 % rule can be applied to a count rather than to a part of speech. It
does not decide — a script cannot tell whether `investimento` in `Política de Investimentos`
means committing capital. It puts the evidence where the decision has to be made.
"""

from __future__ import annotations

import argparse
import collections
import glob
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "promotion-audit.json"
CONFIG = ROOT / "config"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location("build_oov_queue", ROOT / "scripts" / "build_oov_queue.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    from semantic_normalizer.normalizer import normalize_text
    from semantic_normalizer.registry import load_lexicon, load_registry

    # Every promotion this campaign performed, read from the amendments rather than listed here,
    # so the audit cannot drift from what was actually applied.
    promoted: dict[tuple[str, str], dict] = {}
    for path in sorted(CONFIG.glob("precision-amendment-*.json")):
        amendment = json.loads(path.read_text(encoding="utf-8"))
        for operation in amendment["operations"]:
            if operation.get("op") != "promote":
                continue
            promoted[(operation["concept"], operation["form"])] = {
                "amendment": amendment["id"],
                "declared_right": operation.get("right"),
                "declared_total": operation.get("total"),
                "why": operation.get("why"),
            }

    registry = load_registry()
    definitions = {record["concept_id"]: record["definition"] for record in registry["records"]}
    lexicon = load_lexicon()

    sentences = []
    for path in sorted(glob.glob(str(Path(args.corpus) / "*.md"))):
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        sentences.extend(builder.sentences_of(builder.strip_math(builder.HTML_COMMENT.sub(" ", raw))))

    occurrences: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for index, sentence in enumerate(sentences):
        for record in normalize_text(sentence, source=f"s{index}", kind="text", lexicon=lexicon):
            for event in record["match_events"]:
                key = (event["concept_id"], event["alias"])
                if key in promoted:
                    start, end = event["start"], event["end"]
                    occurrences[key].append(
                        sentence[max(0, start - 62):end + 54].replace("\n", " ").strip()
                    )

    rows = []
    for key, meta in sorted(promoted.items()):
        found = occurrences.get(key, [])
        rows.append({
            "concept_id": key[0], "form": key[1],
            "definition": definitions.get(key[0], "(concept not in registry)"),
            "amendment": meta["amendment"],
            "declared_right": meta["declared_right"],
            "declared_total": meta["declared_total"],
            "justification_was_morphological": meta["why"] is None or any(
                word in (meta["why"] or "").lower()
                for word in ("plural of", "nominalisation", "nominalization", "inflection")
            ),
            "corpus_occurrences": len(found),
            "occurrences": found,
        })
    rows.sort(key=lambda row: -row["corpus_occurrences"])

    unjustified = [row for row in rows if row["justification_was_morphological"]]
    report = {
        "schema_version": "promotion-audit-v1",
        "registry": {"version": registry["version"], "concepts": len(registry["records"])},
        "promotions": len(rows),
        "promotions_justified_morphologically": len(unjustified),
        "occurrences_to_read": sum(row["corpus_occurrences"] for row in rows),
        "note": "Every occurrence of every promoted form. The 50 % rule needs counts, and a "
                "morphological justification is not a count.",
        "forms": rows,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("promotions", "promotions_justified_morphologically",
                       "occurrences_to_read")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
