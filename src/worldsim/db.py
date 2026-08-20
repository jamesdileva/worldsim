"""SQLite persistence: worlds and snapshots tables (architecture_detailed.md §24.1).

World state is stored as compressed JSON snapshots. Tile arrays are serialized
as base64-encoded raw bytes so round-trips are exact (byte-level determinism).
"""

from __future__ import annotations

import base64
import json
import sqlite3
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .settlement import Settlement
from .world import UNOWNED, World

DEFAULT_DB_PATH = Path("data/world_sim/world.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
    id TEXT PRIMARY KEY,
    seed INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_tick INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
    tick INTEGER NOT NULL,
    world_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    PRIMARY KEY (tick, world_id)
);

CREATE TABLE IF NOT EXISTS settlements (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    world_id TEXT NOT NULL,
    spawn_x INTEGER NOT NULL,
    spawn_y INTEGER NOT NULL,
    created_at_tick INTEGER NOT NULL,
    destroyed_at_tick INTEGER
);
"""


def _encode_array(arr: np.ndarray) -> dict:
    return {
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
        "data": base64.b64encode(zlib.compress(arr.tobytes())).decode("ascii"),
    }


def _decode_array(obj: dict) -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(obj["data"]))
    return np.frombuffer(raw, dtype=np.dtype(obj["dtype"])).reshape(obj["shape"])


def _encode_settlement(s: Settlement) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "spawn_x": s.spawn_x,
        "spawn_y": s.spawn_y,
        "population": s.population,
        "food_stock": s.food_stock,
        "resource_inventory": s.resource_inventory,
        "created_at_tick": s.created_at_tick,
        "destroyed_at_tick": s.destroyed_at_tick,
        "growth_progress": s.growth_progress,
        "starvation_progress": s.starvation_progress,
        "net_food_rate": s.net_food_rate,
        "build_queue": list(s.build_queue),
    }


def _decode_settlement(obj: dict) -> Settlement:
    return Settlement(
        name=obj["name"],
        spawn_x=obj["spawn_x"],
        spawn_y=obj["spawn_y"],
        population=obj["population"],
        food_stock=obj["food_stock"],
        resource_inventory=obj["resource_inventory"],
        id=obj["id"],
        created_at_tick=obj["created_at_tick"],
        destroyed_at_tick=obj["destroyed_at_tick"],
        growth_progress=obj["growth_progress"],
        starvation_progress=obj["starvation_progress"],
        net_food_rate=obj["net_food_rate"],
        build_queue=list(obj.get("build_queue", [])),
    )


def serialize_world(world: World, settlements: list[Settlement] | None = None) -> str:
    state = {
        "seed": world.seed,
        "size": world.size,
        "tick": world.tick,
        "elevation": _encode_array(world.elevation),
        "moisture": _encode_array(world.moisture),
        "terrain": _encode_array(world.terrain),
        "ownership": _encode_array(world.ownership),
        "improvements": _encode_array(world.improvements),
        "settlements": [_encode_settlement(s) for s in (settlements or [])],
    }
    return json.dumps(state, sort_keys=True)


def deserialize_world(
    state_json: str,
) -> tuple[World, list[Settlement]]:
    state = json.loads(state_json)
    world = World(seed=state["seed"], size=state["size"])
    world.tick = state["tick"]
    world.elevation = _decode_array(state["elevation"])
    world.moisture = _decode_array(state["moisture"])
    world.terrain = _decode_array(state["terrain"])
    if "ownership" in state:
        world.ownership = _decode_array(state["ownership"])
    if "improvements" in state:
        world.improvements = _decode_array(state["improvements"])
    settlements = [
        _decode_settlement(obj) for obj in state.get("settlements", [])
    ]
    return world, settlements


@dataclass
class WorldRecord:
    world_id: str
    seed: int


class WorldStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_world(
        self,
        world: World,
        settlements: list[Settlement] | None = None,
        snapshot_tick: int | None = None,
    ) -> str:
        """Insert a world row, write a snapshot, and upsert settlement rows."""
        world_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        tick = snapshot_tick if snapshot_tick is not None else world.tick
        with self._conn:
            self._conn.execute(
                "INSERT INTO worlds (id, seed, created_at, last_tick) VALUES (?, ?, ?, ?)",
                (world_id, world.seed, created_at, tick),
            )
            self._conn.execute(
                "INSERT INTO snapshots (tick, world_id, state_json) VALUES (?, ?, ?)",
                (tick, world_id, serialize_world(world, settlements)),
            )
            for s in settlements or []:
                self._conn.execute(
                    "INSERT INTO settlements "
                    "(id, name, world_id, spawn_x, spawn_y, created_at_tick, destroyed_at_tick) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        s.id,
                        s.name,
                        world_id,
                        s.spawn_x,
                        s.spawn_y,
                        s.created_at_tick,
                        s.destroyed_at_tick,
                    ),
                )
        return world_id

    def load_latest_snapshot(self, world_id: str) -> tuple[World, list[Settlement]]:
        row = self._conn.execute(
            "SELECT state_json FROM snapshots WHERE world_id = ? "
            "ORDER BY tick DESC LIMIT 1",
            (world_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No snapshot found for world {world_id}")
        return deserialize_world(row[0])
