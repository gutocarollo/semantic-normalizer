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
        "report": "reports/unread-residual-v2300-r8.json", "seed": 31622777,
        "registry": "2.30.0",
        "errors": 0,
        "found": [],
        "state": "after batches 42-43 and amendments 60-61: the heading-coverage vocabulary, the "
                 "concept the coverage filter had been hiding, and the material_information lemma "
                 "fix. Drawn because an adversarial review established that the largest single "
                 "vocabulary delta of the campaign had never passed this pipeline — the precision "
                 "figure still described registry 2.28.0 while 2.30.0 shipped. All 240 read "
                 "individually; zero false positives.",
    },
    {
        "report": "reports/unread-residual-v2280-r7.json", "seed": 31622777,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "after batch 40 registered the 20 real terms the partial-heading bucket was hiding",
    },
    {
        "report": "reports/unread-residual-v2280-r6.json", "seed": 26457514,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "after the unattested-canonical tail was worked down to its floor",
    },
    {
        "report": "reports/unread-residual-v2280-r5.json", "seed": 22360680,
        "registry": "2.28.0",
        "errors": 1,
        "found": ["`configure` as the Portuguese reflexive subjunctive matching seed software "
                  "vocabulary — repaired by amendment 56"],
        "state": "after the colon tie-break fix and technical.credit_spread",
    },
    {
        "report": "reports/unread-residual-v2280-r4.json", "seed": 17320508,
        "registry": "2.28.0",
        "errors": 1,
        "found": ["`Liquidez` in the elided-head risk list — the residual already declared in "
                  "KNOWN_RESIDUALS, found rather than new"],
        "state": "after the heading-coverage tail was worked to 84.62 %",
    },
    {
        "report": "reports/unread-residual-v2280-r3.json", "seed": 30313373,
        "registry": "2.28.0",
        "errors": 1,
        "found": ["`BD` as Benefício Definido resolving to system.database, seed software "
                  "vocabulary colliding with a pension acronym — repaired by batch 37 and "
                  "amendments 51-52"],
        "state": "after the heading-coverage tail was worked rather than declared: 30 concepts "
                 "across batches 35-36, coverage 60.62 % -> 76.23 %",
    },
    {
        "report": "reports/unread-residual-v2280-r2.json", "seed": 14142136,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "after the truncation rule was narrowed to fragments only, restoring the 125 "
                 "legitimate simplifications the first version suppressed",
    },
    {
        "report": "reports/unread-residual-v2280-final.json", "seed": 24011975,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "after the sense splits the antonym guard forced — put/call, the two swap legs, "
                 "and `expressamente` — each its own concept",
    },
    {
        "report": "reports/unread-residual-v2280-pool3.json", "seed": 66260701,
        "registry": "2.28.0",
        "errors": 1,
        "found": ["`múltiplos` as the ordinary quantifier in `imunização de múltiplos passivos` "
                  "rather than the valuation multiple — 6 of 13 occurrences, repaired by "
                  "amendment 45"],
        "state": "the shipped state: 483 concepts, after the canonical-surface repairs and the "
                 "language-detection fix",
    },
    {
        "report": "reports/unread-residual-v2280-pool1.json", "seed": 57721566,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "after amendment 40 closed the elliptical-coordination collocations; pooled with "
                 "the draw below, which shares its population exactly",
    },
    {
        "report": "reports/unread-residual-v2280-pool2.json", "seed": 16180339,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "after amendment 40; the second half of the pooled 480",
    },
    {
        "report": "reports/unread-residual-v2280-accounted.json", "seed": 27182818,
        "registry": "2.28.0",
        "errors": 0,
        "found": [],
        "state": "drawn with every sweep queue passed, which is the accounting the two draws below "
                 "got wrong by being given one queue file; zero sense errors, one recall gap "
                 "(amendment 39). This is the CLEANER of the two post-repair draws and is NOT the "
                 "one the published figure comes from: the draw below found a real residual that "
                 "still exists by design, and reporting a zero-error sample instead would be "
                 "choosing the number rather than measuring it",
    },
    {
        "report": "reports/unread-residual-v2280-confirm.json", "seed": 31415926,
        "registry": "2.28.0",
        "errors": 1,
        "found": ["`Posições Compradas` glossing the Long Extension strategy rather than naming a "
                  "long position — 1 of the form's 7 occurrences, the residual the 50 % rule "
                  "leaves by design"],
        "state": "after amendments 37 and 38 repaired what the first 2.28.0 draw found",
    },
    {
        "report": "reports/unread-residual-v2280.json", "seed": 20260801, "registry": "2.28.0",
        "errors": 2,
        "found": ["`posições compradas` on technical.long_extension — the long LEG of a position "
                  "read as the long-extension STRATEGY, all 3 occurrences",
                  "`ISR` on technical.esg — the broader family, where the corpus defines the "
                  "acronym as socially responsible investment outright, all 9 occurrences"],
        "state": "after the heading-coverage batches took the dictionary from 395 to 482 concepts; "
                 "both errors were pre-existing misassignments the new concepts made visible, and "
                 "amendment 37 reassigned them",
    },
    {
        "report": "reports/unread-residual-v2270.json", "seed": 73205080, "registry": "2.27.0",
        "errors": 0,
        "found": [],
        "state": "after the matcher was corrected to resolve overlaps by length",
    },
    {
        "report": "reports/unread-residual-v2260.json", "seed": 22360679, "registry": "2.26.0",
        "errors": 0,
        "found": [],
        "state": "after every repair of the phase-two review",
    },
    {
        "report": "reports/unread-residual-v2250.json", "seed": 26457513, "registry": "2.25.0",
        "errors": 1,
        "found": ["CDs (Certificates of Deposit) matching technical.cds — an acronym collision "
                  "the importer's case-sensitive demoter passed"],
        "state": "after the review of the phase-two repairs; repaired by batch 24 and amendment 31",
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


# Residuals this campaign measured, judged, and deliberately left — each below the 50 % threshold
# that would justify demoting its form, so removing it would cost more correct matches than the one
# wrong match it removes. They are named here because a precision report that finds zero errors has
# to be readable alongside the errors we know are still there.
KNOWN_RESIDUALS = [
    "technical.long_position / `Posições Compradas` glossing the Long Extension strategy — 1 of the "
    "form's 7 occurrences, the other 6 being plain long positions",
    "technical.liquidity / `Liquidez` in `- De Crédito - De Liquidez - De Volatilidade`, a list of "
    "risk types with the head `risco` elided — 1 of the form's occurrences",
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
    parser.add_argument("--current-draw", default="reports/unread-residual-v2360-r15.json",
                        help="the draw taken against the current registry")
    parser.add_argument("--pool-with", nargs="*", default=[],
                        help="further draws over the IDENTICAL population, pooled with the current "
                             "one; pooling uses more of the evidence already collected, selecting "
                             "between draws uses less of it and picks which number to report")
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
    unread_share = unread_events / total_events

    # Draws over the SAME population pool; they do not compete. A review pointed out that
    # publishing one of several valid measurements is a choice about which number to report, and
    # that refusing to publish the cleanest one is only half the correction — the other half is
    # that pooling uses evidence already paid for. The population identity is checked rather than
    # assumed: same registry version, same event total, same unread count. Draws taken before an
    # amendment changed the registry are NOT eligible, however similar they look, and two earlier
    # 2.28.0 draws are excluded here for exactly that reason even though pooling them would have
    # produced a tighter bound.
    pooled_paths, sample_size, pooled_errors = [], current["sample_size"], current_errors
    for candidate in args.pool_with:
        candidate_path = ROOT / candidate
        if not candidate_path.exists():
            raise SystemExit(f"{candidate} is missing; pooling names it explicitly")
        other = json.loads(candidate_path.read_text(encoding="utf-8"))
        if other.get("adjudicated_errors") is None:
            raise SystemExit(f"{candidate} has not been read; an unread draw cannot be pooled")
        same_population = (
            other["registry"]["version"] == drawn
            and other["events_total"] == total_events
            and other["events_unread"] == unread_events
        )
        if not same_population:
            raise SystemExit(
                f"{candidate} describes a different population than {args.current_draw} "
                f"(registry/events/unread differ); pooling across populations would be a "
                f"different measurement wearing one number"
            )
        pooled_paths.append(candidate)
        sample_size += other["sample_size"]
        pooled_errors += other["adjudicated_errors"]
    current_errors = pooled_errors

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
    # What the draw can and cannot see, stated next to what it found. Zero errors in a sample is
    # read as "no errors exist" unless the report says otherwise, and this campaign has declared
    # residuals it deliberately did not repair — the `Posições Compradas` gloss, `liquidez` where
    # `risco` is elided in a list. Their rate is far below what a sample this size can resolve, so
    # not drawing them is the expected outcome rather than evidence the sample is blind; the
    # arithmetic saying so belongs in the report and not in a reviewer's head.
    #
    # P(catch >= 1 of k marked events when drawing n of N) = 1 - (1 - n/N)**k. Solved for n at
    # p=0.5 to give the sample size that would make detection a coin flip. Verified against a
    # 20 000-trial simulation: the formula says 1899, the simulation measures 0.501 at n=1900.
    known_residuals = len(KNOWN_RESIDUALS)
    miss_one = 1 - sample_size / unread_events
    detectable = {
        "known_declared_residuals": known_residuals,
        "residuals": KNOWN_RESIDUALS,
        "error_rate_they_imply": round(known_residuals / unread_events, 6),
        "error_rate_this_draw_rules_out": round(high, 6),
        "probability_all_of_them_are_missed": round(miss_one ** known_residuals, 4),
        "sample_size_for_even_odds_of_catching_one": round(
            unread_events * (1 - 0.5 ** (1 / known_residuals))) if known_residuals else None,
        "reading": (
            f"{current_errors} error(s) found in {sample_size} is consistent with the residuals "
            "known to remain: they imply a rate well below what a sample of this size can resolve, "
            "so a draw that happens to miss them is expected rather than suspicious. The claim is "
            "the Wilson upper bound, not the point estimate — this draw rules out error rates "
            "above the figure named here, and says nothing about rates beneath it."
            if known_residuals / unread_events < high else
            "The known residuals alone imply a rate this draw claims to have ruled out. The sample "
            "is not reaching where the errors are and the bound is optimistic."
        ),
    }
    report = {
        "schema_version": "precision-final-v1",
        "registry_version": current["registry"]["version"],
        "concepts": current["registry"]["concepts"],
        "events_total": total_events,
        "residual_draws_pooled": [args.current_draw, *pooled_paths],
        "what_this_sample_cannot_detect": detectable,
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
