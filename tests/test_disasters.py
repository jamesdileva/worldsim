import random

import numpy as np
import pytest

from worldsim.buildings import BUILDING_SPECS, BuildingType, Improvement
from worldsim.disasters import (
    DISASTER_RADIUS,
    DROUGHT_DURATION_TICKS,
    DisasterEvent,
    DisasterType,
    roll_event,
    season_of,
)
from worldsim.settlement import (
    HAPPINESS_DECAY_AFTER_TICKS,
    LOW_HAPPINESS_COLLAPSE_TICKS,
    LOW_HAPPINESS_THRESHOLD,
    Settlement,
)
from worldsim.simulation import (
    RUIN_GROWTH_MULTIPLIER,
    RUIN_RESETTLE_CHANCE,
    RUIN_RESETTLE_MIN_AGE,
    Simulation,
)
from worldsim.tiles import TerrainType
from worldsim.world import World


def make_sim(seed: int = 42, count: int = 1) -> tuple[Simulation, list]:
    sim = Simulation(World(seed=seed))
    settlements = sim.spawn_settlements(count=count)
    return sim, settlements


# ----------------------------------------------------------------------
# Seasons & event stream
# ----------------------------------------------------------------------

def test_season_cycles():
    assert season_of(0) == "spring"
    assert season_of(127) == "spring"
    assert season_of(128) == "summer"
    assert season_of(512) == "spring"


def test_event_stream_deterministic():
    a = [roll_event(12345, t, 256) for t in range(0, 2000, 50)]
    b = [roll_event(12345, t, 256) for t in range(0, 2000, 50)]
    assert a == b


def test_event_stream_varies_by_seed():
    a = [roll_event(1, t, 256) for t in range(0, 5000, 50)]
    b = [roll_event(2, t, 256) for t in range(0, 5000, 50)]
    assert a != b


def test_rolled_events_have_valid_fields():
    for seed in range(50):
        for tick in range(0, 10000, 50):
            event = roll_event(seed, tick, 256)
            if event is not None:
                assert event.type in DisasterType
                assert DISASTER_RADIUS <= event.center_x < 256 - DISASTER_RADIUS
                assert event.start_tick == tick
                if event.type == DisasterType.DROUGHT:
                    assert event.duration == DROUGHT_DURATION_TICKS
                else:
                    assert event.duration == 1


# ----------------------------------------------------------------------
# Drought / Fire / Plague effects
# ----------------------------------------------------------------------

