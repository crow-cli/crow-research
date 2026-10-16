"""The evaluator for the n=26 circle-packing task.

One candidate in, two JSON files out. It is the only thing in a campaign that
gets to say whether a packing is a packing and what it scores, which is what
makes ``correct`` and ``combined_score`` evidence rather than an agent's
opinion: the verifier checks rubrics against these two files, and the host moves
``best`` on the numbers in them.

Deterministic on purpose — no clock, no randomness, no timestamps, no paths in
the output, and keys sorted, so the same candidate produces the same two files
byte for byte. A campaign that cannot reproduce its own scores cannot bank one.

It runs the candidate in-process and it is **not** on a timeout. Nothing in this
pipeline is on a clock: a candidate that loops forever hangs the turn, and
``session/cancel`` ends it. That is the same contract the loop runs under, and
putting a clock here would only mean the evaluator deciding how long a scientist
is allowed to think.

    python evaluate.py --program program_1/src/program.py --results-dir program_1
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import traceback
from pathlib import Path
from typing import Any, Optional, Sequence

#: The task is 26 circles. Not a default, not a knob.
N_CIRCLES = 26

#: Tolerance for the geometric checks. Tight enough that a packing which merely
#: looks non-overlapping at four decimal places does not pass, loose enough that
#: a radius computed as ``1/12`` is not failed for its last bit.
ATOL = 1e-9


def load_solution(program: str | Path):
    """Import ``solution`` out of a candidate file.

    The candidate's own directory goes on ``sys.path`` first, so a program split
    across sibling modules can import them the way it would standalone.
    """
    path = Path(program).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"no candidate at {path}")
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location("_circpack_candidate", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load a module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_circpack_candidate"] = module
    spec.loader.exec_module(module)
    try:
        return getattr(module, "solution")
    except AttributeError:
        raise ImportError(f"{path.name} defines no solution() — that is the candidate interface") from None


def _floats(values: Any, what: str) -> list[float]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{what} is a sequence of numbers, got a string")
    if not isinstance(values, Sequence):
        raise TypeError(f"{what} is a sequence, got {type(values).__name__}")
    out: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{what}[{index}] is a number, got {value!r}")
        out.append(float(value))
    return out


def validate(centers: Any, radii: Any, reported: Any) -> tuple[bool, str]:
    """Is this a packing of 26 circles in the unit square, and is the sum honest?

    Returns ``(correct, message)``. The message is what lands in
    ``correct.json``, so it is written for whoever has to fix the candidate: it
    names the circle, not just the rule.
    """
    if isinstance(centers, (str, bytes)) or not isinstance(centers, Sequence):
        return False, f"centers is a sequence of (x, y) pairs, got {type(centers).__name__}"
    if len(centers) != N_CIRCLES:
        return False, f"expected {N_CIRCLES} centers, got {len(centers)}"
    points: list[tuple[float, float]] = []
    for index, point in enumerate(centers):
        if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
            return False, f"centers[{index}] is an (x, y) pair, got {point!r}"
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            return False, f"centers[{index}] is a pair of numbers, got {point!r}"
        if not (math.isfinite(x) and math.isfinite(y)):
            return False, f"centers[{index}] is not finite: ({x}, {y})"
        points.append((x, y))
    try:
        rs = _floats(radii, "radii")
    except TypeError as error:
        return False, str(error)
    if len(rs) != N_CIRCLES:
        return False, f"expected {N_CIRCLES} radii, got {len(rs)}"
    for index, value in enumerate(rs):
        if not math.isfinite(value):
            return False, f"radii[{index}] is not finite: {value}"
        if value < 0.0:
            return False, f"radii[{index}] is negative: {value}"
    if isinstance(reported, bool) or not isinstance(reported, (int, float)):
        return False, f"sum_of_radii is a number, got {reported!r}"
    total = math.fsum(rs)
    if not math.isclose(total, float(reported), abs_tol=1e-6):
        return False, f"sum_of_radii was reported as {float(reported)!r} but the radii sum to {total!r}"
    for index, (x, y) in enumerate(points):
        radius = rs[index]
        if x - radius < -ATOL or x + radius > 1 + ATOL or y - radius < -ATOL or y + radius > 1 + ATOL:
            return False, (f"circle {index} at ({x:.6f}, {y:.6f}) r={radius:.6f} "
                           f"leaves the unit square")
    for i in range(N_CIRCLES):
        for j in range(i + 1, N_CIRCLES):
            gap = math.dist(points[i], points[j])
            if gap < rs[i] + rs[j] - ATOL:
                return False, (f"circles {i} and {j} overlap: centers {gap:.6f} apart, "
                               f"radii sum to {rs[i] + rs[j]:.6f}")
    return True, "26 circles, no overlap, all inside the unit square, and the reported sum is the sum of the radii"


def metrics_for(radii: Sequence[float]) -> dict[str, Any]:
    """The public numbers. ``combined_score`` is the one the campaign ranks on."""
    values = [float(r) for r in radii]
    return {
        "combined_score": math.fsum(values),
        "num_circles": len(values),
        "min_radius": min(values),
        "max_radius": max(values),
        "radius_spread": max(values) - min(values),
    }


def evaluate(program: str | Path, results_dir: str | Path) -> dict[str, Any]:
    """Run one candidate and write its two files. Returns what it wrote.

    A candidate that raises is not an evaluator failure: it is an attempt with
    ``combined_score`` 0.0 and ``correct`` false, recorded with the error, which
    is the honest outcome and the one the campaign can learn from.
    """
    out = Path(results_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics: dict[str, Any] = {"combined_score": 0.0, "num_circles": 0,
                               "min_radius": 0.0, "max_radius": 0.0, "radius_spread": 0.0}
    correct, message = False, ""
    try:
        centers, radii, reported = load_solution(program)()
        correct, message = validate(centers, radii, reported)
        metrics = metrics_for(radii)
        if not correct:
            metrics["error"] = message
    except Exception as error:  # a candidate is untrusted code by definition
        message = f"{type(error).__name__}: {error}"
        metrics = {**metrics, "error": message}
        print(traceback.format_exc(), file=sys.stderr)
    _write(out / "metrics.json", metrics)
    _write(out / "correct.json", {"correct": correct, "message": message})
    return {"metrics": metrics, "correct": correct, "message": message}


def _write(path: Path, doc: dict[str, Any]) -> None:
    """Sorted keys and no trailing whitespace, so the bytes are reproducible."""
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--program", required=True, help="candidate file defining solution()")
    parser.add_argument("--results-dir", required=True, help="where metrics.json and correct.json go")
    parser.add_argument("--quiet", action="store_true", help="only write the files")
    args = parser.parse_args(argv)
    result = evaluate(args.program, args.results_dir)
    if not args.quiet:
        score = result["metrics"]["combined_score"]
        print(f"combined_score {score:.6f} | correct {str(result['correct']).lower()}")
        print(f"  {result['message']}")
        print(f"wrote {Path(args.results_dir) / 'metrics.json'}")
        print(f"wrote {Path(args.results_dir) / 'correct.json'}")
    # Zero either way: a failed packing is a result, not a crash, and an agent
    # that reads the exit code instead of correct.json learns the wrong lesson.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
