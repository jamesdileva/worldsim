"""World model: grid state, generation, stats, and ASCII rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import terrain
from .tiles import ASCII_GLYPHS, TERRAIN_PROFILES, TerrainType

DEFAULT_SIZE = 256
UNOWNED = -1


@dataclass
class World:
    seed: int
    size: int = DEFAULT_SIZE
    tick: int = 0
    elevation: np.ndarray = field(init=False)
    moisture: np.ndarray = field(init=False)
    terrain: np.ndarray = field(init=False)
    # Tile ownership: settlement index, or UNOWNED (-1).
    ownership: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.elevation = terrain._noise_grid(
            self.seed, self.size, terrain.ELEVATION_SEED_OFFSET, scale=6.0
        )
        self.moisture = terrain._noise_grid(
            self.seed, self.size, terrain.MOISTURE_SEED_OFFSET, scale=5.0
        )
        self.terrain = terrain.classify(self.elevation, self.moisture)
        self.ownership = np.full((self.size, self.size), UNOWNED, dtype=np.int32)

    @property
    def movement_cost(self) -> np.ndarray:
        costs = np.array(
            [TERRAIN_PROFILES[TerrainType(t)].movement_cost for t in range(len(TerrainType))],
            dtype=np.float64,
        )
        return costs[self.terrain]

    def resource_yield(self) -> dict[str, int]:
        """Aggregate base resource yields across the whole world."""
        totals = {"food": 0, "wood": 0, "stone": 0, "metal": 0}
        for tt in TerrainType:
            profile = TERRAIN_PROFILES[tt]
            count = int((self.terrain == tt.value).sum())
            for res in totals:
                per_tile = getattr(profile, res)
                if per_tile > 0:
                    totals[res] += per_tile * count
        return totals

    def food_yield_grid(self) -> np.ndarray:
        """Per-tile food yield as an int array."""
        yields = np.array(
            [TERRAIN_PROFILES[TerrainType(t)].food for t in range(len(TerrainType))],
            dtype=np.int32,
        )
        return yields[self.terrain]

    def terrain_breakdown(self) -> dict[TerrainType, int]:
        counts = np.bincount(self.terrain.ravel(), minlength=len(TerrainType))
        return {TerrainType(i): int(counts[i]) for i in range(len(TerrainType))}

    def render_ascii(self) -> str:
        return "\n".join(
            "".join(ASCII_GLYPHS[TerrainType(v)] for v in row) for row in self.terrain
        )
