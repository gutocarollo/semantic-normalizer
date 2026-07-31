#!/usr/bin/env python3
"""Measure every form held at `review`, so the 50 % rule covers all of them and not just ten.

The campaign converged on a rule — demote a form when it is wrong in half its occurrences or
more, keep it and forbid the wrong collocations when it is wrong less often — and then applied
it to the ten forms an exhaustive sweep had measured. An adversarial review pointed out that
196 forms sit at `review` and the other 186 have no `wrong/total` anywhere, so the rule creates
an obligation the campaign left unpaid. That backlog is where the remaining recall lives.

A form can be at `review` for three different reasons and they are not equally justified:

  contested   another concept claims the same surface, so the collision demoter sent both down.
              Correct: the surface really is ambiguous, and review is the designed answer.
  observed    the importer files inflections under `observed`, which is `review` by contract.
              A generator must not emit them; the MATCHER has no reason to refuse them.
  measured    an exhaustive sweep found it wrong often enough to demote. Correct by measurement.

Only the first and third are earned. The second is a side effect of one policy field serving two
different questions — what a generator may produce, and what a matcher may accept — and every
uncontested `observed` form with corpus occurrences is recall given away for nothing.

This counts each form's corpus occurrences and reports what promoting the uncontested ones would
recover, so the decision is made against a number rather than against the shape of the registry.
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT.parent / "cga-2026-markdown"
DEFAULT_OUTPUT = ROOT / "reports" / "review-backlog.json"


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from semantic_normalizer.registry import load_registry

    registry = load_registry()
    records = registry["records"]

    claimants: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for record in records:
        for language, entries in record["lexical_forms"].items():
            for entry in entries:
                claimants[(language, fold(entry["form"]))].add(record["concept_id"])

    # Forms an amendment demoted with a measured wrong/total. Read from the adjudication record
    # rather than hard-coded, so this cannot drift from what was actually measured.
    adjudication = json.loads((ROOT / "reports" / "sweep-adjudication.json").read_text(encoding="utf-8"))
    measured = {
        (item["concept_id"], fold(item["form"])): item
        for item in adjudication["errors_found_and_repaired"]
    }

    text = fold(" ".join(
        Path(path).read_text(encoding="utf-8", errors="replace")
        for path in sorted(glob.glob(str(Path(args.corpus) / "*.md")))
    ))

    rows = []
    for record in records:
        for language, entries in record["lexical_forms"].items():
            labels = record["labels"][language]
            for entry in entries:
                if entry["policy"] != "review":
                    continue
                key = (language, fold(entry["form"]))
                others = claimants[key] - {record["concept_id"]}
                pattern = re.compile(
                    rf"(?<![a-zà-ÿ0-9]){re.escape(fold(entry['form']))}(?![a-zà-ÿ0-9])"
                )
                occurrences = len(pattern.findall(text))
                adjudicated = measured.get((record["concept_id"], fold(entry["form"])))
                if others:
                    reason = "contested"
                elif adjudicated:
                    reason = "measured"
                elif entry["form"] in labels["observed"]:
                    reason = "observed"
                else:
                    reason = "unexplained"
                rows.append({
                    "concept_id": record["concept_id"], "language": language,
                    "form": entry["form"], "reason": reason,
                    "contested_by": sorted(others),
                    "corpus_occurrences": occurrences,
                    "measured_wrong": adjudicated["wrong"] if adjudicated else None,
                    "measured_total": adjudicated["total"] if adjudicated else None,
                })

    by_reason = collections.Counter(row["reason"] for row in rows)
    recoverable = [
        row for row in rows
        if row["reason"] in {"observed", "unexplained"} and row["corpus_occurrences"] > 0
    ]
    recoverable.sort(key=lambda row: -row["corpus_occurrences"])

    report = {
        "schema_version": "review-backlog-v1",
        "registry": {"version": registry["version"], "concepts": len(records)},
        "review_forms": len(rows),
        "by_reason": dict(sorted(by_reason.items())),
        "recoverable_forms": len(recoverable),
        "recoverable_occurrences": sum(row["corpus_occurrences"] for row in recoverable),
        "note": "`recoverable` counts raw corpus occurrences of the surface, which is an upper "
                "bound: a longer form may already consume some of them, and each candidate still "
                "needs its own sweep before promotion. It sizes the gap; it does not authorise "
                "closing it blindly.",
        "recoverable": recoverable,
        "all": rows,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("review_forms", "by_reason", "recoverable_forms",
                       "recoverable_occurrences")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
