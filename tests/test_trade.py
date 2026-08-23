import numpy as np
import pytest

from worldsim.buildings import Improvement
from worldsim.simulation import (
    COLLAPSE_INTERVAL_TICKS,
    SPAWN_MIN_DISTANCE,
    TRADE_AMOUNT_PER_TICK,
    Simulation,
    TradeRoute,
)
from worldsim.tiles import TerrainType
from worldsim.world import World


def make_sim(seed: int = 42, count: int = 3) -> tuple[Simulation, list]:
    sim = Simulation(World(seed=seed))
    settlements = sim.spawn_settlements(count=count)
    return sim, settlements


# ----------------------------------------------------------------------
# Multi-spawn
# ----------------------------------------------------------------------

def test_multi_spawn_creates_n_settlements():
    _, settlements = make_sim(seed=42, count=3)
    assert len(settlements) == 3


def test_spawns_respect_min_distance():
    _, settlements = make_sim(seed=12345, count=3)
    for i, a in enumerate(settlements):
        for b in settlements[i + 1 :]:
            dist = max(abs(a.spawn_y - b.spawn_y), abs(a.spawn_x - b.spawn_x))
            assert dist >= SPAWN_MIN_DISTANCE


def test_spawn_locations_deterministic():
    _, a = make_sim(seed=7, count=3)
    _, b = make_sim(seed=7, count=3)
    assert [(s.spawn_x, s.spawn_y) for s in a] == [(s.spawn_x, s.spawn_y) for s in b]


def test_settlements_have_unique_names_and_ids():
    _, settlements = make_sim(seed=5, count=3)
    names = [s.name for s in settlements]
    ids = [s.id for s in settlements]
    assert len(set(names)) == len(names)
    assert len(set(ids)) == len(ids)


def test_no_territory_overlap_at_spawn():
    sim, settlements = make_sim(seed=99, count=3)
    for s in settlements:
        territory = sim.territory_of(s)
        assert len(territory) == 9  # each claimed its full 3x3
    total_owned = int((sim.world.ownership != -1).sum())
    assert total_owned == 27


@pytest.mark.slow
def test_all_settlements_grow_independently():
    sim, settlements = make_sim(seed=11, count=3)
    start_pops = [s.population for s in settlements]
    for _ in range(240):
        sim.step()
    alive = [s for s in settlements if s.is_alive]
    assert len(alive) >= 2  # most survive on decent spawns
    for s in alive:
        assert s.population > start_pops[sim.settlements.index(s)] - 10


@pytest.mark.slow
def test_multi_settlement_simulation_deterministic():
    def run(seed):
        sim, settlements = make_sim(seed=seed, count=3)
        history = []
        for _ in range(150):
            sim.step()
            history.append(
                tuple(
                    (s.population, round(s.food_stock, 6)) for s in settlements
                )
                + (len(sim.active_routes()),)
            )
        return history

    assert run(31415) == run(31415)


# ----------------------------------------------------------------------
# Trade mechanics
# ----------------------------------------------------------------------

def force_adjacent(sim: Simulation, a, b) -> None:
    """Test helper: claim tiles so territories touch."""
    idx_a = sim.settlements.index(a)
    target = (a.spawn_y, a.spawn_x + 2)
    if sim.world.ownership[target] == -1:
        sim.world.ownership[target] = idx_a
    idx_b = sim.settlements.index(b)
    neighbor = (a.spawn_y, a.spawn_x + 3)
    if sim.world.ownership[neighbor] == -1:
        sim.world.ownership[neighbor] = idx_b


def test_route_requires_adjacency():
    sim, (a, b, _) = make_sim(seed=42, count=3)
    # Spawns are >= 32 apart; territories cannot be adjacent yet.
    assert not sim.can_establish_route(a, b)
    assert sim.establish_route(a, b) is None


def test_establish_route_between_adjacent():
    sim, (a, b, _) = make_sim(seed=42, count=3)
    force_adjacent(sim, a, b)
    route = sim.establish_route(a, b)
    assert route is not None
    assert route.active
    # Duplicate routes between the same pair are rejected.
    assert sim.establish_route(a, b) is None
    assert sim.establish_route(b, a) is None


def test_trade_transfers_gap_scaled_units():
    """Sprint 32: shipment size scales with the valuation gap."""
    from worldsim.markets import BASE_TRADE_UNITS, MAX_TRADE_UNITS

    sim, (a, b, _) = make_sim(seed=42, count=3)
    force_adjacent(sim, a, b)
    a.resource_inventory["wood"] = 100.0
    b.resource_inventory["wood"] = 0.0
    route = sim.establish_route(a, b)
    before_a = a.resource_inventory["wood"]
    before_b = b.resource_inventory["wood"]
    sim._trade_tick(route)
    moved = before_a - a.resource_inventory["wood"]
    assert BASE_TRADE_UNITS <= moved <= MAX_TRADE_UNITS
    assert b.resource_inventory["wood"] == pytest.approx(before_b + moved)
    assert route.transfers == 1


