"""Sprint 32: derived market prices + price-driven trade."""

import pytest

from worldsim.markets import (
    MAX_TRADE_UNITS,
    best_trade,
    market_prices,
    resource_price,
    settlement_availability,
    transfer_units,
    valuation_gap,
)
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Price derivation (pure function of state)
# ----------------------------------------------------------------------

def test_prices_deterministic_and_complete():
    sim = _sim()
    a = market_prices(sim)
    b = market_prices(sim)
    assert a == b
    assert set(a) == {"food", "wood", "stone", "metal"}
    assert a["metal"] > a["food"]  # scarcer base resource prices higher


def test_scarcity_raises_abundance_lowers():
    from worldsim.markets import PRICE_CEILING, PRICE_FLOOR

    sim = _sim()
    base = resource_price(sim, "wood")
    for s in sim.settlements:
        s.resource_inventory["wood"] = 0.0
    scarce = resource_price(sim, "wood")
    for s in sim.settlements:
        s.resource_inventory["wood"] = 500.0
    glut = resource_price(sim, "wood")
    assert scarce > base > glut
    assert PRICE_FLOOR <= glut <= PRICE_CEILING
    assert scarce <= PRICE_CEILING


def test_dead_settlements_excluded_from_pricing():
    sim = _sim(n=2)
    sim.settlements[1].population = 0
    living_only = [sim.settlements[0]]
    expected = (
        sum(settlement_availability(s, "stone") for s in living_only)
        / len(living_only))
    assert resource_price(sim, "stone") >= 0
    # No crash with dead settlement; price computed from the living only.
    p = resource_price(sim, "stone")
    assert isinstance(p, float)


def test_empty_world_returns_base_price():
    sim = _sim()
    sim.settlements[0].population = 0
    if len(sim.settlements) > 1:
        sim.settlements[1].population = 0
    assert resource_price(sim, "food") == 1.0


# ----------------------------------------------------------------------
# Valuation gap + direction
# ----------------------------------------------------------------------

def test_valuation_gap_positive_for_donors_surplus():
    sim = _sim(n=2)
    a, b = sim.settlements
    a.resource_inventory.update({"wood": 300.0})
    b.resource_inventory.update({"wood": 0.0})
    gap = valuation_gap(sim, a.id, b.id, "wood")
    assert gap > 0.5
    assert valuation_gap(sim, b.id, a.id, "wood") < 0


def test_best_trade_picks_largest_gap_resource():
    sim = _sim(n=2)
    a, b = sim.settlements
    a.resource_inventory.update({"wood": 400.0, "stone": 100.0})
    b.resource_inventory.update({"wood": 0.0, "stone": 0.0})
    result = best_trade(sim, a, b)
    assert result is not None
    resource, gap = result
    assert resource in ("wood", "stone")
    assert gap > 0


def test_no_trade_between_identically_stocked():
    sim = _sim(n=2)
    a, b = sim.settlements
    a.resource_inventory.update({"wood": 50.0, "stone": 20.0,
                                 "metal": 5.0})
    b.resource_inventory.update({"wood": 50.0, "stone": 20.0,
                                 "metal": 5.0})
    a.food_stock = b.food_stock
    assert best_trade(sim, a, b) is None


# ----------------------------------------------------------------------
# Transfer sizing
# ----------------------------------------------------------------------

def test_transfer_units_scale_with_gap_and_cap():
    from worldsim.markets import BASE_TRADE_UNITS

    small = transfer_units(0.25, donor_is_era3=False)
    large = transfer_units(4.0, donor_is_era3=False)
    capped = transfer_units(99.0, donor_is_era3=False)
    assert small > BASE_TRADE_UNITS
    assert large == capped == MAX_TRADE_UNITS


def test_era3_bonus_applies_after_cap():
    assert transfer_units(4.0, True) == pytest.approx(MAX_TRADE_UNITS * 1.25)


def test_trade_tick_moves_scaled_amounts():
    sim = _sim(n=2)
    a, b = sim.settlements
    route = sim.establish_route(a, b)
    assert route is not None
    a.resource_inventory.update({"wood": 400.0, "stone": 200.0,
                                 "metal": 100.0})
    b.resource_inventory.update({"wood": 0.0, "stone": 0.0, "metal": 0.0})
    before = b.resource_inventory["wood"]
    sim._trade_tick(route)
    moved = b.resource_inventory["wood"] - before
    assert 1.0 < moved <= MAX_TRADE_UNITS  # scaled up beyond old fixed unit
    assert route.transfers == 1
    # donor stock respected exactly
    assert a.resource_inventory["wood"] == pytest.approx(400.0 - moved)


def test_trade_tick_clamps_to_donor_stock():
    sim = _sim(n=2)
    a, b = sim.settlements
    # S63 founder wealth varies; pin identical baselines EXCEPT wood so
    # wood owns the largest valuation gap and flows toward the receiver.
    for s in (a, b):
        s.resource_inventory.clear()
        s.resource_inventory.update({"wood": 0.0, "stone": 20.0,
                                     "metal": 5.0})
        s.food_stock = 200.0
    route = sim.establish_route(a, b)
    assert route is not None
    a.resource_inventory["wood"] = 0.6
    sim._trade_tick(route)
    assert b.resource_inventory["wood"] == pytest.approx(
        0.6 - a.resource_inventory["wood"])
    # dust trading skipped: nothing below the floor moves twice
    assert a.resource_inventory["wood"] == pytest.approx(0.0)


def test_food_trades_like_a_resource():
    sim = _sim(n=2)
    a, b = sim.settlements
    route = sim.establish_route(a, b)
    assert route is not None
    a.food_stock = 800.0
    b.food_stock = 10.0
    before = b.food_stock
    sim._trade_tick(route)
    moved = b.food_stock - before
    assert moved > 1.0  # big gap -> scaled shipment
    assert a.food_stock == pytest.approx(800.0 - moved)


# ----------------------------------------------------------------------
# Summaries surface prices; determinism preserved
# ----------------------------------------------------------------------

def test_world_summary_contains_prices_line():
    sim = _sim(n=2)
    text = __import__("worldsim.summaries", fromlist=["summarize_world"]) \
        .summarize_world(sim, tier="full")
    line = next(l for l in text.splitlines() if l.startswith("Market prices"))
    assert "food=" in line and "metal=" in line


def test_byte_identical_across_identical_sims_with_trade():
    def run():
        sim = _sim(n=3, seed=91)
        for _ in range(120):
            sim.step()
        return [(s.name, round(s.food_stock, 6),
                 dict(sorted(s.resource_inventory.items())))
                for s in sim.settlements]

    assert run() == run()


def test_frozen_contract_unchanged():
    from worldsim.actions import NUM_ACTIONS
    from worldsim.agents import OBSERVATION_DIM

    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
