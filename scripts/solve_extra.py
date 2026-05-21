#!/usr/bin/env python3
"""Solve the next batch of configs with the PRIORITY: partners are
always unique (partner_dups = 0). When math forces a tradeoff between
"all pairs play together" and "no duplicate partners", we choose
unique partners — losing one or two pair coverages is preferable to
having a duplicate partnership.

Configs covered:
- 5/1   (5 rounds, perfect: 0 dups + every pair plays once)
- 9/2   (9 rounds, perfect)
- 10/2  (11 rounds, 1 pair won't play together — chosen for unique partners)
- 13/3  (13 rounds, perfect)

For each, we run CP-SAT with HARD constraint partner_dups=0, then
minimise opp_max as the secondary objective.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from itertools import combinations
import time
from ortools.sat.python import cp_model

from precompute_schedules import all_matches, all_team_splits, to_compact

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "schedules.json"


def solve_unique_partners(N, C, R, target_variants=20, max_seconds=600):
    """Find up to target_variants schedules with partner_dups=0 and the
    lowest possible opp_max. The 'all pairs partner once' is not required —
    we may leave a few pairs un-paired in exchange for uniqueness."""
    print(f"\n========= N={N}, C={C}, R={R} =========")
    matches = all_matches(N)
    print(f"  candidate 4-tuples: {len(matches)}")

    total_partnerships = R * C * 2  # each round: C matches × 2 partnerships
    total_pairs = N * (N - 1) // 2
    print(f"  partnerships per schedule: {total_partnerships}, unique pairs: {total_pairs}")
    if total_partnerships > total_pairs:
        print(f"  WARN: more slots than unique pairs — duplicates forced ({total_partnerships - total_pairs} of them).")
    else:
        missing = total_pairs - total_partnerships
        if missing > 0:
            print(f"  Note: {missing} pair(s) will not play as partners (tradeoff for 0 dups).")

    # Average opp count for scoring info
    total_opp_slots = R * C * 4
    avg_opp = total_opp_slots / total_pairs
    print(f"  avg opp per pair = {avg_opp:.3f}")
    floor_opp = int(avg_opp)
    ceil_opp = int(avg_opp) if avg_opp.is_integer() else int(avg_opp) + 1

    forbidden = []
    optimum_opp_max = None
    optimum_opp_min = None
    variants = []

    while len(variants) < target_variants:
        model = cp_model.CpModel()

        m_vars = {}
        t_vars = {}
        for r in range(R):
            for four in matches:
                m_vars[(r, four)] = model.NewBoolVar(f"m_r{r}_{four}")
                for s in range(3):
                    t_vars[(r, four, s)] = model.NewBoolVar(f"t_r{r}_{four}_s{s}")
                model.Add(sum(t_vars[(r, four, s)] for s in range(3)) == m_vars[(r, four)])

        # Each round: exactly C matches
        for r in range(R):
            model.Add(sum(m_vars[(r, four)] for four in matches) == C)

        # Each player at most once per round
        playing_per_round = min(N, 4 * C)
        for r in range(R):
            for i in range(N):
                model.Add(
                    sum(m_vars[(r, four)] for four in matches if i in four) <= 1
                )
            model.Add(
                sum(m_vars[(r, four)] for four in matches) * 4 == playing_per_round
            )

        # Partnership counts and HARD constraint: no pair partners more than once
        for i, j in combinations(range(N), 2):
            terms = []
            for r in range(R):
                for four in matches:
                    if i not in four or j not in four:
                        continue
                    splits = all_team_splits(four)
                    for s_idx, (t1, t2) in enumerate(splits):
                        if (i in t1 and j in t1) or (i in t2 and j in t2):
                            terms.append(t_vars[(r, four, s_idx)])
            model.Add(sum(terms) <= 1)  # 0 or 1 partnerships per pair

        # Opponent counts
        opp_count = {}
        for i, j in combinations(range(N), 2):
            terms = []
            for r in range(R):
                for four in matches:
                    if i not in four or j not in four:
                        continue
                    splits = all_team_splits(four)
                    for s_idx, (t1, t2) in enumerate(splits):
                        if (i in t1 and j in t2) or (i in t2 and j in t1):
                            terms.append(t_vars[(r, four, s_idx)])
            count = model.NewIntVar(0, R, f"oc_{i}_{j}")
            model.Add(count == sum(terms))
            opp_count[(i, j)] = count

        opp_max = model.NewIntVar(0, R, "opp_max")
        opp_min = model.NewIntVar(0, R, "opp_min")
        model.AddMaxEquality(opp_max, list(opp_count.values()))
        model.AddMinEquality(opp_min, list(opp_count.values()))

        # Rest balance (max - min <= 1)
        resting_per_round = N - playing_per_round
        if resting_per_round > 0:
            rest_counts = []
            for i in range(N):
                rest_var = model.NewIntVar(0, R, f"rest_{i}")
                model.Add(rest_var == R - sum(
                    m_vars[(r, four)]
                    for r in range(R)
                    for four in matches
                    if i in four
                ))
                rest_counts.append(rest_var)
            rmin = model.NewIntVar(0, R, "rmin")
            rmax = model.NewIntVar(0, R, "rmax")
            model.AddMinEquality(rmin, rest_counts)
            model.AddMaxEquality(rmax, rest_counts)
            model.Add(rmax - rmin <= 1)

        # Exclude previously found solutions
        for forbid in forbidden:
            model.AddBoolOr([t_vars[triple].Not() for triple in forbid])

        # Objective
        if optimum_opp_max is None:
            model.Minimize(opp_max * 10 + opp_min * (-1))
        else:
            model.Add(opp_max == optimum_opp_max)
            model.Add(opp_min == optimum_opp_min)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_seconds
        solver.parameters.num_search_workers = 8
        t0 = time.time()
        status = solver.Solve(model)
        dt = time.time() - t0

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print(f"  no more solutions (status {status}) after {dt:.1f}s")
            break

        omax = solver.Value(opp_max)
        omin = solver.Value(opp_min)
        if optimum_opp_max is None:
            optimum_opp_max = omax
            optimum_opp_min = omin
            print(f"  optimum: opp {omin}..{omax} (in {dt:.1f}s)")

        rounds = []
        chosen_triples = set()
        for r in range(R):
            round_matches = []
            for four in matches:
                if solver.Value(m_vars[(r, four)]) == 1:
                    for s_idx in range(3):
                        if solver.Value(t_vars[(r, four, s_idx)]) == 1:
                            chosen_triples.add((r, four, s_idx))
                            t1, t2 = all_team_splits(four)[s_idx]
                            round_matches.append((list(t1), list(t2)))
                            break
            rounds.append(round_matches)

        variants.append({
            "rounds": rounds,
            "partner_dups": 0,
            "opp_min": omin,
            "opp_max": omax,
        })
        forbidden.append(chosen_triples)
        print(f"  variant {len(variants)}/{target_variants} ({dt:.1f}s)")

    return variants


def save_config(N, C, R, variants):
    out = json.loads(OUT_PATH.read_text())
    first = variants[0]
    out["configs"][f"{N}-{C}"] = {
        "N": N, "C": C, "R": R,
        "stats": {
            "partnerDups": 0,
            "oppMin": first["opp_min"],
            "oppMax": first["opp_max"],
        },
        "variants": to_compact(variants),
    }
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"  saved → file {OUT_PATH.stat().st_size / 1024:.1f} KB")


CONFIGS = [
    # (N, C, R, max_seconds_per_solve)
    (5,  1,  5,  60),
    (9,  2,  9,  300),
    (10, 2, 11,  600),    # R=11, NOT 12 — keeps partner_dups=0
    (13, 3, 13, 1800),
]


def main():
    only = set(sys.argv[1:])
    for N, C, R, budget in CONFIGS:
        key = f"{N}-{C}"
        if only and key not in only:
            continue
        variants = solve_unique_partners(N, C, R, max_seconds=budget)
        if variants:
            save_config(N, C, R, variants)


if __name__ == "__main__":
    main()
