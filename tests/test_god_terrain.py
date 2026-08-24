"""Sprint 41: terrain manipulation — god terraforming."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.buildings import Improvement
from worldsim.simulation import Simulation
from worldsim.tiles import TerrainType
from worldsim.world import World


def _sim(n=1, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    for _ in range(10):
        sim.step()
    return sim


def _owned_unimproved_tile(sim, s):
    ty, tx = next(
        t for t in sim.territory_of(s)
        if sim.world.improvements[t[0], t[1]] == -1
    )
    return ty, tx


# ----------------------------------------------------------------------
# Single-tile terraforming
# ----------------------------------------------------------------------

def test_terraform_changes_terrain_and_invalidates_food_cache():
    sim = _sim()
    s = sim.settlements[0]
    ty, tx = _owned_unimproved_tile(sim, s)
    # Make it fertile first so there is yield to lose.
    sim.world.terrain[ty, tx] = TerrainType.FERTILE.value
    sim.world._food_grid = None
    sim._invalidate_cache()

    income_fertile = sim.food_income(s)
    sim.god_terraform(tx, ty, "mountain")
    income_after = sim.food_income(s)
    assert sim.world.terrain[ty, tx] == TerrainType.MOUNTAIN.value
    assert income_after < income_fertile  # cache invalidated same tick


def test_incompatible_building_is_lost():
    sim = _sim()
    s = sim.settlements[0]
    ty, tx = _owned_unimproved_tile(sim, s)
    sim.world.improvements[ty, tx] = Improvement.FARM.value
    before, after = sim.god_terraform(tx, ty, "mountain")
    assert after["improvement_lost"] is True
    assert sim.world.improvements[ty, tx] == -1


def test_compatible_building_survives():
    sim = _sim()
    s = sim.settlements[0]
    ty, tx = _owned_unimproved_tile(sim, s)
    sim.world.improvements[ty, tx] = Improvement.MINE.value
    sim.god_terraform(tx, ty, "mountain")
    assert sim.world.improvements[ty, tx] == Improvement.MINE.value
    record = sim._terraform_tile(tx, ty, "mountain")
    assert record["after"]["improvement_lost"] is False


def test_road_survives_land_change_but_not_water():
    sim = _sim()
    s = sim.settlements[0]
    ty, tx = _owned_unimproved_tile(sim, s)
    sim.world.improvements[ty, tx] = Improvement.ROAD.value
    sim.god_terraform(tx, ty, "desert")
    assert sim.world.improvements[ty, tx] == Improvement.ROAD.value
    sim.god_terraform(tx, ty, "water")
    assert sim.world.improvements[ty, tx] == -1


def test_unknown_terrain_and_oob_rejected():
    sim = _sim()
    with pytest.raises(ValueError):
        sim.god_terraform(10, 10, "lava")
    with pytest.raises(ValueError):
        sim.god_terraform(999, 999, "plains")


def test_movement_cost_reflects_new_terrain():
    sim = _sim()
    ty, tx = _owned_unimproved_tile(sim, sim.settlements[0])
    sim.god_terraform(tx, ty, "mountain")
    from worldsim.tiles import TERRAIN_PROFILES

    expected = TERRAIN_PROFILES[TerrainType.MOUNTAIN].movement_cost
    assert sim.world.movement_cost[ty, tx] == pytest.approx(expected)


# ----------------------------------------------------------------------
# Region variant + audit + determinism
# ----------------------------------------------------------------------

def test_region_terraform_counts_changes_and_losses():
    from worldsim.regions import circle_tiles

    sim = _sim()
    s = sim.settlements[0]
    tiles = [t for t in sim.territory_of(s)]
    ty1, tx1 = tiles[0]
    sim.world.improvements[ty1, tx1] = Improvement.FARM.value
    region = set(circle_tiles(64, tx1, ty1, 3))
    expected_losses = sum(
        1 for ty, tx in region
        if 0 <= ty < 64 and 0 <= tx < 64
        and sim.world.improvements[ty, tx] != -1
    )
    _before, after = sim.god_terraform_region(
        tx1, ty1, radius=3, terrain_name="mountain")
    assert after["tiles_changed"] >= 1
    # Roads survive land changes; buildings incompatible with mountain die.
    assert after["improvements_lost"] <= expected_losses


def test_divine_event_logged():
    sim = _sim()
    ty, tx = _owned_unimproved_tile(sim, sim.settlements[0])
    sim.god_terraform(tx, ty, "forest")
    divine = [e for e in sim.event_log if e.type == "divine"]
    assert len(divine) == 1 and "forest" in divine[0].description


def test_determinism_across_identical_sims():
    def run():
        sim = Simulation(World(seed=91, size=64))
        sim.spawn_settlements(count=2)
        for _ in range(20):
            sim.step()
        s = sim.settlements[0]
        sim.god_terraform_region(s.spawn_x, s.spawn_y, 4, "desert")
        return (
            sim.world.terrain.copy(),
            [(x.name, round(x.food_stock, 5)) for x in sim.settlements],
        )

    a = run()
    b = run()
    assert (a[0] == b[0]).all() and a[1] == b[1]


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
