# Circle packing, n = 26

The same benchmark SakanaAI's Agentic Scientist ships in its own
`examples/circpack`, so a campaign run here and a campaign run there are scored
by the same number. Everything else about the two is different.

## Goal

Place 26 circles inside the unit square `[0,1] x [0,1]` so that no two overlap,
no circle leaves the square, and the **sum of the radii** is as large as
possible. The sum of the radii is the score.

## Candidate interface

A candidate is one Python file defining:

```python
def solution() -> tuple[list[tuple[float, float]], list[float], float]:
    """Return (centers, radii, sum_of_radii)."""
```

* `centers` — 26 `(x, y)` pairs.
* `radii` — 26 non-negative floats, one per center.
* `sum_of_radii` — their sum, which must equal `sum(radii)`.

Standard library only. No file writes, no network, no subprocesses, no reading
the evaluator's source: a candidate that inspects its own grader is not a
packing, it is a cheat, and the evaluator's validation is deliberately the same
arithmetic a reader would do by hand.

## Evaluating

```sh
python examples/tasks/circpack/evaluate.py \
  --program <candidate.py> --results-dir <dir>
```

Writes exactly two files into `<dir>`:

* `metrics.json` — `combined_score` (the sum of the radii), `num_circles`,
  `min_radius`, `max_radius`, `radius_spread`.
* `correct.json` — `correct` (a boolean) and `message` (why not).

The evaluator is deterministic: the same candidate produces the same two files,
byte for byte. It runs the candidate in-process and it is **not** on a clock —
there is no timeout, because nothing in this pipeline is. A candidate that loops
forever hangs the turn, and `session/cancel` ends it, which is the same contract
the loop itself runs under.

## Baseline

`program_0/src/program.py` lays the 26 centers out as a 5 x 6 grid and gives
every circle the largest radius its nearest neighbour and the border allow. It
scores **2.166667** and it is correct. It is the reference, it is read-only, and
beating it is the first thing a campaign has to do.

Beating it is not hard and that is the point: 2.1667 is what 26 *equal* circles
achieve on a rectangular grid, and the best equal-radius packing of 26 circles
is only about 2.35. Anything past that has to come from circles of *different*
sizes — a few large ones and several small ones filling the gaps between them —
which is a search problem rather than a layout problem.

## Task rubrics

The campaign-level contract, in the shape the state file holds them. These are
what the whole campaign is judged on; each round's *idea* carries its own
rubrics, written by the explorer, which are how that round is judged.

```json
[
  {"id": "exposes_solution", "type": "implementation",
   "description": "the candidate defines solution() returning (centers, radii, sum_of_radii) for exactly 26 circles"},
  {"id": "correct_packing", "type": "metric",
   "description": "correct.json reports correct true — no overlap, nothing outside the unit square, and the reported sum equals the sum of the radii"},
  {"id": "beats_program_0", "type": "metric",
   "description": "metrics.json reports combined_score strictly greater than 2.166667"}
]
```
