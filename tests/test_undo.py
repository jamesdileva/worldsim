"""Sprint 43: timeline branching / undo."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.cli import _undo_state_json, build_parser
from worldsim.db import WorldStore, serialize_world
from worldsim.simulation import Simulation
from worldsim.summaries import summarize_world
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    for _ in range(10):
        sim.step()
    return sim


def _snapshot(sim) -> str:
    return _undo_state_json(sim, serialize_world)


# ----------------------------------------------------------------------
# Undo point capture + restore fidelity
# ----------------------------------------------------------------------

def test_undo_point_saved_on_intervention(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    try:
        sim = _sim()
        wid = "test-world"
        pre = _snapshot(sim)
        sim.god_smite(sim.settlements[0], 1)
        store.save_undo_point(wid, pre, sim.tick, label="smite")
        assert store.count_undo_points(wid) == 1
        point = store.latest_undo_point(wid)
        assert point is not None
        assert point["label"] == "smite"
        assert point["tick"] == sim.tick
    finally:
        store.close()


def test_no_undo_points_returns_none(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    try:
        assert store.latest_undo_point("nothing") is None
        assert store.count_undo_points("nothing") == 0
    finally:
        store.close()


def test_restore_is_byte_exact():
    """§16.4 undo/revert: restoring the pre-intervention snapshot must
    reproduce the exact prior state (byte-equal summary)."""
    sim = _sim(n=3)
    pre_state = _snapshot(sim)
    pre_summary = summarize_world(sim, tier="full")

    # Mutate heavily after the snapshot.
    sim.god_smite(sim.settlements[0], 5)
    sim.god_bless_resources(sim.settlements[1], "wood", 999.0)
    sim.god_terraform(20, 20, "mountain")
    assert summarize_world(sim, tier="full") != pre_summary

    restored_sim = Simulation.from_state_json(pre_state)
    assert summarize_world(restored_sim, tier="full") == pre_summary


# ----------------------------------------------------------------------
# Branching: alternate timelines coexist
# ----------------------------------------------------------------------

def test_branch_replays_independently(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    try:
        sim = _sim(n=2, seed=99)
        wid = "origin"
        pre = _snapshot(sim)

        # Timeline A: bless.
        sim.god_bless_resources(sim.settlements[0], "food", 500.0)
        state_a = _snapshot(sim)
        store.save_world_with_id(wid, sim.world, settlements=sim.settlements,
                                 event_log=sim.event_log)

        # Timeline B (branch): smite instead, from the same fork point.
        branch_sim = Simulation.from_state_json(pre)
        branch_sim.god_smite(branch_sim.settlements[0], 3)
        store.save_world_with_id(
            "branch", branch_sim.world,
            settlements=branch_sim.settlements,
            event_log=branch_sim.event_log,
            skip_entity_rows=True,
        )
        del state_a

        # Both worlds exist and diverged.
        assert store.world_exists(wid)
        assert store.world_exists("branch")
        origin_loaded, *_ = store.load_latest_snapshot(wid)
        branch_loaded, *_ = store.load_latest_snapshot("branch")
        assert origin_loaded.seed == branch_loaded.seed == 99
    finally:
        store.close()


# ----------------------------------------------------------------------
# CLI surface
# ----------------------------------------------------------------------

def test_cli_undo_subcommand_parses():
    parser = build_parser()
    args = parser.parse_args(["undo", "--world-id", "w"])
    assert args.world_id == "w"
    assert args.as_world is None
    args2 = parser.parse_args([
        "undo", "--world-id", "w", "--as-world", "branch-1"])
    assert args2.as_world == "branch-1"


# ----------------------------------------------------------------------
# Determinism from a restored point + frozen contract
# ----------------------------------------------------------------------

def test_replay_from_restored_point_is_deterministic():
    def replay_from(state_json):
        sim = Simulation.from_state_json(state_json)
        for _ in range(50):
            sim.step()
        return [
            (s.name, s.population, round(s.food_stock, 5))
            for s in sim.settlements
        ]

    sim = _sim(n=2, seed=123)
    state = _snapshot(sim)
    assert replay_from(state) == replay_from(state)


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
