import json
import sqlite3

import numpy as np
import pytest

from worldsim.db import (
    WorldStore,
    deserialize_world,
    serialize_world,
)
from worldsim.world import World


def test_serialize_round_trip_exact():
    world = World(seed=555)
    restored, settlements = deserialize_world(serialize_world(world))
    np.testing.assert_array_equal(world.terrain, restored.terrain)
    np.testing.assert_array_equal(world.elevation, restored.elevation)
    np.testing.assert_array_equal(world.moisture, restored.moisture)
    assert restored.seed == world.seed
    assert restored.size == world.size
    assert settlements == []


def test_save_and_load_world(tmp_path):
    db = tmp_path / "world.db"
    store = WorldStore(db)
    try:
        world_id = store.save_world(World(seed=777), snapshot_tick=0)
        loaded, _ = store.load_latest_snapshot(world_id)
        original = World(seed=777)
        np.testing.assert_array_equal(original.terrain, loaded.terrain)
    finally:
        store.close()


def test_schema_tables_exist(tmp_path):
    db = tmp_path / "world.db"
    store = WorldStore(db)
    try:
        rows = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert {"worlds", "snapshots"} <= names
    finally:
        store.close()


def test_snapshot_state_is_compressed_json(tmp_path):
    db = tmp_path / "world.db"
    store = WorldStore(db)
    try:
        world_id = store.save_world(World(seed=1))
        (state_json,) = store._conn.execute(
            "SELECT state_json FROM snapshots WHERE world_id = ?", (world_id,)
        ).fetchone()
        state = json.loads(state_json)
        assert state["seed"] == 1
        assert set(state) == {
            "seed",
            "size",
            "tick",
            "elevation",
            "moisture",
            "terrain",
            "ownership",
            "improvements",
            "settlements",
        }
    finally:
        store.close()


def test_load_missing_world_raises(tmp_path):
    store = WorldStore(tmp_path / "world.db")
    try:
        with pytest.raises(ValueError):
            store.load_latest_snapshot("nonexistent")
    finally:
        store.close()


def test_ownership_defaults_unowned():
    world = World(seed=8)
    assert (world.ownership == -1).all()


def test_sqlite_file_created_at_expected_path(tmp_path):
    db = tmp_path / "nested" / "world.db"
    store = WorldStore(db)
    store.close()
    assert db.exists()
