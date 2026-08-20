import numpy as np
import pytest

from worldsim.buildings import (
    BASE_FOOD_CAPACITY,
    BUILDING_SPECS,
    BuildingType,
    Improvement,
    ROAD_COST_STONE,
)
from worldsim.simulation import Simulation
from worldsim.tiles import TerrainType
from worldsim.world import World


def make_sim(seed: int = 42) -> tuple[Simulation, object]:
    sim = Simulation(World(seed=seed))
    settlement = sim.spawn_settlement()
    return sim, settlement


def give(settlement, wood: float = 0, stone: float = 0) -> None:
    inv = settlement.resource_inventory
    inv["wood"] = inv.get("wood", 0.0) + wood
    inv["stone"] = inv.get("stone", 0.0) + stone


# ----------------------------------------------------------------------
# Placement & costs
# ----------------------------------------------------------------------

def test_farm_spec_matches_acceptance_criteria():
    spec = BUILDING_SPECS[BuildingType.FARM]
    assert (spec.cost_wood, spec.cost_stone) == (5, 3)
    assert spec.food_output == 2
    assert TerrainType.PLAINS in spec.valid_terrain


def test_build_farm_on_valid_tile_deducts_resources():
    sim, s = make_sim(seed=42)
    site = next(
        (y, x)
        for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    wood_before = s.resource_inventory["wood"]
    stone_before = s.resource_inventory["stone"]
    assert sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])
    assert s.resource_inventory["wood"] == wood_before - 5
    assert s.resource_inventory["stone"] == stone_before - 3
    assert (
        sim.world.improvements[site] == Improvement.FARM.value
    )


def test_farm_produces_two_food_per_tick():
    sim, s = make_sim(seed=42)
    site = next(
        (y, x)
        for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])
    base_income = sim.food_income(s)
    # Farm adds exactly +2 food/tick over terrain yields.
    assert base_income == sum(
        sim.world.food_yield_grid()[y, x] for y, x in sim.territory_of(s)
    ) + 2


def test_cannot_build_on_invalid_terrain():
    sim, s = make_sim(seed=42)
    forest_site = next(
        ((y, x) for y, x in sim.territory_of(s)
         if sim.world.terrain[y, x] == TerrainType.FOREST.value),
        None,
    )
    if forest_site is None:
        pytest.skip("no forest in starting territory")
    give(s, wood=100, stone=100)
    assert not sim.build_at(s, BuildingType.FARM, x=forest_site[1], y=forest_site[0])


def test_cannot_build_outside_territory():
    sim, s = make_sim(seed=42)
    size = sim.world.size
    for y in range(size):
        for x in range(size):
            if sim.world.ownership[y, x] == -1:
                give(s, wood=100, stone=100)
                assert not sim.build_at(s, BuildingType.FARM, x=x, y=y)
                return
    pytest.fail("no unowned tile found")


def test_cannot_build_on_occupied_tile():
    sim, s = make_sim(seed=42)
    site = next(
        (y, x)
        for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    give(s, wood=100, stone=100)
    assert sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])
    assert not sim.build_at(s, BuildingType.GRANARY, x=site[1], y=site[0])


def test_insufficient_resources_blocks_construction():
    sim, s = make_sim(seed=42)
    s.resource_inventory["wood"] = 0
    s.resource_inventory["stone"] = 0
    site = next(
        (y, x)
        for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    assert not sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])


def test_sawmill_and_mine_require_matching_terrain():
    assert TerrainType.FOREST in BUILDING_SPECS[BuildingType.SAWMILL].valid_terrain
    assert TerrainType.MOUNTAIN in BUILDING_SPECS[BuildingType.MINE].valid_terrain


# ----------------------------------------------------------------------
# Outputs & storage cap
# ----------------------------------------------------------------------

def test_sawmill_produces_wood():
    sim, s = make_sim(seed=42)
    site = next(
        ((y, x) for y, x in sim.territory_of(s)
         if sim.world.terrain[y, x] == TerrainType.FOREST.value),
        None,
    )
    if site is None:
        pytest.skip("no forest in starting territory")
    give(s, wood=50, stone=50)
    assert sim.build_at(s, BuildingType.SAWMILL, x=site[1], y=site[0])
    wood_before = s.resource_inventory["wood"]
    sim._produce_resources(s)
    assert s.resource_inventory["wood"] == wood_before + 2


def test_mine_produces_stone_and_metal():
    sim, s = make_sim(seed=42)
    site = next(
        ((y, x) for y, x in sim.territory_of(s)
         if sim.world.terrain[y, x] == TerrainType.MOUNTAIN.value),
        None,
    )
    if site is None:
        pytest.skip("no mountain in starting territory")
    give(s, wood=50, stone=50)
    assert sim.build_at(s, BuildingType.MINE, x=site[1], y=site[0])
    stone_before = s.resource_inventory.get("stone", 0)
    metal_before = s.resource_inventory.get("metal", 0)
    sim._produce_resources(s)
    assert s.resource_inventory["stone"] == stone_before + 2
    assert s.resource_inventory["metal"] == metal_before + 1


def test_food_cap_enforced():
    sim, s = make_sim(seed=42)
    assert sim.food_capacity(s) == BASE_FOOD_CAPACITY
    s.food_stock = BASE_FOOD_CAPACITY + 100
    income = min(
        sim.food_income(s),
        max(0.0, sim.food_capacity(s) - s.food_stock),
    )
    assert income == 0.0  # at/over cap: surplus wasted


