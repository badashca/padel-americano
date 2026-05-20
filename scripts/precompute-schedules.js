#!/usr/bin/env node
/* eslint-disable no-console */

/**
 * Precompute optimal Padel-Americano schedules for common configs.
 *
 * Run with `node scripts/precompute-schedules.js`. Output goes to
 * data/schedules.json. Each config produces up to TARGET_VARIANTS
 * distinct schedules that achieve the mathematical optimum for both
 * partnership duplicates and opponent spread.
 *
 * Algorithm:
 * 1. Build the Whist 1-factorization for the round (positions of pairs).
 * 2. For each round, enumerate ALL ways to partition its N/2
 *    partnerships into C courts (each holding 2 partnerships).
 * 3. Round-by-round coordinate descent: for each round, pick the
 *    grouping that minimises the global opponent quadratic cost given
 *    the OTHER rounds' current assignments. Iterate until no improvement.
 * 4. Repeat with different player-to-position permutations; keep
 *    schedules that achieve the math-bound optimum.
 */

const fs = require('fs');
const path = require('path');

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* Yield every way to partition `items` (length 2C) into C unordered
   pairs of pairs (i.e. C courts, each with 2 partnerships). */
function* partitionsIntoCourts(items, C) {
  if (C === 0) { yield []; return; }
  if (items.length !== 2 * C) return;
  const [head, ...rest] = items;
  for (let i = 0; i < rest.length; i++) {
    const remaining = rest.slice(0, i).concat(rest.slice(i + 1));
    for (const sub of partitionsIntoCourts(remaining, C - 1)) {
      yield [[head, rest[i]], ...sub];
    }
  }
}

/* Whist partnerships for round r among 2k+1 rotating positions plus a
   fixed centre position. Returns N/2 [a,b] partnerships in position space. */
function whistPartnerships(r, N) {
  const rotating = N - 1;
  const fixed = N - 1;
  const halfRot = Math.floor((rotating - 1) / 2);
  const parts = [[fixed, r]];
  for (let i = 1; i <= halfRot; i++) {
    const a = (r + i) % rotating;
    const b = ((r - i) % rotating + rotating) % rotating;
    parts.push([a, b]);
  }
  return parts;
}

const oppPenalty = (count) => (count + 1) * (count + 1);

/* Apply +1 / -1 to opponent counts for the four opp pairs in one court. */
function bumpCourt(opp, t1, t2, sign) {
  for (const a of t1) {
    for (const b of t2) {
      opp[a][b] += sign;
      opp[b][a] += sign;
    }
  }
}

function buildSchedule(N, C) {
  const positions = shuffle([...Array(N).keys()]);
  const R = N - 1;
  const opp = Array.from({ length: N }, () => new Array(N).fill(0));

  // Initial assignment: simplest grouping (sequential pairs of partnerships)
  const rounds = [];
  for (let r = 0; r < R; r++) {
    const parts = whistPartnerships(r, N).map(([a, b]) => [positions[a], positions[b]]);
    const courts = [];
    for (let c = 0; c < C; c++) {
      courts.push([parts[2 * c], parts[2 * c + 1]]);
    }
    rounds.push({ parts, courts });
    for (const [t1, t2] of courts) bumpCourt(opp, t1, t2, +1);
  }

  // Coordinate-descent polish: for each round, undo its contribution,
  // enumerate all partitions, pick the lowest-cost one, re-apply.
  let changed = true;
  let passes = 0;
  while (changed && passes < 50) {
    changed = false;
    passes++;
    for (let r = 0; r < R; r++) {
      // Remove this round's opp contributions.
      for (const [t1, t2] of rounds[r].courts) bumpCourt(opp, t1, t2, -1);

      let bestCourts = null;
      let bestCost = Infinity;
      for (const partition of partitionsIntoCourts(rounds[r].parts, C)) {
        let cost = 0;
        for (const [t1, t2] of partition) {
          cost += oppPenalty(opp[t1[0]][t2[0]])
                + oppPenalty(opp[t1[0]][t2[1]])
                + oppPenalty(opp[t1[1]][t2[0]])
                + oppPenalty(opp[t1[1]][t2[1]]);
        }
        if (cost < bestCost) { bestCost = cost; bestCourts = partition; }
      }

      // Re-apply chosen partition.
      const old = rounds[r].courts;
      const same = old.length === bestCourts.length
        && old.every((c, i) => c[0] === bestCourts[i][0] && c[1] === bestCourts[i][1]);
      if (!same) changed = true;
      rounds[r].courts = bestCourts;
      for (const [t1, t2] of bestCourts) bumpCourt(opp, t1, t2, +1);
    }
  }

  return { rounds, opp };
}

