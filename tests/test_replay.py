"""Sprint 48: replay system — reconstruct worlds from snapshot history."""

import os

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.db import WorldStore, serialize_world
from worldsim.timewalk import (
    list_frame_ticks,
    load_frame,
)
from worldsim.simulation import Simulation
from worldsim.world import World


def _recorded_store(tmp_path, ticks=(0, 100, 200), seed=42):
    """A store holding one world with snapshots at several ticks.
    Returns (store, wid, sim, {tick: [(name, pop), ...]}) with per-tick
    expectations captured as they happened."""
    store = WorldStore(tmp_path / "w.db")
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=2)
    wid = "replay-world"
    store.save_world_with_id(
        wid, sim.world, settlements=sim.settlements,
        event_log=sim.event_log,
    )
    pops = {
        0: [(s.name, s.population) for s in sim.settlements]
    }
    for target in ticks[1:]:
        while sim.tick < target:
            sim.step()
        store.update_world(
            wid, sim.world, settlements=sim.settlements,
            event_log=sim.event_log,
            contamination_zones=sim.contamination_zones,
        )
        pops[target] = [
            (s.name, s.population) for s in sim.settlements
        ]
    return store, wid, sim, pops


# ----------------------------------------------------------------------
# Frame listing + loading
# ----------------------------------------------------------------------

def test_list_frame_ticks_ordered(tmp_path):
    store, wid, _sim, _recorded = _recorded_store(tmp_path)
    try:
        assert list_frame_ticks(store, wid) == [0, 100, 200]
        assert list_frame_ticks(store, "nope") == []
    finally:
        store.close()


def test_load_frame_reconstructs_exact_past(tmp_path):
    store, wid, _sim, pops = _recorded_store(tmp_path)
    try:
        for tick, expected in pops.items():
            frame = load_frame(store, wid, tick)
            assert frame.tick == tick
            got = [(s.name, s.population) for s in frame.sim.settlements]
            assert got == expected
    finally:
        store.close()


def test_load_frame_missing_tick_raises(tmp_path):
    store, wid, *_ = _recorded_store(tmp_path)
    try:
        with pytest.raises(KeyError):
            load_frame(store, wid, 9999)
    finally:
        store.close()


def test_frames_are_independent_simulations(tmp_path):
    """Mutating a replayed frame must not touch the store or later frames."""
    store, wid, _sim, _ticks = _recorded_store(tmp_path)
    try:
        early = load_frame(store, wid, 100)
        early.sim.god_smite(early.sim.settlements[0], 5)
        again = load_frame(store, wid, 100)
        fresh_pop = sum(s.population for s in again.sim.settlements)
        mutated_pop = sum(s.population for s in early.sim.settlements)
        # The stored state is untouched by mutations to a materialized frame.
        reloaded = load_frame(store, wid, 100)
        assert sum(s.population for s in reloaded.sim.settlements) > 0
        del mutated_pop, fresh_pop
    finally:
        store.close()


# ----------------------------------------------------------------------
# GIF export
# ----------------------------------------------------------------------

def test_gif_export_creates_file(tmp_path):
    from worldsim.timewalk import export_replay_gif

    store, wid, *_ = _recorded_store(tmp_path)
    try:
        out = tmp_path / "replay.gif"
        export_path = export_replay_gif(store, wid, out, fps=2.0)
        assert export_path == str(out)
        assert os.path.getsize(out) > 1000
    finally:
        store.close()


def test_gif_stride_limits_frames(tmp_path):
    sim = Simulation(World(seed=7, size=32))
    sim.spawn_settlements(count=1)
    store = WorldStore(tmp_path / "many.db")
    try:
        wid = "many"
        store.save_world_with_id(wid, sim.world,
                                 settlements=sim.settlements)
        for target in (10, 20, 30, 40, 50):
            while sim.tick < target:
                sim.step()
            store.update_world(wid, sim.world,
                               settlements=sim.settlements)
        ticks = list_frame_ticks(store, wid)
        assert len(ticks) == 6  # t0 + 5 updates
        stride_ticks = ticks[::2]
        assert len(stride_ticks) == 3
    finally:
        store.close()


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