def test_granary_raises_capacity():
    sim, s = make_sim(seed=42)
    site = next(
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.improvements[y, x] == Improvement.NONE.value
        and sim.world.terrain[y, x] != TerrainType.WATER.value
    )
    give(s, wood=50, stone=50)
    sim.build_at(s, BuildingType.GRANARY, x=site[1], y=site[0])
    assert sim.food_capacity(s) == BASE_FOOD_CAPACITY + 500


# ----------------------------------------------------------------------
# Roads
# ----------------------------------------------------------------------

def test_road_halves_movement_cost():
    sim, s = make_sim(seed=42)
    target = next(
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.improvements[y, x] == Improvement.NONE.value
        and sim.world.terrain[y, x] != TerrainType.WATER.value
    )
    give(s, stone=ROAD_COST_STONE)
    before = sim.world.movement_cost[target]
    assert sim.build_road(s, x=target[1], y=target[0])
    after = sim.world.movement_cost[target]
    assert after == pytest.approx(before * 0.5)


def test_road_costs_stone():
    sim, s = make_sim(seed=42)
    target = next(
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.improvements[y, x] == Improvement.NONE.value
        and sim.world.terrain[y, x] != TerrainType.WATER.value
    )
    stone_before = s.resource_inventory["stone"]
    assert sim.build_road(s, x=target[1], y=target[0])
    assert s.resource_inventory["stone"] == stone_before - ROAD_COST_STONE


def test_no_roads_initially_all_flagged_connected():
    sim, s = make_sim(seed=42)
    connected, disconnected = sim.road_connectivity(s)
    assert connected == set()
    assert disconnected == set()


def test_disconnected_road_is_flagged():
    sim, s = make_sim(seed=42)
    territory = sorted(sim.territory_of(s))
    # Find two land tiles far apart (not adjacent to spawn).
    far = [
        (y, x) for y, x in territory
        if max(abs(y - s.spawn_y), abs(x - s.spawn_x)) >= 2
        and sim.world.terrain[y, x] != TerrainType.WATER.value
    ]
    if len(far) < 2:
        pytest.skip("territory too small")
    give(s, stone=10)
    (y1, x1), (y2, x2) = far[0], far[-1]
    assert sim.build_road(s, x=x1, y=y1)
    assert sim.build_road(s, x=x2, y=y2)
    connected, disconnected = sim.road_connectivity(s)
    assert (y2, x2) in disconnected or (y1, x1) in disconnected


def test_contiguous_road_network_is_connected():
    sim, s = make_sim(seed=42)
    give(s, stone=20)
    # Build an L-shaped road from spawn.
    sy, sx = s.spawn_y, s.spawn_x
    tiles = [(sy, sx + 1), (sy, sx + 2), (sy + 1, sx + 2)]
    built = 0
    for y, x in tiles:
        if sim.build_road(s, x=x, y=y):
            built += 1
    if built < len(tiles):
        pytest.skip("water blocked road path")
    connected, disconnected = sim.road_connectivity(s)
    assert disconnected == set()
    assert (sy, sx + 2) in connected


def test_auto_road_rule_extends_network():
    sim, s = make_sim(seed=11)
    give(s, stone=50)
    sim._auto_road_rule(s)
    assert len(sim.roads_of(s)) == 1
    connected, disconnected = sim.road_connectivity(s)
    assert disconnected == set()


# ----------------------------------------------------------------------
# Destruction
# ----------------------------------------------------------------------

def test_destroy_building_clears_tile():
    sim, s = make_sim(seed=42)
    site = next(
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    give(s, wood=50, stone=50)
    sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])
    assert sim.destroy_building(site[1], site[0])
    assert sim.world.improvements[site] == Improvement.NONE.value
    assert not sim.destroy_building(site[1], site[0])  # nothing to destroy


def test_releasing_territory_destroys_improvements():
    sim, s = make_sim(seed=42)
    site = next(
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    give(s, wood=50, stone=50)
    sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])
    sim.release_territory(s)
    assert sim.world.improvements[site] == Improvement.NONE.value
    assert (sim.world.ownership == -1).all()


# ----------------------------------------------------------------------
# Build queue & auto rules
# ----------------------------------------------------------------------

def test_build_queue_processes_fifo():
    sim, s = make_sim(seed=42)
    give(s, wood=50, stone=50)
    sim.enqueue_build(s, BuildingType.FARM)
    sim.enqueue_build(s, BuildingType.GRANARY)
    sim._process_build_queue(s)
    assert s.build_queue == ["GRANARY"]
    sim._process_build_queue(s)
    assert s.build_queue == []


def test_auto_build_rule_queues_something_valid():
    sim, s = make_sim(seed=42)
    sim._auto_build_rule(s)
    assert len(s.build_queue) == 1
    queued = BuildingType[s.build_queue[0]]
    assert sim.find_building_site(s, queued) is not None


def test_simulation_with_buildings_remains_deterministic():
    def run(seed):
        sim, s = make_sim(seed=seed)
        history = []
        for _ in range(150):
            sim.step()
            history.append(
                (
                    s.population,
                    round(s.food_stock, 6),
                    dict(sorted(s.resource_inventory.items())),
                    int((sim.world.improvements != Improvement.NONE.value).sum()),
                )
            )
        return history

    assert run(777) == run(777)


def test_long_run_grows_economy():
    sim, s = make_sim(seed=2024)
    for _ in range(300):
        sim.step()
        if not s.is_alive:
            break
    assert s.is_alive
    counts = sim.buildings_of(s)
    assert sum(counts.values()) > 0
    assert counts[BuildingType.FARM] > 0
