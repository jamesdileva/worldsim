"""Tile definitions: terrain types, movement costs, and resource yields.

Implements the terrain characteristics table from docs/architecture_detailed.md §3.1.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class TerrainType(enum.IntEnum):
    WATER = 0
    DESERT = 1
    PLAINS = 2
    FERTILE = 3
    FOREST = 4
    MOUNTAIN = 5


@dataclass(frozen=True)
class TerrainProfile:
    movement_cost: float
    food: int
    wood: int
    stone: int
    metal: int


# Per-tile base yields and movement costs (architecture_detailed.md §3.1).
TERRAIN_PROFILES: dict[TerrainType, TerrainProfile] = {
    TerrainType.WATER: TerrainProfile(movement_cost=5.0, food=0, wood=0, stone=0, metal=0),
    TerrainType.DESERT: TerrainProfile(movement_cost=1.5, food=-1, wood=0, stone=0, metal=0),
    TerrainType.PLAINS: TerrainProfile(movement_cost=1.0, food=1, wood=0, stone=0, metal=0),
    TerrainType.FERTILE: TerrainProfile(movement_cost=1.0, food=2, wood=0, stone=0, metal=0),
    TerrainType.FOREST: TerrainProfile(movement_cost=1.2, food=1, wood=3, stone=0, metal=0),
    TerrainType.MOUNTAIN: TerrainProfile(movement_cost=2.0, food=0, wood=0, stone=2, metal=1),
}

# ASCII glyphs used by the text renderer.
ASCII_GLYPHS: dict[TerrainType, str] = {
    TerrainType.WATER: "~",
    TerrainType.DESERT: ".",
    TerrainType.PLAINS: ",",
    TerrainType.FERTILE: "\"",
    TerrainType.FOREST: "T",
    TerrainType.MOUNTAIN: "^",
}