def test_trade_direction_follows_surplus():
    sim, (a, b, _) = make_sim(seed=42, count=3)
    force_adjacent(sim, a, b)
    a.resource_inventory["stone"] = 50.0
    b.resource_inventory["stone"] = 0.0
    route = sim.establish_route(a, b)
    sim._trade_tick(route)
    assert b.resource_inventory["stone"] >= 1.0
    # Flip the imbalance: now B donates back.
    moved_ab = b.resource_inventory["stone"]
    b.resource_inventory["stone"] = 90.0 + moved_ab
    a.resource_inventory["stone"] = 0.0
    sim._trade_tick(route)
    assert a.resource_inventory["stone"] >= 1.0
    assert b.resource_inventory["stone"] < 90.0 + moved_ab


def test_food_is_tradable():
    from worldsim.markets import BASE_TRADE_UNITS

    sim, (a, b, _) = make_sim(seed=42, count=3)
    force_adjacent(sim, a, b)
    a.food_stock = 1000.0
    b.food_stock = 0.0
    route = sim.establish_route(a, b)
    sim._trade_tick(route)
    assert b.food_stock > BASE_TRADE_UNITS


def test_route_deactivates_when_partner_dies():
    sim, (a, b, _) = make_sim(seed=42, count=3)
    force_adjacent(sim, a, b)
    route = sim.establish_route(a, b)
    b.population = 0
    sim._trade_tick(route)
    assert not route.active


def test_auto_trade_connects_adjacent_pairs_only():
    """Sprint 11: trade routes form between known neighbors (proximity OR
    territory contact) — with all settlements within range, all pairs link."""
    sim, settlements = make_sim(seed=12345, count=3)
    sim._auto_trade_rule()
    active = sim.active_routes()
    # C(3,2) = 3 unique pairs.
    assert len(active) == 3


# ----------------------------------------------------------------------
# Scarcity & economic collapse
# ----------------------------------------------------------------------

def test_scarcity_detected_on_negative_inventory():
    sim, (a,) = make_sim(seed=42, count=1)
    assert not a.is_in_scarcity
    a.resource_inventory["wood"] = -1.0
    assert a.is_in_scarcity


def test_economic_collapse_loses_population():
    sim, (a,) = make_sim(seed=42, count=1)
    # No territory income: starvation + collapse both drain population.
    sim.release_territory(a)
    a.food_stock = 0
    a.resource_inventory = {"wood": -5.0, "stone": -5.0}
    pop_before = a.population
    for _ in range(COLLAPSE_INTERVAL_TICKS):
        sim.step()
    assert a.population < pop_before
    assert a.negative_inventory_progress == 0


def test_collapse_to_death_releases_territory():
    sim, (a,) = make_sim(seed=42, count=1)
    sim.release_territory(a)  # no income at all
    a.population = 1
    a.food_stock = 0
    a.resource_inventory = {"wood": -100.0, "stone": -100.0}
    for _ in range(COLLAPSE_INTERVAL_TICKS * 3):
        if not a.is_alive:
            break
        sim.step()
    assert not a.is_alive
    assert a.destroyed_at_tick is not None
    assert (sim.world.ownership == -1).all()


def test_recovery_clears_collapse_timer():
    sim, (a,) = make_sim(seed=42, count=1)
    a.resource_inventory["wood"] = -1.0
    for _ in range(20):
        sim.step()
    assert a.negative_inventory_progress > 0
    a.resource_inventory["wood"] = 50.0
    for _ in range(5):
        sim.step()
    assert a.negative_inventory_progress == 0


def test_scarcity_halves_build_rate():
    sim, (a,) = make_sim(seed=42, count=1)
    # Affordable farm resources, but a negative metal stock triggers scarcity
    # without blocking construction affordability.
    a.resource_inventory["wood"] = 30.0
    a.resource_inventory["stone"] = 15.0
    a.resource_inventory["metal"] = -0.5
    sim._auto_build_rule(a)
    assert len(a.build_queue) == 1
    ticks_run = 0
    while a.build_queue and ticks_run < 10:
        sim.step()
        ticks_run += 1
    assert not a.build_queue
    assert sim.tick % 2 == 0  # processed on an even tick
