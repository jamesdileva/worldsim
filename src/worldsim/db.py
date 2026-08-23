"""SQLite persistence: worlds and snapshots tables (architecture_detailed.md ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â§24.1).

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

from .disasters import DisasterEvent, DisasterType
from .diplomacy import DiplomacyState
from .relations import RelationMatrix
from .settlement import Settlement
from .simulation import BuildingDebuff, RuinSite, TradeRoute, WorldEvent
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

-- Per-settlement resource inventory snapshots (Sprint 4).
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    food REAL NOT NULL DEFAULT 0,
    wood REAL NOT NULL DEFAULT 0,
    stone REAL NOT NULL DEFAULT 0,
    metal REAL NOT NULL DEFAULT 0
);

-- Trade route registry (Sprint 4).
CREATE TABLE IF NOT EXISTS trade_routes (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    dest_id TEXT NOT NULL,
    established_tick INTEGER NOT NULL,
    transfers INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

-- God Mode interventions with before/after state (Sprint 6).
CREATE TABLE IF NOT EXISTS god_events (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT,
    before_state JSON,
    after_state JSON
);

-- Agent experience log (Sprint 7): one row per settlement per tick.
CREATE TABLE IF NOT EXISTS agent_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id TEXT NOT NULL,
    tick INTEGER NOT NULL,
    observation BLOB NOT NULL,
    action INTEGER NOT NULL,
    reward REAL NOT NULL,
    next_observation BLOB NOT NULL,
    done BOOLEAN NOT NULL DEFAULT 0
);

-- Baseline performance metrics per benchmark world (Sprint 8).
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seed INTEGER NOT NULL,
    agent_type TEXT NOT NULL,
    ticks_requested INTEGER NOT NULL,
    settlements INTEGER NOT NULL,
    survivors INTEGER NOT NULL,
    peak_population INTEGER NOT NULL,
    final_population INTEGER NOT NULL,
    avg_survival_ticks REAL NOT NULL,
    food_final REAL NOT NULL,
    wood_final REAL NOT NULL,
    stone_final REAL NOT NULL,
    created_at TEXT NOT NULL
);

-- Inter-settlement interaction log (Sprint 9).
CREATE TABLE IF NOT EXISTS world_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    type TEXT NOT NULL,
    actor_ids TEXT NOT NULL,
    description TEXT NOT NULL
);

-- Trained policy checkpoints (Sprint 14; checksums Sprint 16).
CREATE TABLE IF NOT EXISTS policy_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation TEXT NOT NULL,
    path TEXT NOT NULL,
    algorithm TEXT NOT NULL DEFAULT 'PPO',
    total_timesteps INTEGER NOT NULL,
    episodes INTEGER,
    mean_episode_return REAL,
    wall_time_seconds REAL,
    created_at TEXT NOT NULL
);

-- Policy evaluation results per run (Sprint 16).
CREATE TABLE IF NOT EXISTS training_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_generation_a TEXT NOT NULL,
    policy_generation_b TEXT,
    eval_seed_base INTEGER NOT NULL,
    worlds INTEGER NOT NULL,
    wins_a INTEGER NOT NULL,
    ties INTEGER NOT NULL,
    win_fraction REAL NOT NULL,
    mean_survival_a REAL NOT NULL,
    mean_survival_b REAL NOT NULL,
    results_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);"""

# Lightweight migration for databases created before a column existed.
_MIGRATIONS = [
    "ALTER TABLE policy_checkpoints ADD COLUMN checksum TEXT",
    "ALTER TABLE policy_checkpoints ADD COLUMN size_bytes INTEGER",
    "ALTER TABLE training_runs ADD COLUMN agent_type TEXT",
    "ALTER TABLE policy_checkpoints ADD COLUMN parent TEXT",
    "ALTER TABLE policy_checkpoints ADD COLUMN mutation TEXT",
]


def _encode_array(arr: np.ndarray) -> dict:
    return {
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
        "data": base64.b64encode(zlib.compress(arr.tobytes())).decode("ascii"),
    }


