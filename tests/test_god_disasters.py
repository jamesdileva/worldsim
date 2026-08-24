"""Sprint 39: disaster toolkit — authored disasters."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.disasters import (
    DISASTER_RADIUS,
    DROUGHT_DURATION_TICKS,
    PLAGUE_MORTALITY,
)
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Authored disasters use the same mechanics as random ones
# ----------------------------------------------------------------------

def test_authored_fire_burns_improvements_in_zone():
    sim = _sim(n=1)
    s = sim.settlements[0]
    for _ in range(30):
        sim.step()
    # Force a forest improvement next to the spawn so fire has fuel.
    ty, tx = sim.territory_of(s)[0]
    from worldsim.buildings import Improvement
    from worldsim.tiles import TerrainType

    sim.world.terrain[ty, tx] = TerrainType.FOREST.value
    sim.world.improvements[ty, tx] = Improvement.FARM.value
    sim._invalidate_cache()
    before, after = sim.god_trigger_disaster(
        "fire", s.spawn_x, s.spawn_y, radius=6)
    assert after["tiles_burned"] >= 1
    assert sim.world.improvements[ty, tx] == -1  # burned off


def test_authored_plague_kills_affected_population():
    sim = _sim(n=2)
    a, b = [s for s in sim.settlements if s.is_alive][:2]
    a.population = 100
    b.population = 100
    # Radius small enough to spare b: place disaster right on a's spawn.
    radius = max(
        1,
        min(
            abs(a.spawn_x - b.spawn_x), abs(a.spawn_y - b.spawn_y),
            DISASTER_RADIUS,
        ) // 2 or 1,
    )
    before, after = sim.god_trigger_disaster(
        "plague", a.spawn_x, a.spawn_y, radius=radius)
    assert a.population < 100
    assert b.population == 100  # outside the zone
    assert after["deaths"] == 100 - a.population


def test_authored_drought_is_ongoing_and_expires():
    sim = _sim(n=1)
    s = sim.settlements[0]
    start_tick = sim.tick
    sim.god_trigger_disaster("drought", s.spawn_x, s.spawn_y, duration=100)
    assert len(sim.active_disasters()) == 1
    mult_during = sim._drought_multiplier(s)
    assert mult_during < 1.0  # farms suppressed
    sim.world.tick = start_tick + 101
    assert sim._drought_multiplier(s) == pytest.approx(1.0)


def test_drought_duration_defaults_to_standard():
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_trigger_disaster("drought", s.spawn_x, s.spawn_y)
    (event,) = sim.disaster_events
    assert event.duration == DROUGHT_DURATION_TICKS


def test_zone_preview_lists_affected_settlements():
    sim = _sim(n=2)
    a, b = [s for s in sim.settlements if s.is_alive][:2]
    before, _after = sim.god_trigger_disaster(
        "drought", a.spawn_x, a.spawn_y,
        radius=max(1, abs(a.spawn_x - b.spawn_x) // 2),
    )
    assert a.name in before["affected_settlements"]
    assert before["affected_settlements"] == sorted(
        before["affected_settlements"])


def test_unknown_type_rejected():
    sim = _sim()
    with pytest.raises(ValueError):
        sim.god_trigger_disaster("meteor", 10, 10)


# ----------------------------------------------------------------------
# Audit trail + determinism
# ----------------------------------------------------------------------

def test_divine_event_logged_for_authored_disasters():
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_trigger_disaster("fire", s.spawn_x, s.spawn_y, radius=4)
    divine = [e for e in sim.event_log if e.type == "divine"]
    assert len(divine) == 1
    assert "fire" in divine[0].description


def test_identical_authors_produce_identical_states():
    def run():
        sim = _sim(n=2, seed=77)
        for s in sim.settlements:
            s.army = 3.0
        sim.god_trigger_disaster(
            "fire", sim.settlements[0].spawn_x,
            sim.settlements[0].spawn_y, radius=8)
        sim.god_trigger_disaster(
            "plague", sim.settlements[1].spawn_x,
            sim.settlements[1].spawn_y, radius=8)
        return [
            (s.name, s.population,
             dict(sorted(s.resource_inventory.items())))
            for s in sim.settlements
        ]

    assert run() == run()


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
