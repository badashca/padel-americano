#!/usr/bin/env python3
"""
Precompute mathematically-optimal Padel-Americano schedules using
OR-Tools CP-SAT solver.

For each (N, C, R) config we:
  1. Build a CP model where every match (i, j, k, l) on court c in
     round r is a Boolean variable. Constraints enforce a valid
     schedule (each playing player is in exactly one match per round,
     court contains 4 distinct players, partnership pairs sum correctly).
  2. Add objective: minimise the maximum opponent-pair count, with
     partnership duplicates as a hard constraint (= 0 when feasible,
     else minimum forced by the math).
  3. Enumerate up to TARGET_VARIANTS distinct optimal solutions by
     adding "exclude this solution" constraints between solves.

Outputs data/schedules.json in the same format used by index.html.
"""

import json
import os
import sys
import time
from itertools import combinations
from pathlib import Path

from ortools.sat.python import cp_model

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "schedules.json"

TARGET_VARIANTS = 20


def all_matches(N):
    """Return all 4-player combinations as sorted 4-tuples."""
    return list(combinations(range(N), 4))


def all_team_splits(four):
    """For a 4-tuple (a,b,c,d), return the 3 ways to split into
    (team1, team2) where each team is sorted and team1 < team2 lex."""
    a, b, c, d = four
    splits = [
        ((a, b), (c, d)),
        ((a, c), (b, d)),
        ((a, d), (b, c)),
    ]
    # Normalise: ensure team1 has smaller min, otherwise swap
    out = []
    for t1, t2 in splits:
        if t1[0] > t2[0]:
            t1, t2 = t2, t1
        out.append((t1, t2))
    return out


def build_and_solve(N, C, R, target_variants=TARGET_VARIANTS, max_seconds=120):
    """Run CP-SAT to find up to target_variants optimal schedules."""
    print(f"\n=== N={N}, C={C}, R={R} ===", flush=True)
    matches = all_matches(N)
    print(f"  candidate 4-tuples: {len(matches)}", flush=True)

    # Decision: m[(r, four)] = 1 if this 4-tuple plays together in round r (any court)
    # We don't care WHICH court — courts are interchangeable. The court
    # number is assigned after solving.
    # Additional: t[(r, four, split_idx)] = 1 if four plays with split_idx
    #   in round r. Sum of t[(r, four, *)] == m[(r, four)].

    # Bound on opp-pair count
    total_opp_slots = R * C * 4   # each round: C matches × 4 opp pairs
    total_pairs = N * (N - 1) // 2
    avg_opp = total_opp_slots / total_pairs
    opp_upper = max(1, int(avg_opp) + 2)
    floor_opp = int(avg_opp)
    ceil_opp = int(avg_opp) if avg_opp.is_integer() else int(avg_opp) + 1

    print(f"  avg opp per pair = {avg_opp:.3f}, target spread = "
          f"{floor_opp}..{ceil_opp}", flush=True)

    found_variants = []
    forbidden = []  # list of sets of (round, four_tuple, split_idx) triples to exclude

    optimum_partner_dups = None  # set after first solve

    while len(found_variants) < target_variants:
        model = cp_model.CpModel()

        # Per (round, four-tuple) Boolean
        m_vars = {}
        # Per (round, four-tuple, split_idx) Boolean
        t_vars = {}
        for r in range(R):
            for four in matches:
                m_vars[(r, four)] = model.NewBoolVar(f"m_r{r}_{four}")
                for s in range(3):
                    t_vars[(r, four, s)] = model.NewBoolVar(f"t_r{r}_{four}_s{s}")
                # If match selected, exactly one split
                model.Add(sum(t_vars[(r, four, s)] for s in range(3)) == m_vars[(r, four)])

        # Each round: exactly C matches
        for r in range(R):
            model.Add(sum(m_vars[(r, four)] for four in matches) == C)

        # Each playing player appears in at most one match per round
        # (For configs with rest, some players sit out.)
        playing_per_round = min(N, 4 * C)
        resting_per_round = N - playing_per_round
        for r in range(R):
            for i in range(N):
                model.Add(
                    sum(m_vars[(r, four)] for four in matches if i in four) <= 1
                )
            # And total players in matches == 4*C
            model.Add(
                sum(m_vars[(r, four)] for four in matches) * 4
                == playing_per_round
            )

        # Partnership variables: for each pair (i,j), how many rounds they
        # partner together. p_count[(i,j)] = sum over splits where i&j are
        # on the same team.
        partner_count = {}
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
            count = model.NewIntVar(0, R, f"pc_{i}_{j}")
            model.Add(count == sum(terms))
            partner_count[(i, j)] = count

        # Opponent variables
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

        # Rest count per player (each round counts if not in any match)
        rest_count = {}
        if resting_per_round > 0:
            for i in range(N):
                rest_var = model.NewIntVar(0, R, f"rest_{i}")
                # rest count = R - num_rounds_in_a_match
                model.Add(rest_var == R - sum(
                    m_vars[(r, four)]
                    for r in range(R)
                    for four in matches
                    if i in four
                ))
                rest_count[i] = rest_var
            # Constrain spread <= 1 (max - min)
            rest_min = model.NewIntVar(0, R, "rest_min")
            rest_max = model.NewIntVar(0, R, "rest_max")
            model.AddMinEquality(rest_min, list(rest_count.values()))
            model.AddMaxEquality(rest_max, list(rest_count.values()))
            model.Add(rest_max - rest_min <= 1)

        # Partner dup objective: minimise sum of max(0, partner_count - 1)
        partner_dup_terms = []
        for (i, j), pc in partner_count.items():
            dup = model.NewIntVar(0, R, f"pd_{i}_{j}")
            model.Add(dup >= pc - 1)
            model.Add(dup >= 0)
            partner_dup_terms.append(dup)
        total_partner_dups = model.NewIntVar(0, R * N * N, "total_pd")
        model.Add(total_partner_dups == sum(partner_dup_terms))

        # Opp max objective
        opp_max = model.NewIntVar(0, R, "opp_max")
        opp_min = model.NewIntVar(0, R, "opp_min")
        model.AddMaxEquality(opp_max, list(opp_count.values()))
        model.AddMinEquality(opp_min, list(opp_count.values()))

        # Exclude previously found solutions
        for forbid in forbidden:
            # forbid is set of (r, four, s) triples that defined a solution
            # Add constraint that not all these are 1 simultaneously
            model.AddBoolOr([
                t_vars[triple].Not() for triple in forbid
            ])

        # Objective
        if optimum_partner_dups is None:
            # Phase 1: minimise partner dups, secondary minimise opp max
            model.Minimize(total_partner_dups * 1000 + opp_max * 10 + opp_min * (-1))
        else:
            # Phase 2: fix optimum partner_dups, minimise opp max
            model.Add(total_partner_dups == optimum_partner_dups)
            model.Add(opp_max == optimum_opp_max)
            model.Add(opp_min == optimum_opp_min)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_seconds
        solver.parameters.num_search_workers = 8
        t0 = time.time()
        status = solver.Solve(model)
        dt = time.time() - t0

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print(f"  no more solutions (status {status}) after {dt:.1f}s", flush=True)
            break

        pd = solver.Value(total_partner_dups)
        omax = solver.Value(opp_max)
        omin = solver.Value(opp_min)

        if optimum_partner_dups is None:
            optimum_partner_dups = pd
            optimum_opp_max = omax
            optimum_opp_min = omin
            print(
                f"  optimum found: partner_dups={pd}, opp {omin}..{omax} (in {dt:.1f}s)",
                flush=True,
            )

        # Extract solution
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

        found_variants.append({
            "rounds": rounds,
            "partner_dups": pd,
            "opp_min": omin,
            "opp_max": omax,
        })
        forbidden.append(chosen_triples)
        print(
            f"  variant {len(found_variants)}/{target_variants} found ({dt:.1f}s)",
            flush=True,
        )

        if status == cp_model.OPTIMAL and len(found_variants) >= target_variants:
            break

    return found_variants, optimum_partner_dups


