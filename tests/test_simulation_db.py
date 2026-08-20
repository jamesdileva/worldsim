import sqlite3

from worldsim.db import WorldStore, deserialize_world, serialize_world
from worldsim.simulation import Simulation
from worldsim.world import World


def test_settlement_round_trip_exact():
    sim = Simulation(World(seed=31337))
    s = sim.spawn_settlement()
    for _ in range(50):
        sim.step()

    world2, settlements, routes = deserialize_world(
        serialize_world(sim.world, sim.settlements)
    )
    assert len(settlements) == 1
    r = settlements[0]
    assert (r.id, r.name) == (s.id, s.name)
    assert (r.spawn_x, r.spawn_y) == (s.spawn_x, s.spawn_y)
    assert r.population == s.population
    assert r.food_stock == s.food_stock
    assert r.growth_progress == s.growth_progress
    assert r.starvation_progress == s.starvation_progress
    assert r.net_food_rate == s.net_food_rate
    import numpy as np

    np.testing.assert_array_equal(world2.ownership, sim.world.ownership)
    assert world2.tick == sim.world.tick
    assert routes == []


def test_store_persists_settlement_rows(tmp_path):
    db = tmp_path / "world.db"
    store = WorldStore(db)
    try:
        sim = Simulation(World(seed=777))
        s = sim.spawn_settlement()
        for _ in range(30):
            sim.step()
        world_id = store.save_world(sim.world, sim.settlements)

        loaded_world, loaded_settlements, _ = store.load_latest_snapshot(world_id)
        rows = store._conn.execute(
            "SELECT id, name, world_id, spawn_x, spawn_y, created_at_tick "
            "FROM settlements WHERE world_id = ?",
            (world_id,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == s.id
        assert rows[0][1] == s.name
        assert len(loaded_settlements) == 1
        assert loaded_settlements[0].population == s.population
        assert loaded_world.tick == sim.tick
    finally:
        store.close()


def test_destroyed_settlement_persists_death_tick(tmp_path):
    db = tmp_path / "world.db"
    store = WorldStore(db)
    try:
        sim = Simulation(World(seed=5))
        s = sim.spawn_settlement()
        s.population = 1
        s.food_stock = 0
        for _ in range(60):
            sim.step()
            if not s.is_alive:
                break
        world_id = store.save_world(sim.world, sim.settlements)
        (destroyed,) = store._conn.execute(
            "SELECT destroyed_at_tick FROM settlements WHERE id = ?", (s.id,)
        ).fetchone()
        assert destroyed == s.destroyed_at_tick
    finally:
        store.close()


def test_settlements_table_schema(tmp_path):
    store = WorldStore(tmp_path / "world.db")
    try:
        cols = {
            r[1]
            for r in store._conn.execute("PRAGMA table_info(settlements)").fetchall()
        }
        assert cols == {
            "id",
            "name",
            "world_id",
            "spawn_x",
            "spawn_y",
            "created_at_tick",
            "destroyed_at_tick",
        }
    finally:
        store.close()


def test_sqlite_connection_usable_after_load(tmp_path):
    store = WorldStore(tmp_path / "world.db")
    try:
        sim = Simulation(World(seed=1))
        s = sim.spawn_settlement()
        world_id = store.save_world(sim.world, [s])
        _, loaded, _ = store.load_latest_snapshot(world_id)
        assert loaded[0].id == s.id
    finally:
        store.close()
