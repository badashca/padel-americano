#!/usr/bin/env python3
"""Quick feasibility test: can N=12 / 3 courts / 11 rounds be solved
with opp_max <= 2 (true Balanced Whist)?

Runs the CP-SAT model with the bound added as a HARD constraint, which
is dramatically faster than minimising. If feasible — we get a true 2..2
schedule. If infeasible — the runtime greedy 1..3 is provably the best.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from precompute_schedules import build_and_solve, to_compact
import json

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "schedules.json"

# Try with hard cap opp_max <= 2 for the 12/3 config.
variants, _ = build_and_solve(N=12, C=3, R=11, target_variants=20,
                              max_seconds=1800, hard_opp_max=2)

if not variants:
    print("\n2..2 infeasible — keeping existing 1..3 schedules")
    sys.exit(0)

print(f"\nFound {len(variants)} optimal 2..2 schedules — replacing 12-3 in JSON")
out = json.loads(OUT_PATH.read_text())
out["configs"]["12-3"] = {
    "N": 12, "C": 3, "R": 11,
    "stats": {
        "partnerDups": variants[0]["partner_dups"],
        "oppMin": variants[0]["opp_min"],
        "oppMax": variants[0]["opp_max"],
    },
    "variants": to_compact(variants),
}
OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")
