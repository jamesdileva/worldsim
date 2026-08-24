"""World visualization (Sprint 44): ASCII maps + PNG exports.

Contract:
- Pure functions of sim state — identical worlds produce byte-identical
  maps and identical PNG pixels (matplotlib Agg, fixed seed-independent
  rendering).
- Glyph priority (top wins): contamination marker > settlement > ruin >
  road > building > terrain glyph.
"""

from __future__ import annotations

from .buildings import Improvement
from .tiles import ASCII_GLYPHS, TerrainType


def _terrain_glyph(terrain_value) -> str:
    return ASCII_GLYPHS[TerrainType(int(terrain_value))]


def _improvement_glyph(improvement_value: int) -> str | None:
    if improvement_value == Improvement.NONE.value:
        return None
    if improvement_value == Improvement.ROAD.value:
        return "#"
    return "b"  # generic built improvement


def render_ascii_map(
    sim,
    x0: int | None = None,
    y0: int | None = None,
    x1: int | None = None,
    y1: int | None = None,
) -> str:
    """ASCII world map with settlement/road/ruin/contamination overlays.

    Optional window crop (inclusive corners, any order normalized).
    """
    world = sim.world
    size = world.size
    lo_x, hi_x = sorted((0 if x0 is None else x0,
                         size - 1 if x1 is None else x1))
    lo_y, hi_y = sorted((0 if y0 is None else y0,
                         size - 1 if y1 is None else y1))
    # Clamp to the world grid (callers may pass exclusive-style bounds).
    hi_x = min(hi_x, size - 1)
    hi_y = min(hi_y, size - 1)
    lo_x = max(lo_x, 0)
    lo_y = max(lo_y, 0)

    # Overlays indexed for O(1) lookup during the scan.
    settlement_at: dict[tuple[int, int], str] = {}
    for s in sim.settlements:
        if s.is_alive:
            settlement_at[(s.spawn_y, s.spawn_x)] = s.name[0].upper()
    ruins_at = {
        (r.spawn_y, r.spawn_x) for r in getattr(sim, "ruins", [])
    }
    contamination_at: set[tuple[int, int]] = set()
    tick = sim.tick
    for zone in getattr(sim, "contamination_zones", []):
        if not zone.is_active(tick):
            continue
        for y in range(max(0, zone.center_y - zone.radius),
                       min(size, zone.center_y + zone.radius + 1)):
            for x in range(max(0, zone.center_x - zone.radius),
                           min(size, zone.center_x + zone.radius + 1)):
                contamination_at.add((y, x))

    lines = []
    for y in range(lo_y, hi_y + 1):
        row_chars = []
        for x in range(lo_x, hi_x + 1):
            if (y, x) in contamination_at:
                row_chars.append("!")
                continue
            glyph = settlement_at.get((y, x))
            if glyph is None and (y, x) in ruins_at:
                glyph = "X"
            if glyph is None:
                glyph = _improvement_glyph(int(world.improvements[y, x]))
            if glyph is None:
                glyph = _terrain_glyph(world.terrain[y, x])
            row_chars.append(glyph)
        lines.append("".join(row_chars))

    header = (
        f"map {lo_x},{lo_y}..{hi_x},{hi_y} tick={tick} "
        f"seed={world.seed}"
    )
    return "\n".join([header] + lines)


LEGEND = (
    "legend: letters=settlements  #=road  b=building  X=ruin  "
    "!=contamination  others=terrain glyphs"
)


def render_settlement_panel(sim, settlement) -> str:
    """Compact multi-line panel for one settlement (deterministic)."""
    from .simulation import Simulation  # noqa: F401 - typing only

    lines = [
        f"{settlement.name} "
        f"[{settlement.personality.get('archetype', 'balanced')}] "
        f"@ ({settlement.spawn_x},{settlement.spawn_y})",
        f"  pop {settlement.population} | army {settlement.army:.0f} | "
        f"fort {settlement.fort_level} | era {settlement.era}"
        + (" | FROZEN" if settlement.frozen else ""),
        f"  food {settlement.food_stock:.0f} "
        f"(net {settlement.net_food_rate:+.1f}) | happy "
        f"{settlement.happiness:.2f}",
        f"  techs: "
        f"{', '.join(sorted(settlement.technologies)) or 'none'}",
    ]
    return "\n".join(lines)


