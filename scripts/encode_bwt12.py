#!/usr/bin/env python3
"""
Encode the user-provided Balanced Whist Tournament for 12 players /
3 courts / 11 rounds. Verifies it's mathematically optimal
(partner_dups=0, every pair as opponents exactly 2x), then generates
20 distinct variants by permuting the player labels.

Player label mapping (0-indexed):
  0=Egor, 1=Galya, 2=LeshaG, 3=David, 4=Grisha, 5=MashaB,
  6=Maria, 7=LeshaN, 8=Sergey, 9=Maxim, 10=Nikita, 11=Oksana
"""

import json
import random
from itertools import combinations
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "schedules.json"
TARGET_VARIANTS = 20

# 11 rounds, each with 3 courts. Each court is [t1a, t1b, t2a, t2b].
BASE_SCHEDULE = [
    # Round 1
    [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9, 10, 11]],
    # Round 2
    [[0, 4, 9, 1], [5, 8, 10, 2], [6, 11, 7, 3]],
    # Round 3
    [[0, 5, 11, 4], [8, 6, 7, 9], [10, 3, 2, 1]],
    # Round 4
    [[0, 8, 3, 5], [6, 10, 2, 11], [7, 1, 9, 4]],
    # Round 5
    [[0, 6, 1, 8], [10, 7, 9, 3], [2, 4, 11, 5]],
    # Round 6
    [[0, 10, 4, 6], [7, 2, 11, 1], [9, 5, 3, 8]],
    # Round 7
    [[0, 7, 5, 10], [2, 9, 3, 4], [11, 8, 1, 6]],
    # Round 8
    [[0, 2, 8, 7], [9, 11, 1, 5], [3, 6, 4, 10]],
    # Round 9
    [[0, 9, 6, 2], [11, 3, 4, 8], [1, 10, 5, 7]],
    # Round 10
    [[0, 11, 10, 9], [3, 1, 5, 6], [4, 7, 8, 2]],
    # Round 11
    [[0, 3, 7, 11], [1, 4, 8, 10], [5, 2, 6, 9]],
]


def verify(schedule, N=12):
    """Verify a schedule is a balanced Whist: partner_dups=0, opp 2..2."""
    partner = [[0] * N for _ in range(N)]
    opp = [[0] * N for _ in range(N)]
    for r, round_matches in enumerate(schedule):
        all_players = []
        for match in round_matches:
            a, b, c, d = match
            partner[a][b] += 1; partner[b][a] += 1
            partner[c][d] += 1; partner[d][c] += 1
            for x in (a, b):
                for y in (c, d):
                    opp[x][y] += 1; opp[y][x] += 1
            all_players.extend(match)
        # Each round should have all 12 players exactly once
        assert sorted(all_players) == list(range(N)), \
            f"Round {r}: players {sorted(all_players)} != {list(range(N))}"

    # Partner: every off-diagonal entry should be exactly 1
    for i, j in combinations(range(N), 2):
        if partner[i][j] != 1:
            return False, f"partner ({i},{j}) = {partner[i][j]} (want 1)"
    # Opponent: every off-diagonal entry should be exactly 2
    for i, j in combinations(range(N), 2):
        if opp[i][j] != 2:
            return False, f"opp ({i},{j}) = {opp[i][j]} (want 2)"
    return True, "balanced Whist verified: partner=1, opp=2 for all pairs"


def relabel(schedule, perm):
    """Apply a player permutation: position p in original maps to perm[p]."""
    out = []
    for round_matches in schedule:
        new_round = []
        for match in round_matches:
            new_round.append([perm[p] for p in match])
        out.append(new_round)
    return out


def canonical_key(schedule):
    """Order-independent fingerprint for dedup."""
    sig = []
    for round_matches in schedule:
        round_sig = []
        for m in round_matches:
            t1 = sorted([m[0], m[1]])
            t2 = sorted([m[2], m[3]])
            teams = sorted([t1, t2])
            round_sig.append(f"{teams[0][0]},{teams[0][1]}|{teams[1][0]},{teams[1][1]}")
        sig.append(';'.join(sorted(round_sig)))
    return '\n'.join(sorted(sig))


def to_variant(schedule):
    """Wrap in the JSON shape expected by index.html."""
    return {"rounds": [{"matches": rm} for rm in schedule]}


def main():
    ok, msg = verify(BASE_SCHEDULE)
    print(f"Verification: {msg}")
    if not ok:
        raise SystemExit(1)

    random.seed(42)
    variants = []
    seen = set()
    # Always include the base schedule first
    variants.append(BASE_SCHEDULE)
    seen.add(canonical_key(BASE_SCHEDULE))

    # Generate variants by random permutation until we have TARGET_VARIANTS distinct
    attempts = 0
    while len(variants) < TARGET_VARIANTS and attempts < 5000:
        attempts += 1
        perm = list(range(12))
        random.shuffle(perm)
        v = relabel(BASE_SCHEDULE, perm)
        # Sanity-check the permuted schedule too
        ok, _ = verify(v)
        if not ok:
            continue
        key = canonical_key(v)
        if key in seen:
            continue
        seen.add(key)
        variants.append(v)

    print(f"Generated {len(variants)} distinct balanced variants")

    # Load existing JSON and replace 12-3
    out = json.loads(OUT_PATH.read_text())
    out["configs"]["12-3"] = {
        "N": 12, "C": 3, "R": 11,
        "stats": {"partnerDups": 0, "oppMin": 2, "oppMax": 2},
        "variants": [to_variant(v) for v in variants],
    }
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
