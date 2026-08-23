"""Sprint 31: technology and civilization eras."""

import pytest

from worldsim.actions import Action, NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.buildings import BuildingType
from worldsim.db import _decode_settlement, _encode_settlement
from worldsim.intents import validate_action
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.tech import (
    BUILDING_ERA_REQUIREMENTS,
    ERA3_FARM_OUTPUT_BONUS,
    TECH_RESEARCH_COSTS,
    era_for,
    next_technology,
)
from worldsim.world import World


def _sim(seed=42, n=1) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Frozen RL contract untouched
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60


# ----------------------------------------------------------------------
# Tech/era pure functions
# ----------------------------------------------------------------------

def test_next_technology_fixed_order():
    assert next_technology([]) == "agriculture"
    assert next_technology(["agriculture"]) == "masonry"
    assert next_technology(["agriculture", "masonry", "engineering",
                            "administration"]) is None


def test_era_derivation():
    assert era_for([]) == 1
    assert era_for(["agriculture"]) == 1
    assert era_for(["agriculture", "masonry"]) == 2
    # Era III needs BOTH engineering and administration.
    assert era_for(["agriculture", "masonry", "engineering"]) == 2
    assert era_for(["agriculture", "masonry", "engineering",
                    "administration"]) == 3


# ----------------------------------------------------------------------
# Research accumulation + discovery events
# ----------------------------------------------------------------------

def test_research_accumulates_and_discovers():
    sim = _sim()
    s = sim.settlements[0]
    s.research_points = TECH_RESEARCH_COSTS["agriculture"] - 0.01
    for _ in range(5):
        sim.step()
    assert "agriculture" in s.technologies
    types = [e.type for e in sim.event_log]
    assert "technology" in types


def test_tech_costs_paid_not_duplicated():
    sim = _sim()
    s = sim.settlements[0]
    s.research_points = TECH_RESEARCH_COSTS["masonry"] + 50.0
    s.technologies.append("agriculture")
    for _ in range(2):
        sim.step()
    assert s.technologies.count("masonry") == 1
    assert s.research_points < TECH_RESEARCH_COSTS["engineering"]


def test_era_advancement_logs_event():
    sim = _sim()
    s = sim.settlements[0]
    s.technologies.extend(["agriculture"])
    s.research_points = TECH_RESEARCH_COSTS["masonry"] - 0.01
    for _ in range(5):
        sim.step()
    assert s.era >= 2
    eras = [e for e in sim.event_log if e.type == "era"]
    assert len(eras) == 1 and "Era 2" in eras[0].description


# ----------------------------------------------------------------------
# Era gates on construction (both legality layers)
# ----------------------------------------------------------------------

def test_build_at_blocks_mine_before_era_two():
    from worldsim.tiles import TerrainType

    sim = _sim()
    s = sim.settlements[0]
    for _ in range(30):
        sim.step()
    s.research_points = 0
    # Deterministic setup: force an owned, unimproved tile to be mountain.
    # territory_of yields (y, x) pairs (np.argwhere rows).
    ty, tx = next(t for t in sim.territory_of(s)
                  if sim.world.improvements[t[0], t[1]] == -1)
    sim.world.terrain[ty, tx] = TerrainType.MOUNTAIN.value
    sim._invalidate_cache()
    my, mx = sim.find_building_site(s, BuildingType.MINE)
    assert (my, mx) == (ty, tx)
    assert sim.build_at(s, BuildingType.MINE, x=mx, y=my) is False
    s.technologies.extend(["agriculture", "masonry"])
    s.resource_inventory.update({"wood": 100.0, "stone": 100.0})
    assert sim.build_at(s, BuildingType.MINE, x=mx, y=my) is True


