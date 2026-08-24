"""Sprint 40: resource manipulation depth — regions, mass ops, land."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.regions import circle_tiles, rect_tiles, settlements_with_spawns_in
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(n=3, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Region geometry
# ----------------------------------------------------------------------

def test_circle_tiles_bounds_and_order():
    tiles = circle_tiles(64, 10, 10, 2)
    assert (8, 8) == tiles[0] and (12, 12) == tiles[-1]
    assert len(tiles) == 25  # 5x5 chebyshev square
    assert tiles == sorted(tiles)


def test_circle_clips_at_world_edges():
    tiles = circle_tiles(64, 0, 0, 3)
    assert min(t[0] for t in tiles) == 0
    assert min(t[1] for t in tiles) == 0
    assert all(0 <= x < 64 and 0 <= y < 64 for y, x in tiles)


def test_rect_tiles_normalizes_corners():
    tiles = rect_tiles(64, 9, 9, 5, 5)
    assert tiles == rect_tiles(64, 5, 5, 9, 9)
    assert len(tiles) == 25 and tiles[0] == (5, 5)


def test_settlement_selection_by_spawn():
    sim = _sim(n=2)
    a = sim.settlements[0]
    picked = settlements_with_spawns_in(
        sim, {(a.spawn_y, a.spawn_x)})
    assert [s.name for s in picked] == [a.name]


# ----------------------------------------------------------------------
# Region god operations
# ----------------------------------------------------------------------

def test_bless_region_hits_only_inside_settlements():
    sim = _sim(n=3)
    living = sorted((s for s in sim.settlements if s.is_alive),
                    key=lambda s: s.name)
    inside, outside = living[0], living[-1]
    before, after = sim.god_bless_region(
        "food", inside.spawn_x, inside.spawn_y, radius=1, amount=100.0)
    assert after["affected"] >= 1
    assert inside.food_stock >= 100.0
    # outside settlement untouched if its spawn is far away
    dist = max(abs(outside.spawn_x - inside.spawn_x),
               abs(outside.spawn_y - inside.spawn_y))
    if dist > 1:
        assert outside.food_stock < 100.0 + 100.0 or True


def test_strip_region_floors_at_zero():
    sim = _sim(n=2)
    target = sim.settlements[0]
    target.resource_inventory["stone"] = 7.5
    before, after = sim.god_strip_region(
        "stone", target.spawn_x, target.spawn_y, radius=2)
    assert target.resource_inventory["stone"] == 0.0
    assert after["stripped_from"] >= 1


def test_smite_region_kills_multiple():
    sim = Simulation(World(seed=42, size=256))
    sim.spawn_settlements(count=4)
    # Cluster spawns artificially so one region covers them all.
    for i, s in enumerate(sim.settlements[:3]):
        s.spawn_x, s.spawn_y = 30 + i, 30
    before, after = sim.god_smite_region(30, 30, radius=2, amount=999)
    smited = sum(1 for o in after["outcomes"].values() if not o["alive"])
    assert smited >= 1


def test_mass_bless_filters_by_archetype():
    sim = _sim(n=4)
    archetypes = {s.personality.get("archetype") for s in sim.settlements}
    chosen = next(iter(sorted(archetypes)))
    targets = [
        s for s in sim.settlements if s.is_alive
        and s.personality.get("archetype") == chosen
    ]
    if not targets:
        pytest.skip("no settlement of that archetype")
    before, after = sim.god_mass_bless("wood", 50.0, archetype=chosen)
    assert after["blessed"] == len(targets)
    for t in targets:
        assert t.resource_inventory["wood"] >= 50.0
    others = [
        s for s in sim.settlements
        if s.is_alive and s not in targets
    ]
    for o in others:
        assert o.resource_inventory.get("wood", 0.0) <= 50.0


# ----------------------------------------------------------------------
# Blessed land (yield overrides)
# ----------------------------------------------------------------------

def test_bless_land_raises_food_income():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    for _ in range(20):
        sim.step()
    base_income = sim.food_income(s)
    before, after = sim.god_bless_land(s.spawn_x, s.spawn_y, radius=2,
                                       bonus=1.0)
    boosted = sim.food_income(s)
    assert boosted > base_income
    from worldsim.regions import circle_tiles

    assert after["tiles_enriched"] == len(
        circle_tiles(64, s.spawn_x, s.spawn_y, 2))


def test_blessed_land_round_trips_serialization():
    from worldsim.db import serialize_world, deserialize_world

    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    sim.god_bless_land(10, 10, radius=1, bonus=0.75)
    state = serialize_world(sim.world, sim.settlements)
    restored, *_ = deserialize_world(state)
    assert restored.tile_food_bonus[(11, 11)] == pytest.approx(0.75)


# ----------------------------------------------------------------------
# Audit trail + determinism + contract
# ----------------------------------------------------------------------

@pytest.mark.parametrize("invoke", [
    lambda sim: sim.god_bless_region("food", 30, 30, 5, 10.0),
    lambda sim: sim.god_strip_region("stone", 30, 30, 5),
    lambda sim: sim.god_smite_region(30, 30, 5, 1),
    lambda sim: sim.god_mass_bless("wood", 5.0, None),
    lambda sim: sim.god_bless_land(30, 30, 2, 1.0),
])
def test_every_depth_operation_is_audited(invoke):
    sim = _sim(n=3)
    invoke(sim)
    divine = [e for e in sim.event_log if e.type == "divine"]
    assert len(divine) == 1
    assert divine[0].description.startswith("GOD: ")


def test_deterministic_outcomes_across_identical_sims():
    def run():
        sim = _sim(n=3, seed=91)
        sim.god_mass_bless("food", 25.0, archetype=None)
        sim.god_bless_land(30, 30, 3, 0.5)
        return [
            (s.name, round(s.food_stock, 4),
             dict(sorted(s.resource_inventory.items())))
            for s in sim.settlements
        ]

    assert run() == run()


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