# Fixed palette keyed by terrain value (stable across runs/platforms).
TERRAIN_COLORS = {
    int(TerrainType.WATER): (0.29, 0.49, 0.75),
    int(TerrainType.DESERT): (0.93, 0.86, 0.60),
    int(TerrainType.PLAINS): (0.68, 0.85, 0.45),
    int(TerrainType.FERTILE): (0.36, 0.70, 0.32),
    int(TerrainType.FOREST): (0.20, 0.45, 0.22),
    int(TerrainType.MOUNTAIN): (0.55, 0.53, 0.50),
}


def export_map_png(sim, path, dpi: int = 100) -> str:
    """Render the world to a PNG: terrain colors, roads/buildings marks,
    settlements as population-scaled markers."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    world = sim.world
    size = world.size
    colors = np.zeros((size, size, 3), dtype=float)
    for value, rgb in TERRAIN_COLORS.items():
        colors[world.terrain == value] = rgb

    fig, ax = plt.subplots(figsize=(size / 20, size / 20), dpi=dpi)
    ax.imshow(colors, interpolation="nearest")

    # Improvements: roads dark dots, buildings brown squares.
    roads = np.argwhere(world.improvements == Improvement.ROAD.value)
    if len(roads):
        ax.scatter(roads[:, 1], roads[:, 0], s=0.5, c="black", zorder=2)
    for btype in (Improvement.FARM, Improvement.SAWMILL, Improvement.MINE,
                  Improvement.GRANARY):
        tiles_ = np.argwhere(world.improvements == btype.value)
        if len(tiles_):
            ax.scatter(tiles_[:, 1], tiles_[:, 0], s=1.2, c="saddlebrown",
                       marker="s", zorder=3)

    # Settlements: population-scaled circles with name labels.
    living = [s for s in sim.settlements if s.is_alive]
    if living:
        xs = [s.spawn_x for s in living]
        ys = [s.spawn_y for s in living]
        sizes = [max(12.0, s.population * 1.6) for s in living]
        ax.scatter(xs, ys, s=sizes, c="crimson", edgecolors="white",
                   linewidths=0.5, zorder=5)
        for s in living:
            ax.annotate(s.name, (s.spawn_x, s.spawn_y), fontsize=4,
                        color="white", ha="center", va="center", zorder=6)

    # Ruins as gray X's.
    for r in getattr(sim, "ruins", []):
        ax.annotate("X", (r.spawn_x, r.spawn_y), fontsize=5, color="gray",
                    ha="center", va="center", zorder=4)

    ax.set_title(
        f"{len(living)} settlements | tick {sim.tick} | seed {world.seed}",
        fontsize=7,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return str(path)


def export_population_chart(sim, path, dpi: int = 100) -> str:
    """Per-civilization population curves from epoch history (Sprint 45)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .histories import population_curves

    ticks, curves = population_curves(sim)
    if not ticks:
        raise ValueError(
            "no epoch history recorded — step the simulation first"
        )
    fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=dpi)
    for name in sorted(curves):
        samples = curves[name]
        # Pad short curves (settlements born mid-run) to full length.
        padded = [None] * (len(ticks) - len(samples)) + samples
        xs = [t for t, v in zip(ticks, padded) if v is not None]
        ys = [v for v in padded if v is not None]
        ax.plot(xs, ys, label=name, linewidth=1.0)
    ax.set_title("Civilization populations", fontsize=8)
    ax.set_xlabel("tick", fontsize=7)
    ax.set_ylabel("population", fontsize=7)
    ax.tick_params(labelsize=6)
    if curves:
        ax.legend(fontsize=5)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return str(path)
