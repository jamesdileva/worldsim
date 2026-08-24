"""Local web API over a live simulation (Sprint 52).

One process owns one living world; HTTP clients mount on it for
everything the CLI does: inspect, step/run/pause, play god, undo,
save/load, chronicle/timeline.

Contract:
- Mutations flow through the SAME god methods + undo-point capture as
  the CLI and live shell — audit/undo semantics identical everywhere.
- Catastrophic actions require explicit confirmation in the request
  payload ("confirm": true) — the API equivalent of --force/--confirm.
- The run loop steps a background thread; the sim itself is only ever
  mutated from that thread or request handlers between ticks (single
  world, single server).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .buildings import Improvement
from .db import WorldStore, serialize_world
from .simulation import Simulation


def _web_dir() -> Path:
    return Path(__file__).parent / "web"


def _chart_bytes(render) -> bytes:
    """Render a chart straight into memory (no temp files)."""
    import io

    buffer = io.BytesIO()
    render(buffer)
    return buffer.getvalue()


@dataclass
class WorldSession:
    """In-process owner of one living world (mirrors the live shell)."""

    store: WorldStore
    sim: Simulation | None = None
    world_id: str | None = None
    restore_json: str | None = None
    restore_label: str | None = None
    running: bool = False
    run_interval_ticks: int = 100
    _thread: threading.Thread | None = field(default=None, init=False)
    _stop = threading.Event()

    # -- lifecycle -------------------------------------------------------

    def load(self, world_id: str) -> None:
        from .cli import _load_sim_from_store

        self.sim = _load_sim_from_store(self.store, world_id)
        self.world_id = world_id
        self.restore_json = None

    def save(self, world_id: str | None = None) -> str:
        if self.sim is None:
            raise RuntimeError("no world loaded")
        wid = world_id or self.world_id or f"world-{self.sim.world.seed}"
        self.store.save_world_with_id(
            wid,
            self.sim.world,
            settlements=self.sim.settlements,
            trade_routes=self.sim.trade_routes,
            ruins=self.sim.ruins,
            disaster_events=self.sim.disaster_events,
            relations=self.sim.relations,
            contested=self.sim.contested,
            building_debuffs=self.sim.building_debuffs,
            event_log=self.sim.event_log,
            diplomacy=self.sim.diplomacy,
            strategy_memory=self.sim.strategy_memory,
            highway_projects=self.sim.highway_projects,
            treaties=self.sim.treaties,
            contamination_zones=self.sim.contamination_zones,
        )
        self.world_id = wid
        return wid

    def snapshot_state(self) -> str:
        return serialize_world(
            self.sim.world,
            self.sim.settlements,
            trade_routes=self.sim.trade_routes,
            ruins=self.sim.ruins,
            disaster_events=self.sim.disaster_events,
            relations=self.sim.relations,
            contested=self.sim.contested,
            building_debuffs=self.sim.building_debuffs,
            event_log=self.sim.event_log,
            diplomacy=self.sim.diplomacy,
            strategy_memory=self.sim.strategy_memory,
            highway_projects=self.sim.highway_projects,
            treaties=self.sim.treaties,
            contamination_zones=self.sim.contamination_zones,
        )

    # -- undo --------------------------------------------------------------

    def capture_undo(self, label: str) -> None:
        if self.sim is None:
            return
        self.restore_json = self.snapshot_state()
        self.restore_label = label

    def commit_mutation(self) -> None:
        if self.restore_json is not None and self.world_id:
            self.store.save_undo_point(
                self.world_id, self.restore_json, self.sim.tick,
                label=self.restore_label or "mutation",
            )
        self.restore_label = self.restore_label  # kept for session undo

    def undo(self) -> dict:
        from .clock import describe

        if self.restore_json is None:
            raise RuntimeError("nothing to undo in this session")
        label = self.restore_label or "mutation"
        tick_before = self.sim.tick if self.sim else -1
        self.sim = Simulation.from_state_json(self.restore_json)
        self.restore_json = None
        return {
            "undid": label,
            "tick_restored": self.sim.tick,
            "previous_tick": tick_before,
            "date": describe(self.sim.tick),
        }

    # -- time control ------------------------------------------------------

    def step(self, ticks: int = 1) -> dict:
        if self.sim is None:
            raise RuntimeError("no world loaded")
        for _ in range(max(0, ticks)):
            self.sim.step()
        return self.status()

    def start_run(self, interval_ticks: int = 500) -> dict:
        if self.sim is None:
            raise RuntimeError("no world loaded")
        if self.running:
            return {"running": True}
        self.running = True
        self._stop.clear()
        self.run_interval_ticks = max(1, interval_ticks)

        def loop():
            while not self._stop.is_set() and self.running:
                self.sim.step()
                if self.sim.tick % self.run_interval_ticks == 0 \
                        and self._stop.wait(0):
                    break
                time.sleep(0)  # yield; speed controlled by caller pacing

        self._thread = threading.Thread(target=loop, daemon=True,
                                        name="worldsim-run")
        self._thread.start()
        return {"running": True}

    def pause(self) -> dict:
        self.running = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        return {"running": False}

    # -- views -------------------------------------------------------------

    def status(self) -> dict:
        from .clock import describe
        from .histories import civilizations_summary

        living = sorted(
            (s for s in self.sim.settlements if s.is_alive),
            key=lambda s: s.name)
        return {
            "world_id": self.world_id,
            "tick": self.sim.tick,
            "date": describe(self.sim.tick),
            "seed": self.sim.world.seed,
            "settlements": [
                {
                    "name": s.name,
                    "population": s.population,
                    "era": s.era,
                    "army": round(s.army, 1),
                    "happiness": round(s.happiness, 3),
                    "food_stock": round(s.food_stock, 1),
                    "frozen": s.frozen,
                }
                for s in living
            ],
            "civilizations": civilizations_summary(self.sim),
            "running": self.running,
        }

    def map_png_bytes(self) -> bytes:
        """Rendered map PNG as bytes (no temp files)."""
        import io

        import matplotlib

        matplotlib.use("Agg")
        from .visualization import export_map_png

        buffer = io.BytesIO()
        export_map_png(self.sim, buffer)
        return buffer.getvalue()


# ----------------------------------------------------------------------
# Pydantic payloads
# ----------------------------------------------------------------------

class StepRequest(BaseModel):
    ticks: int = 1


class RunRequest(BaseModel):
    interval_ticks: int = 500


class GodRequest(BaseModel):
    action: str
    params: dict = {}
    confirm: bool = False


class WorldRefRequest(BaseModel):
    world_id: str


# Actions that demand explicit confirmation through the API.
CATASTROPHIC_ACTIONS = {"nuke", "smite_region"}
CONFIRM_THRESHOLD_SMITE = 25


def create_app(session: WorldSession) -> FastAPI:
    app = FastAPI(title="WorldSim", version="0.1")
    app.state.session = session

    @app.get("/api/status")
    def api_status():
        _require_world(session)
        return session.status()

    @app.get("/api/state")
    def api_state():
        _require_world(session)
        from .markets import market_prices

        sim = session.sim
        return {
            "tick": sim.tick,
            "seed": sim.world.seed,
            "prices": market_prices(sim),
            "populations": {
                s.name: s.population
                for s in sorted(
                    (x for x in sim.settlements if x.is_alive),
                    key=lambda x: x.name)
            },
        }

    @app.get("/api/map.png")
    def api_map_png():
        _require_world(session)
        from .visualization import export_map_png

        return Response(content=session.map_png_bytes(),
                        media_type="image/png")

    @app.get("/api/grid")
    def api_grid():
        """Compact tile/settlement arrays for the canvas frontend."""
        _require_world(session)
        sim = session.sim
        world = sim.world
        settlements = [
            {
                "name": s.name,
                "x": s.spawn_x,
                "y": s.spawn_y,
                "population": s.population,
            }
            for s in sorted(
                (x for x in sim.settlements if x.is_alive),
                key=lambda x: x.name)
        ]
        tick = sim.tick
        return {
            "size": world.size,
            "tick": tick,
            "terrain": world.terrain.tolist(),
            "roads": [
                [int(x), int(y)]
                for y, x in np.argwhere(
                    world.improvements == Improvement.ROAD.value)
            ],
            "ruins": [
                {"x": r.spawn_x, "y": r.spawn_y}
                for r in getattr(sim, "ruins", [])
            ],
            "zones": [
                {"cx": z.center_x, "cy": z.center_y, "radius": z.radius}
                for z in getattr(sim, "contamination_zones", [])
                if z.is_active(tick)
            ],
            "settlements": settlements,
        }

    @app.get("/api/charts/populations.png")
    def api_populations_png():
        _require_world(session)
        from .visualization import export_population_chart

        try:
            content = _chart_bytes(
                lambda buf: export_population_chart(session.sim, buf))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return Response(content=content, media_type="image/png")

    @app.get("/api/charts/events.png")
    def api_events_png():
        _require_world(session)
        if not session.sim.event_log:
            raise HTTPException(404, "no events recorded yet")
        from .visualization import export_event_histogram

        return Response(content=_chart_bytes(
            lambda buf: export_event_histogram(session.sim, buf)),
            media_type="image/png")

    from fastapi.responses import FileResponse

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(_web_dir() / "index.html")

    app.mount("/static", StaticFiles(directory=str(_web_dir())),
              name="static")

    @app.get("/api/chronicle")
    def api_chronicle(name: str | None = None):
        from .histories import (
            build_chronicle,
            civilizations_summary,
        )

        _require_world(session)
        if name:
            target = next(
                (s for s in session.sim.settlements
                 if s.name.lower() == name.lower()), None)
            if target is None:
                raise HTTPException(404, f"no settlement named {name!r}")
            return {
                "name": target.name,
                "chronicle": build_chronicle(session.sim, target),
            }
        return {"civilizations": civilizations_summary(session.sim)}

    @app.get("/api/timeline")
    def api_timeline(limit: int = 50, category: str | None = None):
        from .timeline import build_timeline, category_of, render_timeline

        _require_world(session)
        categories = {category} if category else None
        events = build_timeline(session.sim, categories=categories,
                                limit=max(0, min(limit, 1000)))
        return {
            "count": len(events),
            "rendered": render_timeline(session.sim, events,
                                        date_stamps=False).splitlines(),
        }

    @app.post("/api/step")
    def api_step(request: StepRequest):
        _require_world(session)
        return session.step(max(0, min(request.ticks, 10_000)))

    @app.post("/api/run")
    def api_run(request: RunRequest):
        _require_world(session)
        return session.start_run(request.interval_ticks)

    @app.post("/api/pause")
    def api_pause():
        if session.sim is None:
            raise HTTPException(409, "no world loaded")
        return session.pause()

    @app.post("/api/undo")
    def api_undo():
        try:
            return session.undo()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/save")
    def api_save(request: WorldRefRequest):
        try:
            return {"saved_as": session.save(request.world_id)}
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/load")
    def api_load(request: WorldRefRequest):
        try:
            session.load(request.world_id)
        except Exception as exc:
            raise HTTPException(404, str(exc))
        return {"loaded": request.world_id, "tick": session.sim.tick}

    @app.post("/api/god/{action}")
    def api_god(action: str, request: GodRequest):
        return _dispatch_god(session, action, request)

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket):
        await websocket.accept()
        if session.sim is None:
            await websocket.close(code=4000)
            return
        import asyncio

        try:
            last_tick = -1
            while True:
                tick = session.sim.tick
                if tick != last_tick:
                    await websocket.send_json({
                        "tick": tick,
                        "total_population": sum(
                            s.population for s in session.sim.settlements
                            if s.is_alive),
                        "zones": len([
                            z for z in session.sim.contamination_zones
                            if z.is_active(tick)]),
                    })
                    last_tick = tick
                await asyncio.sleep(0.05)
        except WebSocketDisconnect:
            return

    return app


def _require_world(session: WorldSession) -> None:
    if session.sim is None:
        raise HTTPException(409, "no world loaded — POST /api/load first")


def _require_ws_world(session: WorldSession) -> None:
    if session.sim is None:
        raise RuntimeError("no world loaded")


def _dispatch_god(session: WorldSession, action: str,
                  request: GodRequest) -> dict:
    """Route a god action through the SAME handlers + audit as CLI."""
    params = request.params
    sim = session.sim
    if sim is None:
        raise HTTPException(409, "no world loaded")

    def param(name, default=None, cast=None):
        value = params.get(name, default)
        if cast is not None and value is not None:
            return cast(value)
        return value

    settlement_name = param("settlement", "", str)
    target = None
    if settlement_name:
        lowered = settlement_name.lower()
        target = next(
            (s for s in sim.settlements if s.name.lower() == lowered), None)
        if target is None:
            raise HTTPException(404, f"no settlement named {settlement_name!r}")

    # Confirmation gates (API equivalents of --force/--confirm).
    if action == "smite" and (
        int(param("amount", 0)) >= CONFIRM_THRESHOLD_SMITE
        and not request.confirm
    ):
        raise HTTPException(428, "confirm required for smite >= 25")
    if action in CATASTROPHIC_ACTIONS and not request.confirm:
        raise HTTPException(428, f"confirm required for {action}")

    session.capture_undo(action)
    try:
        if action == "smite":
            before, after = sim.god_smite(target, int(param("amount", 1)))
        elif action == "bless":
            resource = param("resource", "food", str)
            amount = float(param("amount", 100.0))
            if resource == "happiness":
                before, after = sim.god_bless_happiness(target)
            else:
                before, after = sim.god_bless_resources(
                    target, resource, amount)
        elif action == "nuke":
            before, after = sim.god_nuke(int(param("x")), int(param("y")))
        elif action == "spawn_settlement":
            before, after = sim.god_spawn_settlement(
                int(param("x")), int(param("y")),
                name=param("name", None, str))
        elif action == "trigger_disaster":
            radius = param("radius", None)
            kwargs = {} if radius is None else {"radius": int(radius)}
            before, after = sim.god_trigger_disaster(
                param("disaster_type", "", str), int(param("x")),
                int(param("y")), **kwargs)
        elif action == "freeze":
            before, after = sim.god_toggle_freeze(target)
        elif action == "terraform":
            radius = param("radius", None)
            if radius is None:
                before, after = sim.god_terraform(
                    int(param("x")), int(param("y")),
                    param("terrain", "", str))
            else:
                before, after = sim.god_terraform_region(
                    int(param("x")), int(param("y")), int(radius),
                    param("terrain", "", str))
        elif action == "bless_land":
            before, after = sim.god_bless_land(
                int(param("x")), int(param("y")),
                int(param("radius", 1)), float(param("bonus", 1.0)))
        else:
            session.restore_json = session.restore_json  # no-op keep
            raise HTTPException(404, f"unknown god action {action!r}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    session.commit_mutation()
    return {"action": action, "before": before, "after": after}