def test_intent_validator_blocks_granary_with_reason():
    from worldsim.tiles import TerrainType

    sim = _sim(n=2)
    s = sim.settlements[0]
    for _ in range(30):
        sim.step()
    s.research_points = 0
    # Deterministic setup: force an owned unimproved tile to plains.
    # territory_of yields (y, x) pairs (np.argwhere rows).
    ty, tx = next(t for t in sim.territory_of(s)
                  if sim.world.improvements[t[0], t[1]] == -1)
    sim.world.terrain[ty, tx] = TerrainType.PLAINS.value
    sim._invalidate_cache()
    gy, gx = sim.find_building_site(s, BuildingType.GRANARY)
    assert (gy, gx) == (ty, tx)
    s.resource_inventory.update({"wood": 100.0, "stone": 100.0})
    ok, reason = validate_action(sim, s, Action.BUILD_GRANARY)
    assert not ok and reason == "missing_technology_granary"
    s.technologies.extend(["agriculture", "masonry"])
    ok, reason = validate_action(sim, s, Action.BUILD_GRANARY)
    assert ok or reason.startswith('no_site'), reason


def test_farm_unaffected_by_era_gate():
    sim = _sim()
    s = sim.settlements[0]
    for _ in range(30):
        sim.step()
    s.research_points = 0
    site = sim.find_building_site(s, BuildingType.FARM)
    assert site is not None, "seed 42 has a farmable tile after claiming"
    sy, sx = site
    s.resource_inventory.update({"wood": 100.0, "stone": 100.0})
    assert sim.build_at(s, BuildingType.FARM, x=sx, y=sy) is True


# ----------------------------------------------------------------------
# Era III bonuses
# ----------------------------------------------------------------------

def test_era3_farm_output_bonus():
    sim = _sim()
    s = sim.settlements[0]
    base_income = sim.food_income(s)
    s.technologies.extend(
        ["agriculture", "masonry", "engineering", "administration"])
    boosted = sim.food_income(s)
    farms = int((sim.world.improvements[
        sim.world.ownership == sim.settlements.index(s)]
        == 1).sum())
    if farms > 0:
        expected_extra = (
            farms * 2 * ERA3_FARM_OUTPUT_BONUS)  # farm food_output == 2
        assert boosted - base_income == pytest.approx(expected_extra)


def test_era3_route_transfer_bonus():
    sim = _sim(n=2)
    a, b = sim.settlements[0], sim.settlements[1]
    route = sim.establish_route(a, b)
    assert route is not None
    a.resource_inventory.update({"wood": 500.0, "stone": 500.0,
                                 "metal": 500.0})
    b.resource_inventory.update({"wood": 0.0, "stone": 0.0, "metal": 0.0})
    before_b = b.resource_inventory["wood"]
    sim._trade_tick(route)
    normal_gain = b.resource_inventory["wood"] - before_b

    b.resource_inventory.update({"wood": 0.0})
    a.resource_inventory.update({"wood": 500.0})
    a.technologies.extend(
        ["agriculture", "masonry", "engineering", "administration"])
    before_b2 = b.resource_inventory["wood"]
    sim._trade_tick(route)
    era3_gain = b.resource_inventory["wood"] - before_b2
    assert era3_gain > normal_gain


# ----------------------------------------------------------------------
# Persistence round-trip
# ----------------------------------------------------------------------

def test_research_state_round_trips_through_db_encoding():
    s = Settlement(name="Alpha", spawn_x=1, spawn_y=1)
    s.research_points = 123.5
    s.technologies.extend(["agriculture", "masonry"])
    restored = _decode_settlement(_encode_settlement(s))
    assert restored.research_points == pytest.approx(123.5)
    assert restored.technologies == ["agriculture", "masonry"]
    assert restored.era == 2


# ----------------------------------------------------------------------
# Determinism across identical worlds
# ----------------------------------------------------------------------

def test_tech_timeline_byte_identical_across_sims():
    def run():
        sim = Simulation(World(seed=77, size=64))
        sim.spawn_settlements(count=2)
        for _ in range(400):
            sim.step()
        return [(s.name, s.technologies, round(s.research_points, 6))
                for s in sim.settlements]

    assert run() == run()