function scoreSchedule(N, rounds, opp) {
  const partner = Array.from({ length: N }, () => new Array(N).fill(0));
  for (const r of rounds) {
    for (const [t1, t2] of r.courts) {
      partner[t1[0]][t1[1]]++; partner[t1[1]][t1[0]]++;
      partner[t2[0]][t2[1]]++; partner[t2[1]][t2[0]]++;
    }
  }
  let partnerDups = 0;
  let oppMin = Infinity;
  let oppMax = 0;
  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      if (partner[i][j] > 1) partnerDups += partner[i][j] - 1;
      if (opp[i][j] < oppMin) oppMin = opp[i][j];
      if (opp[i][j] > oppMax) oppMax = opp[i][j];
    }
  }
  return { partnerDups, oppMin, oppMax, oppSpread: oppMax - oppMin };
}

function canonicalKey(rounds) {
  // Order-independent fingerprint for deduplication.
  const sig = rounds.map(r => {
    return r.courts.map(([t1, t2]) => {
      const a = [...t1].sort((x, y) => x - y);
      const b = [...t2].sort((x, y) => x - y);
      const teams = [a, b].sort((x, y) => x[0] - y[0] || x[1] - y[1]);
      return teams.map(t => t.join(',')).join('|');
    }).sort().join(';');
  });
  return sig.sort().join('\n');
}

function findOptimalSchedules(N, C, target, maxTries) {
  const R = N - 1;
  const avgOpp = (8 * C * R) / (N * (N - 1));
  const expectedFloor = Math.floor(avgOpp);
  const expectedCeil = Math.ceil(avgOpp);

  const variants = [];
  const seen = new Set();
  let bestSeenScore = null;

  for (let i = 0; i < maxTries && variants.length < target; i++) {
    const { rounds, opp } = buildSchedule(N, C);
    const score = scoreSchedule(N, rounds, opp);

    // Track best-overall for reporting even if we don't find target count.
    if (!bestSeenScore || score.oppMax < bestSeenScore.oppMax ||
        (score.oppMax === bestSeenScore.oppMax && score.partnerDups < bestSeenScore.partnerDups)) {
      bestSeenScore = score;
    }

    const isOptimal = score.partnerDups === 0
                   && score.oppMin >= expectedFloor
                   && score.oppMax <= expectedCeil;
    if (!isOptimal) continue;

    const key = canonicalKey(rounds);
    if (seen.has(key)) continue;
    seen.add(key);
    variants.push({ rounds, score });
  }
  return { variants, bestSeenScore };
}

function compactRound(r) {
  return {
    matches: r.courts.map(([t1, t2]) => [t1[0], t1[1], t2[0], t2[1]]),
  };
}

function main() {
  const easyConfigs = [
    { N: 4, C: 1, maxTries: 200 },
    { N: 8, C: 2, maxTries: 600 },
    { N: 12, C: 3, maxTries: 1500 },
    { N: 16, C: 4, maxTries: 3000 },
  ];
  const TARGET_VARIANTS = 20;
  const out = { version: 1, configs: {} };

  for (const { N, C, maxTries } of easyConfigs) {
    const key = `${N}-${C}`;
    console.log(`\n=== ${key}: N=${N}, C=${C}, max-tries=${maxTries} ===`);
    const t0 = Date.now();
    const { variants, bestSeenScore } = findOptimalSchedules(N, C, TARGET_VARIANTS, maxTries);
    const ms = Date.now() - t0;
    console.log(`  found ${variants.length} optimal variants in ${ms}ms`);
    if (bestSeenScore) {
      console.log(`  best score seen: partnerDups=${bestSeenScore.partnerDups}, oppMin=${bestSeenScore.oppMin}, oppMax=${bestSeenScore.oppMax}`);
    }
    if (variants[0]) {
      console.log(`  optimum score: partnerDups=${variants[0].score.partnerDups}, opp ${variants[0].score.oppMin}..${variants[0].score.oppMax}`);
    }
    out.configs[key] = {
      N, C,
      R: variants[0]?.rounds.length || (N - 1),
      stats: variants[0]?.score || bestSeenScore,
      variants: variants.map(v => ({ rounds: v.rounds.map(compactRound) })),
    };
  }

  const outPath = path.join(__dirname, '..', 'data', 'schedules.json');
  fs.writeFileSync(outPath, JSON.stringify(out));
  const sizeKb = (fs.statSync(outPath).size / 1024).toFixed(1);
  console.log(`\nWrote ${outPath} (${sizeKb} KB)`);
}

main();
