"""Region selectors for God Mode operations (Sprint 40).

Pure geometry helpers: deterministic tile enumeration (sorted row-major),
settlement selection by spawn position. No state mutation here.
"""

from __future__ import annotations

from .settlement import Settlement


def circle_tiles(
    size: int, cx: int, cy: int, radius: int
) -> list[tuple[int, int]]:
    """All in-bounds tiles within Chebyshev radius of the center,
    sorted row-major by (y, x)."""
    tiles = [
        (y, x)
        for y in range(max(0, cy - radius), min(size, cy + radius + 1))
        for x in range(max(0, cx - radius), min(size, cx + radius + 1))
    ]
    return sorted(tiles)


def rect_tiles(
    size: int, x0: int, y0: int, x1: int, y1: int
) -> list[tuple[int, int]]:
    """Tiles in the normalized rectangle (corners may be given in any
    order), sorted row-major by (y, x)."""
    lo_x, hi_x = sorted((x0, x1))
    lo_y, hi_y = sorted((y0, y1))
    return [
        (y, x)
        for y in range(max(0, lo_y), min(size, hi_y + 1))
        for x in range(max(0, lo_x), min(size, hi_x + 1))
    ]


def settlements_with_spawns_in(
    sim, tiles: set[tuple[int, int]]
) -> list[Settlement]:
    """Alive settlements whose spawn tile lies in the region, sorted by
    name for deterministic processing order."""
    return sorted(
        (s for s in sim.settlements if s.is_alive and (s.spawn_y, s.spawn_x) in tiles),
        key=lambda s: s.name,
    )