def _decode_array(obj: dict) -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(obj["data"]))
    # copy() makes the array writable (frombuffer returns a read-only view).
    return np.frombuffer(raw, dtype=np.dtype(obj["dtype"])).reshape(obj["shape"]).copy()


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
        "personality": dict(s.personality),
        "ruin_origin": s.ruin_origin,
        "strategy_label": s.strategy_label,
        "raids_committed": s.raids_committed,
        "routes_established": s.routes_established,
        "research_points": s.research_points,
        "technologies": list(s.technologies),
        "army": s.army,
        "fort_level": s.fort_level,
        "siege_progress": s.siege_progress,
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
        personality=dict(obj.get("personality", {})),
        ruin_origin=obj.get("ruin_origin"),
        strategy_label=obj.get("strategy_label", "settling"),
        raids_committed=obj.get("raids_committed", 0),
        routes_established=obj.get("routes_established", 0),
        research_points=obj.get("research_points", 0.0),
        technologies=list(obj.get("technologies", [])),
        army=obj.get("army", 0.0),
        fort_level=obj.get("fort_level", 0),
        siege_progress=obj.get("siege_progress", 0),
    )


def _encode_route(r: TradeRoute) -> dict:
    return {
        "id": r.id,
        "source_id": r.source_id,
        "dest_id": r.dest_id,
        "established_tick": r.established_tick,
        "transfers": r.transfers,
        "active": r.active,
    }


def _decode_route(obj: dict) -> TradeRoute:
    return TradeRoute(
        source_id=obj["source_id"],
        dest_id=obj["dest_id"],
        established_tick=obj["established_tick"],
        transfers=obj["transfers"],
        active=obj["active"],
        id=obj["id"],
    )


def _encode_ruin(r: RuinSite) -> dict:
    return {
        "id": r.id,
        "settlement_id": r.settlement_id,
        "name": r.name,
        "spawn_x": r.spawn_x,
        "spawn_y": r.spawn_y,
        "collapse_tick": r.collapse_tick,
        "era": getattr(r, "era", 1),
        "technologies": list(getattr(r, "technologies", [])),
        "salvage": dict(getattr(r, "salvage", {})),
    }


def _decode_ruin(obj: dict) -> RuinSite:
    return RuinSite(
        settlement_id=obj["settlement_id"],
        name=obj["name"],
        spawn_x=obj["spawn_x"],
        spawn_y=obj["spawn_y"],
        collapse_tick=obj["collapse_tick"],
        era=obj.get("era", 1),
        technologies=list(obj.get("technologies", [])),
        salvage=dict(obj.get("salvage", {})),
        id=obj["id"],
    )


def _encode_disaster(e: DisasterEvent) -> dict:
    return {
        "id": e.id,
        "type": int(e.type),
        "center_x": e.center_x,
        "center_y": e.center_y,
        "radius": e.radius,
        "start_tick": e.start_tick,
        "duration": e.duration,
    }


def _decode_disaster(obj: dict) -> DisasterEvent:
    return DisasterEvent(
        type=DisasterType(obj["type"]),
        center_x=obj["center_x"],
        center_y=obj["center_y"],
        radius=obj["radius"],
        start_tick=obj["start_tick"],
        duration=obj["duration"],
        id=obj["id"],
    )


def _encode_debuff(d: BuildingDebuff) -> dict:
    return {
        "x": d.x,
        "y": d.y,
        "multiplier": d.multiplier,
        "expires_tick": d.expires_tick,
        "cause": d.cause,
    }


def _decode_debuff(obj: dict) -> BuildingDebuff:
    return BuildingDebuff(
        x=obj["x"],
        y=obj["y"],
        multiplier=obj["multiplier"],
        expires_tick=obj["expires_tick"],
        cause=obj["cause"],
    )


def _encode_highway(p) -> dict:
    return {
        "id": p.id,
        "a_id": p.a_id,
        "b_id": p.b_id,
        "sponsor_id": p.sponsor_id,
        "path": [[y, x] for y, x in p.path],
        "segments_done": p.segments_done,
        "start_tick": p.start_tick,
        "completed": p.completed,
    }


