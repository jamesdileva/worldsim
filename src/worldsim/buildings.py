"""Building types, costs, outputs, and placement rules (Sprint 3).

Spec'd values from docs/detailed_sprint_plan.md Sprint 3:
- Farm: cost 5 wood + 3 stone, +2 food/tick, built on Plains
Everything else is a documented extension decision (see notes.md).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .tiles import TerrainType


class BuildingType(enum.IntEnum):
    FARM = 0
    SAWMILL = 1
    MINE = 2
    GRANARY = 3


class Improvement(enum.IntEnum):
    NONE = -1
    ROAD = 0
    FARM = 1
    SAWMILL = 2
    MINE = 3
    GRANARY = 4


IMPROVEMENT_TO_BUILDING: dict[Improvement, BuildingType] = {
    Improvement.FARM: BuildingType.FARM,
    Improvement.SAWMILL: BuildingType.SAWMILL,
    Improvement.MINE: BuildingType.MINE,
    Improvement.GRANARY: BuildingType.GRANARY,
}


@dataclass(frozen=True)
class BuildingSpec:
    name: str
    cost_wood: int
    cost_stone: int
    food_output: int = 0
    wood_output: int = 0
    stone_output: int = 0
    metal_output: int = 0
    food_capacity: int = 0
    valid_terrain: tuple[TerrainType, ...] = ()
    requires_land: bool = True


BUILDING_SPECS: dict[BuildingType, BuildingSpec] = {
    BuildingType.FARM: BuildingSpec(
        name="Farm",
        cost_wood=5,
        cost_stone=3,
        food_output=2,
        valid_terrain=(TerrainType.PLAINS, TerrainType.FERTILE),
    ),
    BuildingType.SAWMILL: BuildingSpec(
        name="Sawmill",
        cost_wood=4,
        cost_stone=2,
        wood_output=2,
        valid_terrain=(TerrainType.FOREST,),
    ),
    BuildingType.MINE: BuildingSpec(
        name="Mine",
        cost_wood=6,
        cost_stone=4,
        stone_output=2,
        metal_output=1,
        valid_terrain=(TerrainType.MOUNTAIN,),
    ),
    BuildingType.GRANARY: BuildingSpec(
        name="Granary",
        cost_wood=5,
        cost_stone=5,
        food_capacity=500,
        valid_terrain=(
            TerrainType.PLAINS,
            TerrainType.FERTILE,
            TerrainType.FOREST,
            TerrainType.DESERT,
        ),
    ),
}

ROAD_COST_STONE = 1
# Roads halve terrain movement cost (detailed_sprint_plan.md Sprint 3).
ROAD_MOVEMENT_MULTIPLIER = 0.5

BASE_FOOD_CAPACITY = 500
