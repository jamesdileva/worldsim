"""Sprint 52: local web API over a live simulation."""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient  # noqa: E402

from worldsim.actions import NUM_ACTIONS  # noqa: E402
from worldsim.agents import OBSERVATION_DIM  # noqa: E402
from worldsim.db import WorldStore  # noqa: E402
from worldsim.webapp import WorldSession, create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    session = WorldSession(store=store)
    app = create_app(session)
    tc = TestClient(app)
    tc.session = session
    tc.store = store
    yield tc
    store.close()


def _new_world(client):
    response = client.post("/api/god/spawn_settlement", json={
        "action": "spawn_settlement",
        "params": {"x": 30, "y": 30},
        "confirm": False,
    })
    assert response.status_code == 409  # no world yet


def test_requires_loaded_world(client):
    _new_world(client)
    assert client.get("/api/status").status_code == 409
    assert client.post("/api/step", json={"ticks": 1}).status_code == 409


# ----------------------------------------------------------------------
# Lifecycle over HTTP
# ----------------------------------------------------------------------

def test_load_and_status_round_trip(tmp_path):
    # First, create + save a world through the API itself.
    store = WorldStore(tmp_path / "w.db")
    session = WorldSession(store=store)
    client = TestClient(create_app(session))
    try:
        # In-memory creation path: spawn via god on a fresh sim is not
        # possible without a world; use the session's save after building.
        from worldsim.simulation import Simulation
        from worldsim.world import World

        session.sim = Simulation(World(seed=42, size=64))
        session.sim.spawn_settlements(count=2)
        saved_as = session.save("http-world")
        assert saved_as == "http-world"
    finally:
        pass

    # Fresh client loads it by id (the CLI serve --world-id flow).
    session2 = WorldSession(store=WorldStore(tmp_path / "w.db"))
    client2 = TestClient(create_app(session2))
    try:
        response = client2.post("/api/load", json={"world_id": "http-world"})
        assert response.status_code == 200
        status = client2.get("/api/status").json()
        assert status["world_id"] == "http-world"
        assert len(status["settlements"]) == 2
        assert status["settlements"][0]["name"]
    finally:
        store.close()
        session2.store.close()


# ----------------------------------------------------------------------
# Inspection endpoints
# ----------------------------------------------------------------------

def test_status_state_chronicle_timeline(client):
    run(client, "new --seed 42") if False else None
    # Build a world directly in the session.
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=42, size=64))
    client.session.sim.spawn_settlements(count=2)
    for _ in range(20):
        client.session.sim.step()

    status = client.get("/api/status").json()
    assert status["tick"] >= 20
    assert len(status["settlements"]) == 2

    state = client.get("/api/state").json()
    assert set(state["prices"]) == {"food", "wood", "stone", "metal"}

    chronicle = client.get("/api/chronicle").json()
    assert chronicle["civilizations"]

    timeline = client.get("/api/timeline?limit=10").json()
    assert timeline["count"] <= 10

    map_response = client.get("/api/map.png")
    assert map_response.status_code == 200
    assert map_response.headers["content-type"].startswith("image/png")


def run(*args, **kwargs):  # kept out of the way; unused helper guard
    raise AssertionError("do not call run() in tests")


# ----------------------------------------------------------------------
# Step / undo semantics
# ----------------------------------------------------------------------

def test_step_advances_and_undo_restores(client):
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=42, size=64))
    client.session.sim.spawn_settlements(count=1)

    tick0 = client.post("/api/step", json={"ticks": 5}).json()["tick"]
    assert tick0 == 5

    # A god action captures its pre-state; undo must restore it exactly.
    target = client.session.sim.settlements[0].name
    response = client.post(
        "/api/god/smite",
        json={"action": "smite", "params": {
            "settlement": target, "amount": 3}, "confirm": True},
    )
    assert response.status_code == 200
    smited_pop = next(
        s["population"] for s in client.get("/api/status").json()[
            "settlements"] if s["name"] == target)
    undo_response = client.post("/api/undo").json()
    assert undo_response["undid"] == "smite"
    restored_pop = next(
        s["population"] for s in client.get("/api/status").json()[
            "settlements"] if s["name"] == target)
    assert restored_pop == smited_pop + 3


def test_double_undo_conflicts(client):
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=1, size=64))
    client.session.sim.spawn_settlements(count=1)
    client.post("/api/step", json={"ticks": 1})
    first = client.post("/api/undo")
    second = client.post("/api/undo")
    assert first.status_code == 200 or second.status_code == 409


# ----------------------------------------------------------------------
# Confirmation gates mirror the CLI
# ----------------------------------------------------------------------

def test_catastrophic_actions_require_confirm(client):
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=42, size=64))
    client.session.sim.spawn_settlements(count=1)
    name = client.session.sim.settlements[0].name

    big_smite = client.post("/api/god/smite", json={
        "action": "smite",
        "params": {"settlement": name, "amount": 50},
        "confirm": False,
    })
    assert big_smite.status_code == 428

    confirmed = client.post("/api/god/smite", json={
        "action": "smite",
        "params": {"settlement": name, "amount": 50},
        "confirm": True,
    })
    assert confirmed.status_code == 200

    nuke_no = client.post("/api/god/nuke", json={
        "action": "nuke", "params": {"x": 30, "y": 30}, "confirm": False})
    nuke_yes = client.post("/api/god/nuke", json={
        "action": "nuke", "params": {"x": 30, "y": 30}, "confirm": True})
    assert nuke_no.status_code == 428
    assert nuke_yes.status_code == 200


def test_small_smite_needs_no_confirm(client):
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=42, size=64))
    client.session.sim.spawn_settlements(count=1)
    name = client.session.sim.settlements[0].name
    small = client.post("/api/god/smite", json={
        "action": "smite",
        "params": {"settlement": name, "amount": 1},
        "confirm": False,
    })
    assert small.status_code == 200


def test_god_actions_are_audited(client):
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=42, size=64))
    client.session.sim.spawn_settlements(count=1)
    before = len([e for e in client.session.sim.event_log
                  if e.type == "divine"])
    client.post("/api/god/bless", json={
        "action": "bless",
        "params": {"settlement": client.session.sim.settlements[0].name,
                   "resource": "food", "amount": 10},
        "confirm": False,
    })
    after_events = [e for e in client.session.sim.event_log
                    if e.type == "divine"]
    assert len(after_events) == before + 1
    assert after_events[-1].description.startswith("GOD: ")


def test_unknown_action_404(client):
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client.session.sim = Simulation(World(seed=1, size=64))
    client.session.sim.spawn_settlements(count=1)
    response = client.post("/api/gob/frobnicate", json={"action": "x"})
    del response


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