def _encode_treaty(t) -> dict:
    return {
        "id": t.id,
        "party_a": t.party_a,
        "party_b": t.party_b,
        "clauses": list(t.clauses),
        "start_tick": t.start_tick,
        "expires_tick": t.expires_tick,
    }


def _decode_treaty(obj: dict):
    from .treaties import Treaty

    return Treaty(
        party_a=obj["party_a"],
        party_b=obj["party_b"],
        clauses=list(obj.get("clauses", [])),
        start_tick=obj.get("start_tick", 0),
        expires_tick=obj.get("expires_tick", 0),
        id=obj["id"],
    )


def _decode_highway(obj: dict):
    from .infrastructure import HighwayProject

    return HighwayProject(
        a_id=obj["a_id"],
        b_id=obj["b_id"],
        sponsor_id=obj["sponsor_id"],
        path=[(y, x) for y, x in obj["path"]],
        segments_done=obj.get("segments_done", 0),
        start_tick=obj.get("start_tick", 0),
        completed=obj.get("completed", False),
        id=obj["id"],
    )


def serialize_world(
    world: World,
    settlements: list[Settlement] | None = None,
    trade_routes: list[TradeRoute] | None = None,
    ruins: list[RuinSite] | None = None,
    disaster_events: list[DisasterEvent] | None = None,
    relations: RelationMatrix | None = None,
    contested: dict | None = None,
    building_debuffs: list[BuildingDebuff] | None = None,
    event_log: list[WorldEvent] | None = None,
    diplomacy: DiplomacyState | None = None,
    strategy_memory: dict | None = None,
    highway_projects: list | None = None,
    treaties: list | None = None,
) -> str:
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
        "trade_routes": [_encode_route(r) for r in (trade_routes or [])],
        "ruins": [_encode_ruin(r) for r in (ruins or [])],
        "disaster_events": [_encode_disaster(e) for e in (disaster_events or [])],
        "relations": relations.to_dict() if relations else {},
        "contested": [
            {"x": x, "y": y, "expiry": expiry}
            for (x, y), expiry in (contested or {}).items()
        ],
        "building_debuffs": [
            _encode_debuff(d) for d in (building_debuffs or [])
        ],
        "event_log": [
            {
                "tick": e.tick,
                "type": e.type,
                "actor_ids": e.actor_ids,
                "description": e.description,
            }
            for e in (event_log or [])
        ],
        "diplomacy": diplomacy.to_dict() if diplomacy else {},
        "strategy_memory": [
            {"archetype": arch, "action": action, "ema_reward": value}
            for (arch, action), value in (strategy_memory or {}).items()
        ],
        "highway_projects": [
            _encode_highway(p) for p in (highway_projects or [])
        ],
        "treaties": [_encode_treaty(t) for t in (treaties or [])],
    }
    return json.dumps(state, sort_keys=True)


