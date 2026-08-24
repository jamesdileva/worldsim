"""Sprint 53: web frontend v1 — static assets + grid/charts endpoints."""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # noqa: E402

from worldsim.actions import NUM_ACTIONS  # noqa: E402
from worldsim.agents import OBSERVATION_DIM  # noqa: E402
from worldsim.db import WorldStore  # noqa: E402
from worldsim.simulation import Simulation  # noqa: E402
from worldsim.webapp import WorldSession, create_app  # noqa: E402
from worldsim.world import World  # noqa: E402


@pytest.fixture
def client(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    session = WorldSession(store=store)
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    for _ in range(20):
        sim.step()
    session.sim = sim
    session.world_id = "test"
    app = create_app(session)
    app.state.test_session = session
    tc = TestClient(app)
    yield tc
    store.close()


# ----------------------------------------------------------------------
# Static frontend
# ----------------------------------------------------------------------

def test_index_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "WorldSim" in response.text
    assert 'src="/static/app.js"' in response.text


def test_static_assets_exist(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


# ----------------------------------------------------------------------
# Grid endpoint for the canvas
# ----------------------------------------------------------------------

def test_grid_shape_and_content(client):
    grid = client.get("/api/grid").json()
    assert grid["size"] == 64
    assert len(grid["terrain"]) == 64
    assert all(len(row) == 64 for row in grid["terrain"])
    assert all(0 <= v <= 5 for row in grid["terrain"] for v in row)
    assert isinstance(grid["roads"], list)
    assert len(grid["settlements"]) == 2
    names = {s["name"] for s in grid["settlements"]}
    assert any(name for name in names)


def test_grid_includes_contamination_zones(client):
    victim = client.app.state.test_session.sim.settlements[0]
    client.app.state.test_session.sim.god_nuke(victim.spawn_x, victim.spawn_y)
    grid = client.get("/api/grid").json()
    assert len(grid["zones"]) == 1
    assert grid["zones"][0]["radius"] > 0


# ----------------------------------------------------------------------
# Chart endpoints (in-memory PNGs)
# ----------------------------------------------------------------------

def test_population_chart_endpoint(client):
    for _ in range(520):
        client.app.state.test_session.sim.step()
    response = client.get("/api/charts/populations.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) > 500


def test_events_chart_endpoint(client):
    response = client.get("/api/charts/events.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_population_chart_404_without_epochs(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    session = WorldSession(store=store)
    session.sim = Simulation(World(seed=1, size=32))
    session.sim.spawn_settlements(count=1)
    client = TestClient(create_app(session))
    try:
        assert client.get("/api/charts/populations.png").status_code == 404
    finally:
        store.close()


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
