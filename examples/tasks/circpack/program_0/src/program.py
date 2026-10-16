"""The baseline packing: 26 centers on a trimmed 5 x 6 grid, every circle as
large as its nearest neighbour and the border allow.

Scores 2.166667 and is correct. Read-only: it is the reference a campaign is
measured against, and a candidate is cloned from it rather than written over it.

It is deliberately a *layout* and not a search. Every radius comes out at
exactly 1/12, because on this grid the nearest-neighbour distance and the
distance to the border are the same number — which is also why it cannot be
improved by nudging: 2.1667 is what 26 equal circles on a rectangular grid are
worth, and the best equal-radius packing of 26 circles is only about 2.35.
Beating that means circles of different sizes.
"""

from __future__ import annotations

import math

#: 30 grid slots, 26 of them used. Trimming the last column rather than the last
#: row keeps the nearest-neighbour distance uniform at 1/6 across the layout.
ROWS, COLS, N = 5, 6, 26


def grid_centers() -> list[tuple[float, float]]:
    """Column-major, so the trimmed slots all come off one edge."""
    centers = [((i + 0.5) / COLS, (j + 0.5) / ROWS) for i in range(COLS) for j in range(ROWS)]
    return centers[:N]


def largest_radius(index: int, centers: list[tuple[float, float]]) -> float:
    """The biggest circle at this center that stays in the square and off its neighbours."""
    x, y = centers[index]
    border = min(x, y, 1.0 - x, 1.0 - y)
    nearest = min(math.dist(centers[index], centers[other])
                  for other in range(len(centers)) if other != index)
    return min(border, nearest / 2.0)


def solution() -> tuple[list[tuple[float, float]], list[float], float]:
    """One radius for every circle: the smallest any of them can have.

    This grid is uniform — the nearest-neighbour distance and the border
    distance are both 1/12 everywhere, including on the trimmed edge — so
    computing each circle's radius separately would return the same number 26
    times with different last bits, and ``radius_spread`` would report float
    noise as though it were a measurement. Taking the minimum once makes the
    layout exactly what it is: 26 equal circles.
    """
    centers = grid_centers()
    radius = min(largest_radius(i, centers) for i in range(len(centers)))
    radii = [radius] * len(centers)
    return centers, radii, math.fsum(radii)


if __name__ == "__main__":
    _, radii, total = solution()
    print(f"{len(radii)} circles, sum of radii {total:.6f}, "
          f"min {min(radii):.6f}, max {max(radii):.6f}")