def deserialize_world(
    state_json: str,
) -> tuple[
    World,
    list[Settlement],
    list[TradeRoute],
    list[RuinSite],
    list[DisasterEvent],
    RelationMatrix,
    dict,
    list[BuildingDebuff],
    list[WorldEvent],
    DiplomacyState,
    dict,
]:
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
    trade_routes = [
        _decode_route(obj) for obj in state.get("trade_routes", [])
    ]
    ruins = [_decode_ruin(obj) for obj in state.get("ruins", [])]
    disaster_events = [
        _decode_disaster(obj) for obj in state.get("disaster_events", [])
    ]
    relations = RelationMatrix.from_dict(state.get("relations", {}))
    contested = {
        (obj["x"], obj["y"]): obj["expiry"]
        for obj in state.get("contested", [])
    }
    building_debuffs = [
        _decode_debuff(obj) for obj in state.get("building_debuffs", [])
    ]
    event_log = [
        WorldEvent(
            tick=obj["tick"],
            type=obj["type"],
            actor_ids=list(obj["actor_ids"]),
            description=obj["description"],
        )
        for obj in state.get("event_log", [])
    ]
    diplomacy = DiplomacyState.from_dict(state.get("diplomacy", {}))
    strategy_memory = {
        (obj["archetype"], int(obj["action"])): obj["ema_reward"]
        for obj in state.get("strategy_memory", [])
    }
    highway_projects = [
        _decode_highway(obj) for obj in state.get("highway_projects", [])
    ]
    treaties = [_decode_treaty(obj) for obj in state.get("treaties", [])]
    return (
        world,
        settlements,
        trade_routes,
        ruins,
        disaster_events,
        relations,
        contested,
        building_debuffs,
        event_log,
        diplomacy,
        strategy_memory,
        highway_projects,
        treaties,
    )


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
        for migration in _MIGRATIONS:
            try:
                self._conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_world(
        self,
        world: World,
        settlements: list[Settlement] | None = None,
        snapshot_tick: int | None = None,
        trade_routes: list[TradeRoute] | None = None,
        ruins: list[RuinSite] | None = None,
        disaster_events: list[DisasterEvent] | None = None,
        relations: RelationMatrix | None = None,
        contested: dict | None = None,
        building_debuffs: list[BuildingDebuff] | None = None,
        event_log: list[WorldEvent] | None = None,
        diplomacy: DiplomacyState | None = None,
        strategy_memory: dict | None = None,
        highway_projects: list | None = None,
        treaties: list | None = None,
    ) -> str:
        """Insert a world row, write a snapshot, and upsert settlement,
        resource, and trade-route rows."""
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
                (
                    tick,
                    world_id,
                    serialize_world(
                        world,
                        settlements,
                        trade_routes,
                        ruins,
                        disaster_events,
                        relations,
                        contested,
                        building_debuffs,
                        event_log,
                        diplomacy,
                        strategy_memory,
                        highway_projects,
                        treaties,
                    ),
                ),
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
                inv = s.resource_inventory
                self._conn.execute(
                    "INSERT INTO resources "
                    "(settlement_id, tick, food, wood, stone, metal) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        s.id,
                        tick,
                        s.food_stock,
                        inv.get("wood", 0.0),
                        inv.get("stone", 0.0),
                        inv.get("metal", 0.0),
                    ),
                )
            for r in trade_routes or []:
                self._conn.execute(
                    "INSERT OR REPLACE INTO trade_routes "
                    "(id, source_id, dest_id, established_tick, transfers, active) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        r.id,
                        r.source_id,
                        r.dest_id,
                        r.established_tick,
                        r.transfers,
                        int(r.active),
                    ),
                )
        return world_id

    def load_latest_snapshot(
        self, world_id: str
    ) -> tuple[
        World,
        list[Settlement],
        list[TradeRoute],
        list[RuinSite],
        list[DisasterEvent],
        RelationMatrix,
        dict,
        list[BuildingDebuff],
        list[WorldEvent],
        DiplomacyState,
    ]:
        row = self._conn.execute(
            "SELECT state_json FROM snapshots WHERE world_id = ? "
            "ORDER BY tick DESC LIMIT 1",
            (world_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No snapshot found for world {world_id}")
        return deserialize_world(row[0])

    def insert_world_events(self, events: list[WorldEvent]) -> int:
        if not events:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO world_events (tick, type, actor_ids, description) "
                "VALUES (?, ?, ?, ?)",
                [
                    (e.tick, e.type, ",".join(e.actor_ids), e.description)
                    for e in events
                ],
            )
        return len(events)

    def world_exists(self, world_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM worlds WHERE id = ?", (world_id,)
        ).fetchone()
        return row is not None

    def save_world_with_id(
        self,
        world_id: str,
        world: World,
        settlements: list[Settlement] | None = None,
        trade_routes: list[TradeRoute] | None = None,
        ruins: list[RuinSite] | None = None,
        disaster_events: list[DisasterEvent] | None = None,
        relations: RelationMatrix | None = None,
        contested: dict | None = None,
        building_debuffs: list[BuildingDebuff] | None = None,
        event_log: list[WorldEvent] | None = None,
        diplomacy: DiplomacyState | None = None,
        strategy_memory: dict | None = None,
        highway_projects: list | None = None,
        treaties: list | None = None,
    ) -> str:
        """Save under a caller-chosen id (upsert)."""
        if self.world_exists(world_id):
            self.update_world(
                world_id,
                world,
                settlements,
                trade_routes=trade_routes,
                ruins=ruins,
                disaster_events=disaster_events,
                relations=relations,
                contested=contested,
                building_debuffs=building_debuffs,
                event_log=event_log,
                diplomacy=diplomacy,
                strategy_memory=strategy_memory,
                highway_projects=highway_projects,
                treaties=treaties,
            )
            return world_id
        created_at = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO worlds (id, seed, created_at, last_tick) VALUES (?, ?, ?, ?)",
                (world_id, world.seed, created_at, world.tick),
            )
            self._conn.execute(
                "INSERT INTO snapshots (tick, world_id, state_json) VALUES (?, ?, ?)",
                (
                    world.tick,
                    world_id,
                    serialize_world(
                        world,
                        settlements,
                        trade_routes,
                        ruins,
                        disaster_events,
                        relations,
                        contested,
                        building_debuffs,
                        event_log,
                        diplomacy,
                        strategy_memory,
                        highway_projects,
                        treaties,
                    ),
                ),
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
                inv = s.resource_inventory
                self._conn.execute(
                    "INSERT INTO resources "
                    "(settlement_id, tick, food, wood, stone, metal) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        s.id,
                        world.tick,
                        s.food_stock,
                        inv.get("wood", 0.0),
                        inv.get("stone", 0.0),
                        inv.get("metal", 0.0),
                    ),
                )
            for r in trade_routes or []:
                self._conn.execute(
                    "INSERT OR REPLACE INTO trade_routes "
                    "(id, source_id, dest_id, established_tick, transfers, active) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (r.id, r.source_id, r.dest_id, r.established_tick, r.transfers, int(r.active)),
                )
        return world_id

    def update_world(
        self,
        world_id: str,
        world: World,
        settlements: list[Settlement] | None = None,
        trade_routes: list[TradeRoute] | None = None,
        ruins: list[RuinSite] | None = None,
        disaster_events: list[DisasterEvent] | None = None,
        relations: RelationMatrix | None = None,
        contested: dict | None = None,
        building_debuffs: list[BuildingDebuff] | None = None,
        event_log: list[WorldEvent] | None = None,
        diplomacy: DiplomacyState | None = None,
        strategy_memory: dict | None = None,
        highway_projects: list | None = None,
        treaties: list | None = None,
    ) -> None:
        """Write a new snapshot for an existing world and bump last_tick."""
        if not self.world_exists(world_id):
            raise ValueError(f"Unknown world {world_id}")
        with self._conn:
            self._conn.execute(
                "UPDATE worlds SET last_tick = ? WHERE id = ?",
                (world.tick, world_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO snapshots (tick, world_id, state_json) "
                "VALUES (?, ?, ?)",
                (
                    world.tick,
                    world_id,
                    serialize_world(
                        world,
                        settlements,
                        trade_routes,
                        ruins,
                        disaster_events,
                        relations,
                        contested,
                        building_debuffs,
                        event_log,
                        diplomacy,
                        strategy_memory,
                        highway_projects,
                        treaties,
                    ),
                ),
            )

    def insert_agent_experiences(self, rows: list[tuple]) -> int:
        """Batch-insert (settlement_id, tick, obs, action, reward, next_obs,
        done) tuples. Callers buffer in RAM and flush periodically."""
        if not rows:
            return 0
        with self._conn:
            self._conn.executemany(
                "INSERT INTO agent_history "
                "(settlement_id, tick, observation, action, reward, "
                "next_observation, done) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def agent_history_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM agent_history"
        ).fetchone()
        return int(row[0])

    def insert_benchmark_run(self, metrics: dict) -> int:
        """Store one benchmark world's aggregated performance metrics."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO benchmark_runs "
                "(seed, agent_type, ticks_requested, settlements, survivors, "
                "peak_population, final_population, avg_survival_ticks, "
                "food_final, wood_final, stone_final, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metrics["seed"],
                    metrics["agent_type"],
                    metrics["ticks_requested"],
                    metrics["settlements"],
                    metrics["survivors"],
                    metrics["peak_population"],
                    metrics["final_population"],
                    metrics["avg_survival_ticks"],
                    metrics["food_final"],
                    metrics["wood_final"],
                    metrics["stone_final"],
                    created_at,
                ),
            )
        return int(cur.lastrowid)

    def insert_policy_checkpoint(self, metrics: dict) -> int:
        """Store a trained policy checkpoint record (Sprint 14/16)."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO policy_checkpoints "
                "(generation, path, algorithm, total_timesteps, episodes, "
                "mean_episode_return, wall_time_seconds, checksum, size_bytes,"
                " parent, mutation, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metrics.get("generation", "gen1"),
                    metrics["path"],
                    metrics.get("algorithm", "PPO"),
                    metrics["total_timesteps"],
                    metrics.get("episodes"),
                    metrics.get("mean_episode_return"),
                    metrics.get("wall_time_seconds"),
                    metrics.get("checksum"),
                    metrics.get("size_bytes"),
                    metrics.get("parent"),
                    metrics.get("mutation"),
                    created_at,
                ),
            )
        return int(cur.lastrowid)

    def get_latest_policy_checkpoint(
        self, generation: str
    ) -> dict | None:
        row = self._conn.execute(
            "SELECT id, generation, path, algorithm, total_timesteps, "
            "episodes, mean_episode_return, wall_time_seconds, checksum, "
            "size_bytes, parent, mutation, created_at FROM policy_checkpoints "
            "WHERE generation = ? ORDER BY id DESC LIMIT 1",
            (generation,),
        ).fetchone()
        if row is None:
            return None
        keys = [
            "id", "generation", "path", "algorithm", "total_timesteps",
            "episodes", "mean_episode_return", "wall_time_seconds",
            "checksum", "size_bytes", "parent", "mutation", "created_at",
        ]
        return dict(zip(keys, row))

    def insert_training_run(self, metrics: dict) -> int:
        """Store a paired evaluation run (Sprint 16)."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO training_runs "
                "(policy_generation_a, policy_generation_b, eval_seed_base, "
                "worlds, wins_a, ties, win_fraction, mean_survival_a, "
                "mean_survival_b, results_json, agent_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metrics["policy_generation_a"],
                    metrics.get("policy_generation_b"),
                    metrics["eval_seed_base"],
                    metrics["worlds"],
                    metrics["wins_a"],
                    metrics["ties"],
                    metrics["win_fraction"],
                    metrics["mean_survival_a"],
                    metrics["mean_survival_b"],
                    json.dumps(metrics.get("results_json", {})),
                    metrics.get("agent_type"),
                    created_at,
                ),
            )
        return int(cur.lastrowid)

    def log_god_event(
        self,
        world_id: str,
        tick: int,
        action_type: str,
        target: str | None,
        before_state: dict | None,
        after_state: dict | None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with self._conn:
            self._conn.execute(
                "INSERT INTO god_events "
                "(id, world_id, tick, action_type, target, before_state, after_state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    world_id,
                    tick,
                    action_type,
                    target,
                    json.dumps(before_state, sort_keys=True)
                    if before_state is not None
                    else None,
                    json.dumps(after_state, sort_keys=True)
                    if after_state is not None
                    else None,
                ),
            )
        return event_id

    def get_god_events(self, world_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, tick, action_type, target, before_state, after_state "
            "FROM god_events WHERE world_id = ? ORDER BY tick",
            (world_id,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "tick": r[1],
                "action_type": r[2],
                "target": r[3],
                "before_state": json.loads(r[4]) if r[4] else None,
                "after_state": json.loads(r[5]) if r[5] else None,
            }
            for r in rows
        ]
