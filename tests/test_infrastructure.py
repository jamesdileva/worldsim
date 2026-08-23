"""Sprint 33: inter-settlement highway projects."""

import pytest

from worldsim.buildings import Improvement
from worldsim.infrastructure import (
    HIGHWAY_STONE_PER_SEGMENT,
    HIGHWAY_TRADE_BONUS,
    HighwayProject,
    advance_projects,
    can_start_highway,
    highway_connected,
    highway_trade_multiplier,
    maybe_start_highways,
    plan_path,
    start_highway,
)
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.tiles import TerrainType
from worldsim.world import World


def _adjacent_pair(seed=42, size=64):
    """Two settlements whose territories actually touch."""
    sim = Simulation(World(seed=seed, size=size))
    settlements = sim.spawn_settlements(count=2)
    a, b = settlements
    # Force adjacency: claim tiles of B next to A's spawn ring.
    ax, ay = a.spawn_x, a.spawn_y
    for dx in (1, 2, 3):
        for dy in (-1, 0, 1):
            nx, ny = ax + dx, ay + dy
            if sim.world.ownership[ny, nx] == -1:
                sim.world.ownership[ny, nx] = 1
    sim._invalidate_cache()
    return sim, a, b


# ----------------------------------------------------------------------
# Legality
# ----------------------------------------------------------------------

def test_era_gate_blocks_highway_before_era_two():
    sim, a, b = _adjacent_pair()
    ok, reason = can_start_highway(sim, a, b)
    assert not ok and reason == "Brazemi_era_too_low" or reason.endswith(
        "_era_too_low")


def test_highway_starts_at_era_two():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    project = start_highway(sim, a, b, tick=sim.tick)
    assert project is not None
    assert len(sim.highway_projects) == 1


def test_duplicate_highway_rejected():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    assert start_highway(sim, a, b, tick=sim.tick) is not None
    assert start_highway(sim, b, a, tick=sim.tick) is None


def test_non_adjacent_territories_rejected():
    sim = Simulation(World(seed=7, size=256))
    sim.spawn_settlements(count=2)  # far apart on a big map
    a, b = sim.settlements
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    ok, reason = can_start_highway(sim, a, b)
    assert not ok and reason == "territories_not_adjacent"


# ----------------------------------------------------------------------
# Path planning + construction
# ----------------------------------------------------------------------

def test_plan_path_skips_water_and_existing_roads():
    sim, a, b = _adjacent_pair()
    ty, tx = plan_path(sim, a, b)[0]
    assert sim.world.improvements[ty, tx] == Improvement.NONE.value
    assert sim.world.terrain[ty, tx] != TerrainType.WATER.value


def test_construction_consumes_stone_and_lays_roads_progressively():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    a.resource_inventory["stone"] = 100.0
    project = start_highway(sim, a, b, tick=sim.tick)
    total = project.segments_total
    # Fully fund the whole path so construction never pauses.
    a.resource_inventory["stone"] = HIGHWAY_STONE_PER_SEGMENT * total
    laid_mid = 0
    while not project.done and project.segments_done < total:
        before = project.segments_done
        advance_projects(sim)
        # Era II sponsor lays exactly one segment per tick.
        assert project.segments_done == before + 1
        if laid_mid > 500:
            pytest.fail("project never finished")
        laid_mid += 1
    assert project.completed
    assert a.resource_inventory["stone"] == pytest.approx(0.0)
    for ty, tx in project.path:
        assert sim.world.improvements[ty, tx] == Improvement.ROAD.value


def test_project_pauses_without_stone_never_cancels():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    a.resource_inventory["stone"] = 3.0  # exactly one segment
    project = start_highway(sim, a, b, tick=sim.tick)
    advance_projects(sim)
    done_after_first = project.segments_done
    for _ in range(20):
        advance_projects(sim)
    assert project.segments_done == done_after_first
    assert not project.completed
    # Refund: work resumes.
    a.resource_inventory["stone"] = 1000.0
    advance_projects(sim)
    assert project.segments_done > done_after_first


def test_completion_logs_event_and_connects():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    a.resource_inventory["stone"] = 10000.0
    project = start_highway(sim, a, b, tick=sim.tick)
    while not project.done:
        advance_projects(sim)
    assert highway_connected(sim, a.id, b.id)
    types = [e.type for e in sim.event_log]
    assert "infrastructure" in types


def test_era3_sponsor_lays_two_segments_per_tick():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    a.technologies.extend(["engineering", "administration"])
    a.resource_inventory["stone"] = 10000.0
    project = start_highway(sim, a, b, tick=sim.tick)
    if project.segments_total < 2:
        pytest.skip("path too short")
    advance_projects(sim)
    assert project.segments_done == 2


# ----------------------------------------------------------------------
# Trade effect
# ----------------------------------------------------------------------

def test_highway_boosts_route_shipments():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    a, b = sim.settlements
    route = sim.establish_route(a, b)
    assert route is not None
    a.resource_inventory.update({"wood": 400.0})
    b.resource_inventory.update({"wood": 0.0})
    sim._trade_tick(route)
    normal = b.resource_inventory["wood"]

    # Fake a completed highway between the endpoints.
    sim.highway_projects.append(HighwayProject(
        a_id=a.id, b_id=b.id, sponsor_id=a.id,
        path=[(0, 1)], segments_done=1, completed=True))
    assert highway_trade_multiplier(sim, a.id, b.id) == pytest.approx(
        1.0 + HIGHWAY_TRADE_BONUS)

    b.resource_inventory.update({"wood": 0.0})
    a.resource_inventory.update({"wood": 400.0})
    sim._trade_tick(route)
    boosted = b.resource_inventory["wood"]
    assert boosted == pytest.approx(normal * (1.0 + HIGHWAY_TRADE_BONUS))


# ----------------------------------------------------------------------
# Rule hook + determinism + persistence
# ----------------------------------------------------------------------

def test_maybe_start_highways_requires_wealth():
    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    a.resource_inventory["stone"] = 5.0  # below reserve*10
    maybe_start_highways(sim, a)
    assert len(sim.highway_projects) == 0
    a.resource_inventory["stone"] = 100.0
    maybe_start_highways(sim, a)
    assert len(sim.highway_projects) == 1


def test_deterministic_ids_and_paths():
    sim1, a1, b1 = _adjacent_pair(seed=99)
    sim2, a2, b2 = _adjacent_pair(seed=99)
    for s in (a1, b1, a2, b2):
        s.technologies.extend(["agriculture", "masonry"])
    p1 = plan_path(sim1, a1, b1)
    p2 = plan_path(sim2, a2, b2)
    assert p1 == p2
    proj1 = start_highway(sim1, a1, b1, tick=10)
    proj2 = start_highway(sim2, a2, b2, tick=10)
    assert proj1 is not None and proj2 is not None
    assert proj1.id == proj2.id


def test_highway_round_trips_serialization():
    from worldsim.db import _decode_highway, _encode_highway

    sim, a, b = _adjacent_pair()
    for s in (a, b):
        s.technologies.extend(["agriculture", "masonry"])
    project = start_highway(sim, a, b, tick=5)
    project.segments_done = 3
    restored = _decode_highway(_encode_highway(project))
    assert restored.id == project.id
    assert restored.path == project.path
    assert restored.segments_done == 3
    assert restored.a_id == a.id and restored.b_id == b.id


def test_frozen_contract_unchanged():
    from worldsim.actions import NUM_ACTIONS
    from worldsim.agents import OBSERVATION_DIM

    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
