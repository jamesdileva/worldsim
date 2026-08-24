"""World model: grid state, generation, stats, and ASCII rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import terrain
from .buildings import Improvement, ROAD_MOVEMENT_MULTIPLIER
from .tiles import ASCII_GLYPHS, TERRAIN_PROFILES, TerrainType

DEFAULT_SIZE = 256
UNOWNED = -1

# Terrain generation cache: opensimplex sampling is pure-Python (~3 s per
# world); identical (seed, size) requests reuse the generated arrays (copied
# so sim mutations never poison the cache).
_GENERATION_CACHE: dict[tuple[int, int], tuple] = {}
_GENERATION_CACHE_MAX = 64


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
    # Tile improvements (roads, buildings): Improvement enum values.
    improvements: np.ndarray = field(init=False)
    _food_grid: np.ndarray | None = field(init=False, default=None)
    # Sprint 40: god-blessed land — (y, x) -> bonus food yield added on
    # top of the terrain profile. Persisted with world state.
    tile_food_bonus: dict[tuple[int, int], float] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        key = (self.seed, self.size)
        cached = _GENERATION_CACHE.get(key)
        if cached is not None:
            self.elevation, self.moisture, self.terrain = (
                cached[0].copy(),
                cached[1].copy(),
                cached[2].copy(),
            )
        else:
            self.elevation = terrain._noise_grid(
                self.seed, self.size, terrain.ELEVATION_SEED_OFFSET, scale=6.0
            )
            self.moisture = terrain._noise_grid(
                self.seed, self.size, terrain.MOISTURE_SEED_OFFSET, scale=5.0
            )
            self.terrain = terrain.classify(self.elevation, self.moisture)
            if len(_GENERATION_CACHE) >= _GENERATION_CACHE_MAX:
                _GENERATION_CACHE.clear()
            _GENERATION_CACHE[key] = (
                self.elevation.copy(),
                self.moisture.copy(),
                self.terrain.copy(),
            )
        self.ownership = np.full((self.size, self.size), UNOWNED, dtype=np.int32)
        self.improvements = np.full(
            (self.size, self.size), Improvement.NONE.value, dtype=np.int8
        )

    @property
    def movement_cost(self) -> np.ndarray:
        costs = np.array(
            [TERRAIN_PROFILES[TerrainType(t)].movement_cost for t in range(len(TerrainType))],
            dtype=np.float64,
        )
        result = costs[self.terrain]
        result[self.improvements == Improvement.ROAD.value] *= (
            ROAD_MOVEMENT_MULTIPLIER
        )
        return result

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
        """Per-tile food yield as an int array (cached; terrain is static)."""
        if self._food_grid is None:
            yields = np.array(
                [
                    TERRAIN_PROFILES[TerrainType(t)].food
                    for t in range(len(TerrainType))
                ],
                dtype=np.int32,
            )
            self._food_grid = yields[self.terrain]
        return self._food_grid

    def terrain_breakdown(self) -> dict[TerrainType, int]:
        counts = np.bincount(self.terrain.ravel(), minlength=len(TerrainType))
        return {TerrainType(i): int(counts[i]) for i in range(len(TerrainType))}

    def render_ascii(self) -> str:
        return "\n".join(
            "".join(ASCII_GLYPHS[TerrainType(v)] for v in row) for row in self.terrain
        )