def test_drought_halves_farm_yield():
    sim, (s,) = make_sim(seed=42)
    site = next(
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    s.resource_inventory["wood"] = 30
    s.resource_inventory["stone"] = 15
    sim.build_at(s, BuildingType.FARM, x=site[1], y=site[0])
    normal = sim.food_income(s)

    drought = DisasterEvent(
        type=DisasterType.DROUGHT,
        center_x=s.spawn_x,
        center_y=s.spawn_y,
        start_tick=sim.tick,
        duration=DROUGHT_DURATION_TICKS,
    )
    sim.disaster_events.append(drought)
    during = sim.food_income(s) * sim._drought_multiplier(s)
    assert during == pytest.approx(normal * 0.5)

    # After the drought expires, yields recover.
    sim.world.tick = drought.end_tick + 1
    after = sim.food_income(s) * sim._drought_multiplier(s)
    assert after == pytest.approx(normal)


def test_fire_destroys_sawmill_on_forest_tile():
    sim, (s,) = make_sim(seed=42)
    forest_site = next(
        ((y, x) for y, x in sim.territory_of(s)
         if sim.world.terrain[y, x] == TerrainType.FOREST.value),
        None,
    )
    if forest_site is None:
        # Force a forest tile into existence is impossible; skip honestly.
        pytest.skip("no forest in starting territory")
    s.resource_inventory["wood"] = 30
    s.resource_inventory["stone"] = 15
    assert sim.build_at(s, BuildingType.SAWMILL, x=forest_site[1], y=forest_site[0])

    fire = DisasterEvent(
        type=DisasterType.FIRE,
        center_x=forest_site[1],
        center_y=forest_site[0],
        radius=0,
        start_tick=sim.tick,
    )
    burned = sim._apply_fire(fire)
    assert burned >= 1
    assert sim.world.improvements[forest_site] == Improvement.NONE.value


def test_plague_kills_thirty_percent():
    sim, (s,) = make_sim(seed=42)
    s.population = 100
    plague = DisasterEvent(
        type=DisasterType.PLAGUE,
        center_x=s.spawn_x,
        center_y=s.spawn_y,
        start_tick=sim.tick,
    )
    sim._apply_plague(plague)
    assert s.population == 70


def test_plague_only_affects_nearby_settlements():
    sim, settlements = make_sim(seed=12345, count=3)
    target = settlements[0]
    target.population = 100
    far = max(
        (s for s in settlements[1:]),
        key=lambda s: max(
            abs(s.spawn_x - target.spawn_x), abs(s.spawn_y - target.spawn_y)
        ),
    )
    far.population = 100
    plague = DisasterEvent(
        type=DisasterType.PLAGUE,
        center_x=target.spawn_x,
        center_y=target.spawn_y,
        radius=DISASTER_RADIUS,
        start_tick=sim.tick,
    )
    sim._apply_plague(plague)
    assert target.population == 70
    assert far.population == 100  # beyond reach margin


# ----------------------------------------------------------------------
# Happiness & collapse
# ----------------------------------------------------------------------

def test_happiness_decays_after_negative_food_streak():
    sim, (s,) = make_sim(seed=42)
    s.net_food_rate = -5.0
    start = s.happiness
    for _ in range(HAPPINESS_DECAY_AFTER_TICKS + 20):
        s.step_happiness(building_count=0)
    assert s.happiness < start


def test_happiness_recovers_with_positive_food():
    sim, (s,) = make_sim(seed=42)
    s.happiness = 0.2
    s.net_food_rate = 5.0
    for _ in range(50):
        s.step_happiness(building_count=0)
    assert s.happiness > 0.2


def test_collapse_via_low_happiness():
    sim, (s,) = make_sim(seed=42)
    sim.release_territory(s)
    s.food_stock = 0
    s.happiness = LOW_HAPPINESS_THRESHOLD - 0.01
    s.resource_inventory = {"wood": 0.0, "stone": 0.0}
    ticks = 0
    while s.is_alive and ticks < 300:
        sim.step()
        ticks += 1
    assert not s.is_alive
    assert any(r.settlement_id == s.id for r in sim.ruins)


# ----------------------------------------------------------------------
# Ruins & re-settlement
# ----------------------------------------------------------------------

def test_death_records_ruin():
    sim, (s,) = make_sim(seed=42)
    name = s.name
    spawn = (s.spawn_x, s.spawn_y)
    s.population = 0
    sim._kill(s)
    assert len(sim.ruins) == 1
    ruin = sim.ruins[0]
    assert ruin.settlement_id == s.id
    assert (ruin.spawn_x, ruin.spawn_y) == spawn
    assert "Ruins of" in ruin.name


def test_resettle_requires_min_age():
    sim, (s,) = make_sim(seed=42)
    s.population = 0
    ruin = sim._kill(s)
    assert sim._try_resettle_ruin(ruin) is None  # age 0 < 500


def test_resettle_spawns_near_ruin_when_lucky():
    sim, (s,) = make_sim(seed=42)
    s.population = 0
    ruin = sim._kill(s)
    # Force the age window and rig the RNG roll to succeed.
    sim.world.tick = ruin.collapse_tick + RUIN_RESETTLE_MIN_AGE
    real_random = random.Random.random

    class Rigged(random.Random):
        def random(self):
            return 0.0  # always below the 10% chance

    import worldsim.simulation as sim_mod

    saved = sim_mod.random.Random
    sim_mod.random.Random = Rigged
    try:
        new = sim._try_resettle_ruin(ruin)
    finally:
        sim_mod.random.Random = saved
    assert new is not None
    dist = max(abs(new.spawn_x - ruin.spawn_x), abs(new.spawn_y - ruin.spawn_y))
    assert dist <= 3  # founded near the old capital
    assert new.ruin_origin == ruin.id


def test_ruin_origin_growth_bonus():
    sim, (s,) = make_sim(seed=42)
    s.population = 0
    ruin = sim._kill(s)
    location = sim._find_free_tile_near(ruin.spawn_x, ruin.spawn_y)
    assert location is not None
    row, col = location
    founder = Settlement(
        name="Newstart", spawn_x=col, spawn_y=row, ruin_origin=ruin.id
    )
    sim.settlements.append(founder)
    idx = len(sim.settlements) - 1
    sim._claim_tiles(founder, idx, initial=True)
    assert sim._ruin_adjacent(founder)

    # A settlement without ruin origin is never "ruin adjacent".
    outsider = Settlement(name="Outsider", spawn_x=0, spawn_y=0)
    sim.settlements.append(outsider)
    sim._claim_tiles(outsider, len(sim.settlements) - 1, initial=True)
    assert not sim._ruin_adjacent(outsider)


def test_growth_multiplier_applied():
    from worldsim.settlement import GROWTH_INTERVAL_TICKS

    s = make_settlement()
    s.food_stock = 10_000
    for _ in range(GROWTH_INTERVAL_TICKS):
        s.consume_food(income=1000.0)
        s.step_population(growth_multiplier=RUIN_GROWTH_MULTIPLIER)
    assert s.population == 12  # 24 ticks x2 progress = two growth events


def make_settlement():
    from worldsim.settlement import Settlement

    return Settlement(name="Testa", spawn_x=10, spawn_y=10)


# ----------------------------------------------------------------------
# Integration
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_simulation_with_disasters_deterministic():
    def run(seed):
        sim, settlements = make_sim(seed=seed, count=3)
        history = []
        for _ in range(400):
            sim.step()
            history.append(
                tuple((s.population, round(s.happiness, 6)) for s in settlements)
                + (len(sim.disaster_events), len(sim.ruins))
            )
        return history

    assert run(271828) == run(271828)


@pytest.mark.slow
def test_disasters_occur_naturally_over_long_run():
    sim, _ = make_sim(seed=999, count=2)
    for _ in range(3000):
        sim.step()
    # With ~10% per 50-tick check, 60 checks -> expect several events.
    assert len(sim.disaster_events) >= 1
