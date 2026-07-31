#!/usr/bin/env python3
"""Combine the exhaustive sweep and the residual draws into one precision figure with a bound.

Two kinds of evidence exist and they answer different questions, so neither one alone produces
an honest total.

The **sweep** reads the occurrences a prior×spread ranking puts at the top, and every form where
an error appeared then had all of its occurrences adjudicated one by one. That part is exact:
the counts are of the whole population of those forms, not of a sample of it.

The **residual draws** are uniform samples of the occurrences the ranking dismissed. They cannot
say where errors are — ranking does that better — but they are the only thing that can say how
often the ranking was wrong to dismiss something, which is a rate over a defined population and
exactly what a uniform draw measures.

The total is the weighted combination, and the interval is the Wilson bound on the residual
rate carried through the unread share. Reporting the point estimate alone would repeat the
mistake this project already made once, when sampling reported 95-100 % on batches an
exhaustive sweep scored at 84 %: a number without its uncertainty invites more confidence than
the evidence supports. The lower bound is what a claim should be made against.

Adjudications are declared here, in code, with the seed and sample they came from, because a
count typed into prose is unverifiable. Re-running the samplers with these seeds reproduces the
exact occurrences these numbers describe.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "reports" / "precision-final.json"

# Every residual draw run in this campaign, with the number of wrong events adjudicated in each.
# Each sample was read in full, occurrence by occurrence, against the sentence it came from.
#
# `overwritten: true` marks an entry whose file was later redrawn against a newer registry, so
# the numbers recorded here can no longer be re-derived from that path. An adversarial review
# caught this: the 2.14.1 entry claims two errors and the file at that path is now a 2.15.0 draw
# with none. Redrawing into the same filename destroyed the evidence for a campaign claim, which
# is the same defect as overwriting coverage-baseline.json. Later draws use distinct filenames.
DRAWS = [
    {
        "report": "reports/unread-residual-sample.json", "seed": 20260731, "registry": "2.8.2",
        "errors": 4,
        "found": ["Market in `Market Neutral`", "Price in `Growth at a Reasonable Price`",
                  "Hedge in `Hedge Funds`", "Yield in `High Yield Bonds`"],
        "state": "before the compound-term batches; all four repaired by batches 6 and 7",
    },
    {
        "report": "reports/unread-residual-round2.json", "seed": 913377, "registry": "2.10.0",
        "errors": 2,
        "found": ["ativo in `retorno ativo`", "VaR inside a `\\[...\\]` LaTeX block"],
        "state": "after batches 6 and 7; repaired by batch 8 and by the LaTeX delimiters in build_oov_queue",
    },
    {
        "report": "reports/unread-residual-round3.json", "seed": 55501, "registry": "2.11.0",
        "errors": 1,
        "found": ["Long in `Long and Short`"],
        "state": "after batch 8; repaired by batch 9",
    },
    {
        "report": "reports/unread-residual-final.json", "seed": 88123, "registry": "2.12.0",
        "errors": 1,
        "found": ["Cupom in `Cupom Cambial`"],
        "state": "after batch 9; repaired by batch 10",
    },
    {
        "report": "reports/unread-residual-v2240.json", "seed": 141421356, "registry": "2.24.0",
        "errors": 0,
        "found": [],
        "state": "after the phase-two review: three concepts corrected, the action definitions "
                 "rewritten, 31 CGA concepts harvested",
    },
    {
        "report": "reports/unread-residual-v2211.json", "seed": 57721566, "registry": "2.21.1",
        "errors": 3, "superseded_note": "re-adjudicated after adversarial review: serviços, "
                                        "bolsa de valores, prestadores de serviços",
        "errors": 0,
        "found": [],
        "state": "after phase two, with the review backlog closed and the coverage target audited",
    },
    {
        "report": "reports/unread-residual-v2210.json", "seed": 16180339, "registry": "2.21.0",
        "errors": 1,
        "found": ["passivo in `passivo em taxa de juros` — a swap-leg collocation batch 16 "
                  "enumerated incompletely"],
        "state": "after phase two: the review backlog closed and the coverage target audited",
    },
    {
        "report": "reports/unread-residual-v2191.json", "seed": 31415926, "registry": "2.19.1",
        "errors": 0,
        "found": [],
        "state": "after every repair of adversarial review round 3",
    },
    {
        "report": "reports/unread-residual-v2190.json", "seed": 27182818, "registry": "2.19.0",
        "errors": 1,
        "found": ["do principal inside `o principal índice brasileiro` — a collocation added by "
                  "amendment 20 as a repair and never swept"],
        "state": "after adversarial review round 3 and its repairs; repaired by amendment 21",
    },
    {
        "report": "reports/unread-residual-v2180.json", "seed": 6180339, "registry": "2.18.0",
        "errors": 2,
        "found": ["sem inside `sem prejuízo de` (a fixed legal expression asserting the opposite)",
                  "ativo as a swap leg in `FICA PASSIVO EM DÓLAR (e ativo em taxa de juros)`"],
        "state": "after adversarial review round 2 and its repairs, including the demote-vs-forbid "
                 "rule that restored `rendimento`, `desconto`, `futuros` and `opções`",
    },
    {
        "report": "reports/unread-residual-v216.json", "seed": 770231, "registry": "2.16.2",
        "errors": 0,
        "found": [],
        "state": "first draw with the normative operator vocabulary active; `até` had already "
                 "been caught at 22/27 wrong by its own exhaustive sweep and removed",
    },
    {
        "report": "reports/unread-residual-v2141.json", "seed": 41077, "registry": "2.14.1",
        "errors": 2, "overwritten": True,
        "superseded_by": "the same seed redrawn into the same filename against 2.15.0, which "
                         "found none — so this entry's two errors are no longer re-derivable "
                         "from that path and are recorded here on the strength of the "
                         "adjudication written at the time, not on a surviving artifact",
        "found": ["índices in `índices P/L` (plural form unregistered)",
                  "distribuição in `distribuição de dividendos` (exposed by narrowing entity.distribution)"],
        "state": "after adversarial review round 1 and its repairs; both repaired by batch 12",
    },
]


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 1.0)
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--current-draw", default="reports/unread-residual-v2240.json",
                        help="the draw taken against the current registry")
    args = parser.parse_args()

    draws = []
    for entry in DRAWS:
        path = ROOT / entry["report"]
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        low, high = wilson(entry["errors"], report["sample_size"])
        draws.append({
            **entry,
            "sample_size": report["sample_size"],
            "error_rate": round(entry["errors"] / report["sample_size"], 4),
            "wilson_95": [round(low, 4), round(high, 4)],
        })

    current_path = ROOT / args.current_draw
    if not current_path.exists():
        raise SystemExit(f"{args.current_draw} is missing; the final figure needs a draw "
                         "taken against the CURRENT registry, not an earlier one")
    current = json.loads(current_path.read_text(encoding="utf-8"))
    # The guard the review found missing: this promised to refuse a draw from an earlier
    # registry and only ever checked that the file existed, so a figure measured at 2.12.0 was
    # published under the 2.13.0 label. A stale draw is not merely imprecise — it describes a
    # different artifact than the one it is attached to.
    sys.path.insert(0, str(ROOT / "src"))
    from semantic_normalizer.registry import load_registry

    shipped = load_registry()["version"]
    drawn = current.get("registry", {}).get("version")
    if drawn != shipped:
        raise SystemExit(
            f"{args.current_draw} was drawn against registry {drawn}, but {shipped} ships. "
            "Redraw it: a precision figure must describe the artifact it is published with."
        )

    current_errors = current.get("adjudicated_errors")
    if current_errors is None:
        raise SystemExit(f"{args.current_draw} has no `adjudicated_errors`; it has been drawn "
                         "but not read. A figure computed from an unread draw would be fiction.")

    total_events = current["events_total"]
    read_events = current["events_read_by_sweep"]
    unread_events = current["events_unread"]
    sample_size = current["sample_size"]
    unread_share = unread_events / total_events

    low, high = wilson(current_errors, sample_size)
    point = current_errors / sample_size

    # The swept part's error count is READ, never asserted. An adversarial review found this
    # field hard-coded to 0 while two errors sat inside the swept set — `índice P/L` had passed
    # three separate readings and `atenção` five. A number a script writes about its own work is
    # not evidence, so if the adjudication record is absent the field says `unmeasured` and the
    # bound is computed as if the swept part were entirely unknown.
    adjudication_path = ROOT / "reports" / "sweep-adjudication.json"
    if adjudication_path.exists():
        adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
        swept_open = adjudication["errors_open"]
        swept_basis = adjudication["errors_open_basis"]
        swept_repaired = len(adjudication["errors_found_and_repaired"])
    else:
        swept_open, swept_basis, swept_repaired = "unmeasured", (
            "reports/sweep-adjudication.json is absent, so nothing is known about the swept "
            "part. The bound below treats it as unknown rather than as clean."
        ), None
    report = {
        "schema_version": "precision-final-v1",
        "registry_version": current["registry"]["version"],
        "concepts": current["registry"]["concepts"],
        "events_total": total_events,
        "swept": {
            "events": read_events,
            "share": round(read_events / total_events, 4),
            "known_errors_open": swept_open,
            "forms_with_errors_found_and_repaired": swept_repaired,
            "basis": swept_basis,
        },
        "residual": {
            "events": unread_events,
            "share": round(unread_share, 4),
            "sample_size": sample_size,
            "errors_adjudicated": current_errors,
            "error_rate_point": round(point, 4),
            "error_rate_wilson_95": [round(low, 4), round(high, 4)],
        },
        "precision": {
            "point": round(1 - point * unread_share, 4) if swept_open == 0 else None,
            "lower_bound_95": round(1 - high * unread_share, 4) if swept_open == 0 else None,
            "upper_bound_95": round(1 - low * unread_share, 4) if swept_open == 0 else None,
            "interpretation": "The lower bound is what a claim should be made against: it "
                              "assumes the residual error rate sits at the top of what a sample "
                              "of this size can rule out.",
        },
        "campaign": draws,
        "errors_repaired_total": sum(entry["errors"] for entry in DRAWS) if DRAWS else 0,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in ("registry_version", "concepts", "events_total",
                                             "swept", "residual", "precision")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
