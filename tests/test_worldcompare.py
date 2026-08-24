"""Sprint 49: world comparison — structural diff between two worlds."""

import os

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.simulation import Simulation
from worldsim.worldcompare import (
    compare_worlds,
    export_compare_chart,
    render_compare_markdown,
)
from worldsim.world import World


def _sim(n=2, seed=42, steps=10) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    for _ in range(steps):
        sim.step()
    return sim


# ----------------------------------------------------------------------
# Identical worlds
# ----------------------------------------------------------------------

def test_same_seed_identical_histories_are_identical():
    a = _sim(n=3, seed=99, steps=100)
    b = _sim(n=3, seed=99, steps=100)
    comparison = compare_worlds(a, b)
    assert comparison["identical"] is True
    assert comparison["events"]["only_in_a_count"] == 0
    assert comparison["settlements"]["changed"] == []


def test_different_ticks_show_event_divergence():
    a = _sim(n=2, seed=42, steps=50)
    b = _sim(n=2, seed=42, steps=50)
    # Only B continues: extra ticks + an audited intervention.
    for _ in range(30):
        b.step()
    b.god_bless_resources(b.settlements[0], "wood", 10.0)
    comparison = compare_worlds(a, b)
    assert comparison["meta"]["tick_a"] != comparison["meta"]["tick_b"]
    assert not comparison["identical"]
    assert comparison["events"]["only_in_b_count"] >= 1


# ----------------------------------------------------------------------
# Branch divergence (the S43 use case)
# ----------------------------------------------------------------------

def test_branch_divergence_detected_per_field():
    a = _sim(n=2, seed=42)
    b = Simulation.from_state_json(_state(a))
    victim_a = a.settlements[0]
    victim_b = next(s for s in b.settlements if s.name == victim_a.name)
    a.god_smite(victim_a, 5)
    b.god_bless_resources(victim_b, "wood", 500.0)
    comparison = compare_worlds(a, b)
    changed = {c["name"]: c["fields"] for c in comparison["settlements"]["changed"]}
    assert victim_a.name in changed
    fields = set(changed[victim_a.name])
    assert "population" in fields  # smited in A only
    assert "resources" in fields   # wood blessed in B only
    assert comparison["events"]["only_in_a_count"] >= 1
    assert comparison["events"]["only_in_b_count"] >= 1


def _state(sim) -> str:
    from worldsim.cli import _undo_state_json

    return _undo_state_json(sim, serialize_world)


def serialize_world(*args, **kwargs):
    from worldsim.db import serialize_world as sw

    return sw(*args, **kwargs)


# ----------------------------------------------------------------------
# Rendering + chart
# ----------------------------------------------------------------------

def test_markdown_renders_sections():
    a = _sim(n=2)
    b = _sim(n=2, seed=42, steps=20)
    md = render_compare_markdown(compare_worlds(a, b))
    assert "# World Comparison" in md
    assert "| Counter | A | B |" in md
    assert "Identical: **False**" in md


def test_markdown_identical_branch():
    a = _sim(n=2)
    md = render_compare_markdown(compare_worlds(a, _clone(a)))
    assert "Identical: **True**" in md


def _clone(sim) -> Simulation:
    return Simulation.from_state_json(_state(sim))


def test_compare_chart_created_and_deterministic(tmp_path):
    a = _sim(n=2)
    b = _sim(n=2)
    out1, out2 = tmp_path / "a.png", tmp_path / "b.png"
    export_compare_chart(a, b, out1)
    export_compare_chart(a, b, out2)
    assert os.path.getsize(out1) > 1000
    assert out1.read_bytes() == out2.read_bytes()


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
