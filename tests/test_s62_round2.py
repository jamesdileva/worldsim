"""Sprint 62 round 2: real antagonists, lapsible alliances, highway
rendering data, stockpiles in status, viable god colonies."""

import pytest

from worldsim.db import WorldStore
from worldsim.infrastructure import HighwayProject
from worldsim.relations import FRIENDLY_THRESHOLD
from worldsim.simulation import GOD_SPAWN_POPULATION, Simulation
from worldsim.world import World


def _web_client(sim, tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from worldsim.webapp import WorldSession, create_app

    store = WorldStore(tmp_path / "w.db")
    session = WorldSession(store=store)
    session.sim = sim
    session.world_id = "test"
    return TestClient(create_app(session)), store


def test_antagonist_enforced_even_with_passive_militaries():
    # Seed 3 regression: two natural military archetypes with aggression
    # 0.46 / 0.126 - nobody ever raided. The guarantee must still fire.
    sim = Simulation(World(seed=3, size=64))
    sim.spawn_settlements(count=3)
    qualified = [
        s for s in sim.settlements
        if s.personality.get("archetype") == "military"
        and s.personality.get("aggression", 0.0) >= 0.75
    ]
    assert len(qualified) == 1


def test_cold_alliances_dissolve():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    a, b = sim.settlements[0], sim.settlements[1]
    assert sim.diplomacy.form_alliance(a.id, b.id)
    # Drive relations ice-cold.
    for _ in range(40):
        sim.relations.adjust(a.id, b.id, -10.0)
    assert sim.relations.score(a.id, b.id) < FRIENDLY_THRESHOLD
    sim._refresh_contested_zones()
    assert not sim.diplomacy.is_allied(a.id, b.id)


def test_grid_includes_highway_tiles(tmp_path):
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    project = HighwayProject(
        a_id=sim.settlements[0].id,
        b_id=sim.settlements[1].id,
        sponsor_id=sim.settlements[0].id,
        path=[(5, 5), (5, 6), (5, 7)],
        segments_done=2,
    )
    sim.highway_projects.append(project)
    client, store = _web_client(sim, tmp_path)
    try:
        grid = client.get("/api/grid").json()
        # path entries are (y, x); two segments laid -> tiles (5,5),(5,6)
        assert [5, 5] in grid["highways"]
        assert [6, 5] in grid["highways"]
        assert [7, 5] not in grid["highways"]  # not laid yet
    finally:
        store.close()


def test_status_includes_stockpiles(tmp_path):
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    client, store = _web_client(sim, tmp_path)
    try:
        status = client.get("/api/status").json()
        first = status["settlements"][0]
        for key in ("food_stock", "wood", "stone", "metal"):
            assert key in first
    finally:
        store.close()


def test_god_spawn_colony_starts_viable():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    target = sim.settlements[0]
    _before, after = sim.god_spawn_settlement(
        target.spawn_x + 1, target.spawn_y + 1)
    newcomer = sim.settlements[-1]
    assert newcomer.population == GOD_SPAWN_POPULATION
