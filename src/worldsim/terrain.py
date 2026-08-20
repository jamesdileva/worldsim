"""Seeded terrain generation.

Two independent noise layers (elevation, moisture), each derived from the
master seed so generation is fully deterministic (architecture_detailed.md A1/A4).
"""

from __future__ import annotations

import numpy as np
from opensimplex import OpenSimplex

from .tiles import TerrainType

# Elevation/moisture thresholds for biome classification.
WATER_LEVEL = 0.30
MOUNTAIN_LEVEL = 0.72
DESERT_MOISTURE = 0.35
FOREST_MOISTURE = 0.55
FERTILE_MOISTURE = 0.62

ELEVATION_SEED_OFFSET = 0
MOISTURE_SEED_OFFSET = 1_000_000


def _noise_grid(seed: int, size: int, offset: int, scale: float) -> np.ndarray:
    """Sample 2-octave simplex noise over an evenly spaced grid."""
    noise = OpenSimplex(seed=(seed + offset) & 0x7FFFFFFF)
    coords = np.linspace(0.0, scale, size, endpoint=False)
    base = noise.noise2array(coords, coords)
    detail = noise.noise2array(coords * 3.1 + 17.0, coords * 3.1 + 23.0)
    combined = base + 0.35 * detail
    lo = float(combined.min())
    hi = float(combined.max())
    return (combined - lo) / (hi - lo)


def classify(elevation: np.ndarray, moisture: np.ndarray) -> np.ndarray:
    """Map elevation/moisture fields to terrain types (vectorized)."""
    terrain = np.full(elevation.shape, TerrainType.PLAINS.value, dtype=np.int8)

    is_water = elevation < WATER_LEVEL
    is_mountain = elevation >= MOUNTAIN_LEVEL
    land = ~(is_water | is_mountain)

    terrain[is_water] = TerrainType.WATER.value
    terrain[is_mountain] = TerrainType.MOUNTAIN.value

    dry = land & (moisture < DESERT_MOISTURE)
    forest = land & (moisture >= FOREST_MOISTURE)
    fertile = land & (moisture >= FERTILE_MOISTURE)

    terrain[dry] = TerrainType.DESERT.value
    terrain[forest] = TerrainType.FOREST.value
    terrain[fertile] = TerrainType.FERTILE.value

    return terrain
