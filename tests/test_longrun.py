"""Sprint 50: long-running autonomous world — the roadmap finish line."""

import os

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.cli import _write_report_bundle, build_parser, main
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(n=3, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Report bundle
# ----------------------------------------------------------------------

def test_report_bundle_contains_all_artifacts(tmp_path):
    sim = _sim(n=2)
    for _ in range(600):  # at least one epoch (500-tick interval)
        sim.step()
    out = tmp_path / "bundle"
    paths = _write_report_bundle(sim, str(out))
    names = {os.path.basename(p) for p in paths}
    assert {
        "world_map.png", "population_curves.png",
        "event_histogram.png", "chronicle.md",
    } <= names
    for path in paths:
        assert os.path.getsize(path) > 100
    chronicle = (out / "chronicle.md").read_text(encoding="utf-8")
    assert "# World chronicle" in chronicle
    assert "founded" in chronicle  # sagas present


def test_report_bundle_without_epochs_still_works(tmp_path):
    """A run shorter than one epoch skips curves but keeps everything else."""
    sim = _sim(n=1)
    for _ in range(20):
        sim.step()
    out = tmp_path / "short"
    paths = _write_report_bundle(sim, str(out))
    names = {os.path.basename(p) for p in paths}
    assert "world_map.png" in names and "chronicle.md" in names
    assert "population_curves.png" not in names


# ----------------------------------------------------------------------
# CLI wiring + determinism of the autonomous run
# ----------------------------------------------------------------------

def test_simulate_parser_has_report_dir():
    args = build_parser().parse_args([
        "simulate", "--seed", "1", "--ticks", "10",
        "--report-dir", "out",
    ])
    assert args.report_dir == "out"


def test_autonomous_run_end_to_end(tmp_path):
    db = tmp_path / "w.db"
    report_dir = tmp_path / "report"
    code = main([
        "simulate", "--seed", "7", "--size", "32", "--ticks", "600",
        "--settlements", "2", "--no-save", "--report-interval", "300",
        "--report-dir", str(report_dir),
    ])
    assert code == 0
    assert (report_dir / "chronicle.md").exists()
    assert (report_dir / "world_map.png").exists()


def test_same_seed_produces_identical_chronicle_bytes(tmp_path):
    chronicles = []
    for i in range(2):
        report_dir = tmp_path / f"run{i}"
        sim = _sim(n=3, seed=123)
        for _ in range(1000):
            sim.step()
        paths = _write_report_bundle(sim, str(report_dir))
        chronicles.append(
            next(p for p in paths if p.endswith("chronicle.md")))
    a = open(chronicles[0], encoding="utf-8").read()
    b = open(chronicles[1], encoding="utf-8").read()
    assert a == b


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
