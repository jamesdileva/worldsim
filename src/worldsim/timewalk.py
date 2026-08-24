"""Replay system (Sprint 48): reconstruct past worlds from snapshots.

Every save writes a full state_json keyed by (tick, world_id), so any
recorded run is replayable frame-by-frame.

Contract:
- Frames materialize lazily from the store (a long run can hold
  thousands of snapshots; only requested ones deserialize).
- Deterministic: loading tick T always yields the identical world that
  was saved at tick T.
- Named `timewalk` because `replay.py` already hosts the RL ReplayBuffer
  (Sprint 13) — see AGENTS.md naming lesson.
"""

from __future__ import annotations

from dataclasses import dataclass

from .simulation import Simulation


@dataclass
class ReplayFrame:
    tick: int
    sim: Simulation


def list_frame_ticks(store, world_id: str) -> list[int]:
    """All recorded ticks for a world, ascending."""
    rows = store._conn.execute(
        "SELECT tick FROM snapshots WHERE world_id = ? ORDER BY tick ASC",
        (world_id,),
    ).fetchall()
    return [int(r[0]) for r in rows]


def load_frame(store, world_id: str, tick: int) -> ReplayFrame:
    """Deserialize the world exactly as it was at `tick`."""
    row = store._conn.execute(
        "SELECT state_json FROM snapshots WHERE world_id = ? AND tick = ?",
        (world_id, int(tick)),
    ).fetchone()
    if row is None:
        raise KeyError(
            f"no snapshot for world {world_id} at tick {tick}"
        )
    return ReplayFrame(tick=int(tick), sim=Simulation.from_state_json(row[0]))


def iter_frames(store, world_id: str, stride: int = 1):
    """Yield ReplayFrames for every stride-th recorded tick, ascending.
    Lazy: each frame deserializes only when reached."""
    ticks = list_frame_ticks(store, world_id)
    for tick in ticks[:: max(1, stride)]:
        yield load_frame(store, world_id, tick)


def export_replay_gif(
    store, world_id: str, path, fps: float = 4.0, stride: int = 1,
    max_frames: int = 400,
) -> str:
    """Animated GIF of the world evolving through its recorded history."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.animation import FuncAnimation, PillowWriter

    from .visualization import TERRAIN_COLORS

    ticks = list_frame_ticks(store, world_id)[:: max(1, stride)]
    if len(ticks) > max_frames:
        # Thin further so long runs stay bounded.
        step = -(-len(ticks) // max_frames)
        ticks = ticks[::step]

    def render(sim):
        world = sim.world
        size = world.size
        colors = np.zeros((size, size, 3), dtype=float)
        for value, rgb in TERRAIN_COLORS.items():
            colors[world.terrain == value] = rgb
        return colors

    first = load_frame(store, world_id, ticks[0])
    base = render(first.sim)
    fig, ax = plt.subplots(figsize=(4.0, 4.0), dpi=80)
    image = ax.imshow(base, interpolation="nearest")
    title = ax.set_title(f"tick {ticks[0]}", fontsize=7)
    ax.set_xticks([])
    ax.set_yticks([])

    def update(i):
        tick = ticks[i]
        sim = load_frame(store, world_id, tick).sim
        image.set_data(render(sim))
        living = sum(1 for s in sim.settlements if s.is_alive)
        pop = sum(s.population for s in sim.settlements if s.is_alive)
        title.set_text(f"tick {tick} | {living} alive | pop {pop}")
        return [image, title]

    anim = FuncAnimation(fig, update, frames=len(ticks), interval=1000 / fps)
    anim.save(path, writer=PillowWriter(fps=fps))
    plt.close(fig)
    return str(path)