def to_compact(variants):
    """Convert solver output to the JSON layout the app expects."""
    out = []
    for v in variants:
        round_payload = []
        for round_matches in v["rounds"]:
            matches = [[t1[0], t1[1], t2[0], t2[1]] for t1, t2 in round_matches]
            round_payload.append({"matches": matches})
        out.append({"rounds": round_payload})
    return out


CONFIGS = [
    # (N, C, R, time_budget_per_solve)
    (4, 1, 3, 30),
    (8, 2, 7, 120),
    (12, 3, 11, 600),
    (16, 4, 15, 1200),
]


def main():
    only = set()
    if len(sys.argv) > 1:
        only = set(sys.argv[1:])

    out = {"version": 1, "configs": {}}
    if OUT_PATH.exists():
        try:
            out = json.loads(OUT_PATH.read_text())
            out.setdefault("version", 1)
            out.setdefault("configs", {})
        except Exception:
            pass

    for N, C, R, budget in CONFIGS:
        key = f"{N}-{C}"
        if only and key not in only:
            continue
        variants, optimum_pd = build_and_solve(N, C, R, max_seconds=budget)
        if not variants:
            print(f"  no variants found for {key}", flush=True)
            continue
        first = variants[0]
        out["configs"][key] = {
            "N": N, "C": C, "R": R,
            "stats": {
                "partnerDups": first["partner_dups"],
                "oppMin": first["opp_min"],
                "oppMax": first["opp_max"],
            },
            "variants": to_compact(variants),
        }
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
        size_kb = OUT_PATH.stat().st_size / 1024
        print(f"  wrote {OUT_PATH} ({size_kb:.1f} KB)", flush=True)

    print("\nDone.")


if __name__ == "__main__":
    main()
