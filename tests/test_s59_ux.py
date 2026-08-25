"""Sprint 59 desktop-UX round: road density cap, god spawn claim,
war overlay grid data, optional LLM advisor wiring."""

import numpy as np

import pytest

from worldsim.db import WorldStore
from worldsim.simulation import ROAD_DENSITY_CAP, Simulation
from worldsim.world import World


def _sim(ticks: int = 0) -> Simulation:
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=3)
    for _ in range(ticks):
        sim.step()
    return sim


# ----------------------------------------------------------------------
# Road density cap
# ----------------------------------------------------------------------

def test_territory_roads_respect_density_cap():
    sim = _sim(ticks=800)
    worst = 0.0
    for s in sim.settlements:
        if not s.is_alive:
            continue
        owned = int(sim._owned_mask(s).sum())
        roads = len(sim.roads_of(s))
        if owned > 0:
            worst = max(worst, roads / owned)
    assert worst <= ROAD_DENSITY_CAP + 0.05, (
        f"road density {worst:.2f} exceeds cap {ROAD_DENSITY_CAP}")


def test_roads_serve_buildings():
    # S60 preference: at least one road tile should sit beside a
    # building once a city has both.
    sim = _sim(ticks=800)
    found = False
    for s in sim.settlements:
        if not s.is_alive:
            continue
        idx = sim.settlements.index(s)
        roads = {
            (y, x)
            for y, x in np.argwhere(
                sim.world.improvements == 0)
            if sim.world.ownership[y, x] == idx
        }
        buildings = {
            (int(y), int(x))
            for y, x in np.argwhere(
                (sim.world.ownership == idx)
                & (sim.world.improvements >= 1))
        }
        if buildings and roads:
            for by, bx in buildings:
                for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    if (by + dy, bx + dx) in roads:
                        found = True
                        break
        if found:
            break
    assert found, "no road serves any building"


# ----------------------------------------------------------------------
# Treaty friction (S60): aggressive targets demand warmer relations
# ----------------------------------------------------------------------

def _accept_fixture():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    a, b = sim.settlements[0], sim.settlements[1]
    return sim, a, b


def test_wary_aggressive_target_refuses_lukewarm_relations():
    from worldsim.treaties import CLAUSE_TRADE_PACT, would_accept

    sim, a, b = _accept_fixture()
    b.personality["aggression"] = 0.95
    sim.relations.adjust(a.id, b.id, 30.0)  # friendly but not warm
    ok, reason = would_accept(
        sim, a, b, [CLAUSE_TRADE_PACT])
    assert not ok and reason == "target_wary_of_aggression"


def test_mild_target_accepts_as_before():
    from worldsim.treaties import CLAUSE_TRADE_PACT, would_accept

    sim, a, b = _accept_fixture()
    b.personality["aggression"] = 0.5
    sim.relations.adjust(a.id, b.id, 30.0)
    ok, reason = would_accept(sim, a, b, [CLAUSE_TRADE_PACT])
    assert ok, reason


# ----------------------------------------------------------------------
# God spawn claims owned land
# ----------------------------------------------------------------------

def test_god_spawn_claims_owned_land():
    sim = _sim(ticks=200)
    target = next(s for s in sim.settlements if s.is_alive)
    before = len([s for s in sim.settlements if s.is_alive])
    before_, after = sim.god_spawn_settlement(
        target.spawn_x + 1, target.spawn_y + 1)
    assert after["settlements"] == before + 1
    # The newcomer exists and took land inside the old city's area.
    newcomer = sim.settlements[-1]
    assert newcomer.is_alive
    assert sim.world.ownership[newcomer.spawn_y, newcomer.spawn_x] == (
        len(sim.settlements) - 1)


def test_god_spawn_water_click_stays_near_shore():
    sim = Simulation(World(seed=7, size=48))
    sim.spawn_settlements(count=1)
    water = [
        (y, x)
        for y in range(48) for x in range(48)
        if int(sim.world.terrain[y, x]) == 0
    ]
    if not water:
        pytest.skip("seed has no water tiles")
    wy, wx = water[0]
    try:
        _before, after = sim.god_spawn_settlement(wx, wy)
    except ValueError:
        return  # deep water: fails loudly — correct
    # Shallow-water click: spawn must be on land within the bound.
    newcomer = sim.settlements[-1]
    assert int(sim.world.terrain[newcomer.spawn_y, newcomer.spawn_x]) != 0
    dist = max(abs(newcomer.spawn_x - wx), abs(newcomer.spawn_y - wy))
    assert dist <= 8
    assert after["x"] == newcomer.spawn_x


# ----------------------------------------------------------------------
# War overlay data in /api/grid
# ----------------------------------------------------------------------

def test_grid_includes_wars(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from worldsim.webapp import WorldSession, create_app

    store = WorldStore(tmp_path / "w.db")
    try:
        session = WorldSession(store=store)
        sim = Simulation(World(seed=42, size=64))
        sim.spawn_settlements(count=3)
        session.sim = sim
        session.world_id = "test"
        a, b = sim.settlements[0], sim.settlements[1]
        sim.diplomacy.declare_war(a.id, b.id, sim.tick)
        client = TestClient(create_app(session))
        grid = client.get("/api/grid").json()
        pairs = [
            ((w["a"]["name"], w["b"]["name"]),
             (w["a"]["x"], w["a"]["y"]), (w["b"]["x"], w["b"]["y"]))
            for w in grid["wars"]
        ]
        names = {frozenset(p[0]) for p in pairs}
        assert frozenset((a.name, b.name)) in names
        for _names, pa, pb in pairs:
            assert 0 <= pa[0] < 64 and 0 <= pb[0] < 64
    finally:
        store.close()


# ----------------------------------------------------------------------
# Optional LLM advisor wiring
# ----------------------------------------------------------------------

def test_enable_llm_attaches_agents_on_new_world(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from worldsim.agents import Agent
    from worldsim.llm_agent import LLMDrivenAgent
    from worldsim.webapp import WorldSession, create_app

    store = WorldStore(tmp_path / "w.db")
    try:
        session = WorldSession(store=store)
        assert session.enable_llm() is True
        client = TestClient(create_app(session))
        response = client.post("/api/new", json={
            "seed": 42, "settlements": 3})
        assert response.status_code == 200
        llm_slots = [
            a for a in session.sim.agents
            if isinstance(a, LLMDrivenAgent)
        ]
        assert len(llm_slots) == len(session.sim.settlements)
        # Rule fallback intact: every agent still satisfies the contract.
        for agent in session.sim.agents:
            assert isinstance(agent, Agent)
    finally:
        store.close()


def test_enable_llm_model_override(tmp_path):
    from worldsim.webapp import WorldSession

    session = WorldSession(store=WorldStore(tmp_path / 'w.db'))
    assert session.enable_llm(model='gemma2:2b') is True
    assert session.llm_client.config.model == 'gemma2:2b'
    session.llm_advisor = None
