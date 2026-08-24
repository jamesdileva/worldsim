"""Sprint 42: nuclear events — mass destruction + lasting contamination."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.buildings import Improvement
from worldsim.db import serialize_world, deserialize_world
from worldsim.disasters import (
    CONTAMINATION_HAPPINESS_DECAY,
    CONTAMINATION_TICKS,
    CONTAMINATION_YIELD_FACTOR,
    NUKE_POPULATION_FRACTION,
)
from worldsim.simulation import Simulation
from worldsim.tiles import TerrainType
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    for _ in range(10):
        sim.step()
    return sim


# ----------------------------------------------------------------------
# The strike itself
# ----------------------------------------------------------------------

def test_nuke_annihilates_improvements_in_blast():
    sim = _sim(n=1)
    s = sim.settlements[0]
    tiles = [t for t in sim.territory_of(s)]
    ty, tx = tiles[0]
    sim.world.improvements[ty, tx] = Improvement.FARM.value
    before, after = sim.god_nuke(tx, ty)
    assert after["improvements_destroyed"] >= 1
    assert sim.world.improvements[ty, tx] == -1


def test_nuke_kills_fraction_of_affected_population():
    sim = _sim(n=2)
    a, b = [s for s in sim.settlements if s.is_alive][:2]
    a.population = 100
    a_before_pop = a.population
    before, after = sim.god_nuke(a.spawn_x, a.spawn_y)
    expected_killed = round(100 * NUKE_POPULATION_FRACTION)
    if not a.is_alive:
        # died through the real kill path; migration already handled
        assert after["deaths"][a.name] == expected_killed or (
            100 - expected_killed <= 0
        )
    else:
        assert a.population == a_before_pop - expected_killed


def test_outsiders_unharmed():
    sim = Simulation(World(seed=42, size=256))
    sim.spawn_settlements(count=3)
    living = [s for s in sim.settlements if s.is_alive]
    victim, far = living[0], living[-1]
    victim.population = 50
    far.population = 50
    dist = max(abs(victim.spawn_x - far.spawn_x),
               abs(victim.spawn_y - far.spawn_y))
    if dist <= 12:
        pytest.skip("spawns too close")
    sim.god_nuke(victim.spawn_x, victim.spawn_y)
    assert far.population == 50


def test_death_routes_through_kill_path():
    """A nuked settlement that dies leaves an enriched ruin (S36 rules)."""
    sim = _sim(n=2)
    a, _b = [s for s in sim.settlements if s.is_alive][:2]
    a.population = 1  # any deaths kill it outright
    a.technologies.append("masonry")
    sim.god_nuke(a.spawn_x, a.spawn_y)
    # the essential invariant: it's gone and remembered
    assert not a.is_alive
    assert any(r.settlement_id == a.id for r in sim.ruins)


# ----------------------------------------------------------------------
# Contamination lifecycle
# ----------------------------------------------------------------------

def test_contamination_suppresses_then_restores_yields():
    sim = _sim(n=1)
    s = sim.settlements[0]
    base = sim.food_income(s)
    start_tick = sim.tick
    sim.god_nuke(s.spawn_x, s.spawn_y)
    suppressed = sim.food_income(s)

    # Control: the same world WITHOUT fallout would be much richer.
    zones_backup = list(sim.contamination_zones)
    sim.contamination_zones = []
    sim._invalidate_cache()
    uncontaminated = sim.food_income(s)
    sim.contamination_zones = zones_backup
    sim._invalidate_cache()

    assert suppressed == pytest.approx(
        uncontaminated * CONTAMINATION_YIELD_FACTOR)
    assert suppressed < uncontaminated * 0.5

    # Long after the zone expires: suppression factor gone entirely
    # (terrain yields return at full strength even though buildings died).
    sim.world.tick = start_tick + CONTAMINATION_TICKS + 1
    sim._invalidate_cache()
    assert sim._contaminated_tiles_mask() is None
    restored = sim.food_income(s)
    assert restored == pytest.approx(uncontaminated)


def test_contamination_zone_expires_on_schedule():
    sim = _sim(n=1)
    s = sim.settlements[0]
    start_tick = sim.tick
    sim.god_nuke(s.spawn_x, s.spawn_y)
    assert sim.contamination_zones[0].is_active(sim.tick)
    sim.world.tick = start_tick + CONTAMINATION_TICKS
    assert not sim.contamination_zones[0].is_active(sim.tick)


def test_contaminated_settlement_happiness_bleeds():
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_toggle_freeze(s)  # freeze so nothing else moves happiness
    sim.god_nuke(s.spawn_x, s.spawn_y)
    sim.god_toggle_freeze(s)  # unfreeze: contamination applies again
    before_h = s.happiness
    ticks = 20
    for _ in range(ticks):
        sim.step()
    # Net drop = fallout bleed minus natural recovery (~0.0055/tick).
    assert s.happiness < before_h
    assert before_h - s.happiness >= (
        ticks * (CONTAMINATION_HAPPINESS_DECAY - 0.006) * 0.9)


def test_contamination_visible_in_world_summary():
    from worldsim.summaries import summarize_world

    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_nuke(s.spawn_x, s.spawn_y)
    text = summarize_world(sim, tier="full")
    line = next(l for l in text.splitlines()
                if l.startswith("Contamination:"))
    assert "1 active zone" in line


# ----------------------------------------------------------------------
# Audit + persistence + contract
# ----------------------------------------------------------------------

def test_nuke_is_audited():
    sim = _sim(n=1)
    sim.god_nuke(30, 30)
    divine = [e for e in sim.event_log if e.type == "divine"]
    assert len(divine) == 1
    assert "nuclear" in divine[0].description


def test_zones_round_trip_serialization():
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_nuke(s.spawn_x, s.spawn_y)
    state = serialize_world(
        sim.world, sim.settlements, contamination_zones=sim.contamination_zones
    )
    restored = deserialize_world(state)
    zones = restored[-1]
    assert len(zones) == 1
    assert zones[0].center_x == s.spawn_x
    assert zones[0].center_y == s.spawn_y
    assert zones[0].end_tick == zones[0].start_tick + CONTAMINATION_TICKS


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
