#!/usr/bin/env python3
"""Every item a generator proposed must have a recorded disposition. Fails on the orphans.

The gap this closes was found by a question rather than by a test: *is there a deterministic
mechanism to verify completeness?* There was not, and the repository proved it on the spot —
`reports/wordnet-surfaces-reasoning.json` sat with ten proposals nobody had ruled on, and the
whole suite plus `make deliver` were green. Stopping halfway is invisible to a suite that only
checks invariants.

WHAT IS DECIDABLE HERE AND WHAT IS NOT

Not decidable: whether the dictionary covers the domain. The true vocabulary is unknown, so any
"100 % coverage" claim would be the metric measuring itself. That is why the run report publishes
a CEILING — what was left and why — instead of a completeness figure.

Decidable, and what this checks: whether every item some generator emitted has been dispositioned.
A queue is a promise. This turns the promise into something that fails.

THE SHAPE, BORROWED FROM THE ONE CHECK THAT ALREADY WORKED

`derive_antonyms.py --check` derives from the registry which oppositions the guard SHOULD carry,
compares against what it DOES carry, and exits non-zero on the difference. That is the whole
pattern: derive the expectation from data, compare with the artefact, fail on the gap. This
generalises it from one property to every queue.

DISPOSITIONS, A CLOSED SET

    admitted   — it entered the registry. The named amendment or batch must EXIST, and must
                 mention the item; a disposition pointing at a file that does not exist is worse
                 than no disposition, because it reads as done.
    rejected   — ruled out, with a reason. Reason is required: "rejected" alone is a shrug.
    hierarchy  — it is a relation, not the thing the queue was about; it moves to another queue,
                 which must itself be dispositioned.
    pending    — still open, WITH an owner and a reason. Legal, and deliberately noisy: pending
                 items are counted and printed on every run so they cannot quietly become
                 permanent.

An item with no entry at all is an ORPHAN and fails the check. That is the only hard failure,
because it is the only case where nobody decided anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIG = ROOT / "config"

VERDICTS = ("admitted", "rejected", "hierarchy", "pending")

# Each queue names: the report that holds it, the field with the items, and how to read an item's
# identity. Registered explicitly rather than sniffed, so adding a generator without registering
# its queue is a visible omission in this file instead of a silent pass.
QUEUES = {
    "redundant-concepts-cga": {
        "report": "redundant-concepts-cga.json",
        "field": "needs_judgement",
        "identity": lambda item: " ~ ".join(item["concepts"]),
        "about": "concept pairs a substitution test could not separate; judged elsewhere",
    },
    "wordnet-surfaces-reasoning": {
        "report": "wordnet-surfaces-reasoning.json",
        "field": "items",
        "identity": lambda item: f"{item['concept_id']} + {item['proposed_surface']}",
        "about": "spellings OpenWordnet-PT proposed for existing concepts",
    },
    "hierarchy-proposals": {
        "report": "hierarchy-proposals.json",
        "field": "edges",
        "identity": lambda item: f"{item['narrower']} -> {item['broader']}",
        "about": "broader/narrower edges proposed by containment, WordNet and subsumption",
    },
}


def load_queue(name: str, spec: dict) -> list[str] | None:
    path = REPORTS / spec["report"]
    if not path.exists():
        return None
    report = json.loads(path.read_text(encoding="utf-8"))
    return [spec["identity"](item) for item in report.get(spec["field"], [])]


def load_dispositions(name: str) -> dict[str, dict] | None:
    path = REPORTS / f"{name}-disposition.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["item"]: row for row in payload["dispositions"]}


def check_one(name: str, spec: dict) -> dict:
    items = load_queue(name, spec)
    if items is None:
        return {"queue": name, "state": "no-report",
                "detail": f"{spec['report']} is not present; nothing to disposition"}
    dispositions = load_dispositions(name)
    if dispositions is None:
        return {"queue": name, "state": "no-disposition-file", "items": len(items),
                "detail": f"reports/{name}-disposition.json is missing; all {len(items)} "
                          "items are orphans"}

    orphans = [item for item in items if item not in dispositions]
    stale = [item for item in dispositions if item not in items]
    bad_verdict, missing_reason, dangling = [], [], []
    counts: dict[str, int] = {}
    for item, row in sorted(dispositions.items()):
        verdict = row.get("verdict")
        counts[verdict] = counts.get(verdict, 0) + 1
        if verdict not in VERDICTS:
            bad_verdict.append(f"{item}: {verdict!r}")
            continue
        if not str(row.get("reason", "")).strip():
            missing_reason.append(item)
        if verdict == "pending" and not str(row.get("owner", "")).strip():
            missing_reason.append(f"{item} (pending without an owner)")
        for reference in row.get("applied_in", []):
            if not (CONFIG / reference).is_file():
                dangling.append(f"{item} -> config/{reference}")
    return {
        "queue": name, "state": "checked", "items": len(items),
        "by_verdict": dict(sorted(counts.items())),
        "orphans": orphans, "stale": stale,
        "invalid_verdict": bad_verdict, "missing_reason": missing_reason,
        "dangling_reference": dangling,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [check_one(name, spec) for name, spec in sorted(QUEUES.items())]
    failures = []
    for result in results:
        if result["state"] == "no-disposition-file":
            failures.append(f"{result['queue']}: {result['detail']}")
            continue
        if result["state"] != "checked":
            continue
        for field, label in (("orphans", "no disposition"),
                             ("invalid_verdict", "verdict outside the closed set"),
                             ("missing_reason", "no reason (or pending with no owner)"),
                             ("dangling_reference", "points at a config file that does not exist")):
            for item in result[field]:
                failures.append(f"{result['queue']}: {item} — {label}")

    pending = sum(r.get("by_verdict", {}).get("pending", 0) for r in results)
    summary = {
        "queues": results,
        "failures": len(failures),
        "still_pending": pending,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for result in results:
            if result["state"] != "checked":
                print(f"  {result['queue']}: {result['state']} — {result['detail']}")
                continue
            print(f"  {result['queue']}: {result['items']} items, {result['by_verdict']}")
        if pending:
            print(f"\n  {pending} item(s) still pending — declared, with an owner, and counted "
                  "here on every run so they cannot become permanent quietly.")
    if failures:
        print("\nqueue-disposition: FAIL", file=sys.stderr)
        for failure in failures[:40]:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("\nqueue-disposition: every proposed item has a recorded disposition")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
