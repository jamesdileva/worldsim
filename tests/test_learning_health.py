"""Sprint 47: offline learning-health dashboard."""

import json

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.cli import build_parser
from worldsim.db import WorldStore
from worldsim.training import (
    APPROX_KL_WARN,
    ENTROPY_COLLAPSE_THRESHOLD,
    EXPLAINED_VARIANCE_GOOD,
    EXPLAINED_VARIANCE_OK,
    build_learning_dashboard,
    generate_health_dashboard_plot,
    render_health_dashboard_markdown,
)


def _store_with_summaries(tmp_path, specs):
    """specs: list of (gen, summary_dict) — checkpoints + sibling jsons."""
    store = WorldStore(tmp_path / "w.db")
    for i, (gen, summary) in enumerate(specs):
        ckpt = tmp_path / f"{gen}.zip"
        ckpt.write_bytes(b"fake")
        store.insert_policy_checkpoint({
            "generation": gen,
            "path": str(ckpt),
            "algorithm": "PPO",
            "total_timesteps": 10_000 * (i + 1),
            "episodes": 10,
            "mean_episode_return": summary.get("mean_return"),
            "wall_time_seconds": 60.0,
            "checksum": f"cs{i}",
            "size_bytes": 4,
        })
        ckpt.with_name(f"{gen}_summary.json").write_text(
            json.dumps(summary), encoding="utf-8")
    return store


def _summary(mean_return=1.0, final_entropy=0.5, ev=0.8, kl=0.01,
             tps=100.0):
    return {
        "episodes": 10,
        "mean_return": mean_return,
        "final_entropy": final_entropy,
        "mean_explained_variance": ev,
        "mean_approx_kl": kl,
        "ticks_per_second": tps,
    }


# ----------------------------------------------------------------------
# Health flags
# ----------------------------------------------------------------------

def test_healthy_generation(tmp_path):
    store = _store_with_summaries(tmp_path, [
        ("gen1", _summary()),
    ])
    try:
        dash = build_learning_dashboard(store, ["gen1"])
    finally:
        store.close()
    gen = dash["generations"][0]
    assert gen["found"] and gen["status"] == "healthy"
    assert gen["entropy_status"] == "healthy"
    assert gen["ev_band"] == "good"
    assert gen["flags"] == []
    assert dash["overall_status"] == "healthy"


def test_entropy_collapse_flagged(tmp_path):
    store = _store_with_summaries(tmp_path, [
        ("gen1", _summary(final_entropy=ENTROPY_COLLAPSE_THRESHOLD / 2)),
    ])
    try:
        dash = build_learning_dashboard(store, ["gen1"])
    finally:
        store.close()
    gen = dash["generations"][0]
    assert "entropy_collapsed" in gen["flags"]
    assert gen["entropy_status"] == "collapsed"
    assert dash["overall_status"] == "watch"


def test_ev_bands():
    from worldsim.training import (
        _explained_variance_band as band,
        _entropy_status as ent,
    )

    assert band(EXPLAINED_VARIANCE_GOOD)[0] == "good"
    assert band((EXPLAINED_VARIANCE_GOOD + EXPLAINED_VARIANCE_OK) / 2)[0] == "ok"
    assert band(0.1)[0] == "poor"
    assert band(None)[0] == "unknown"
    assert ent(None)[0] == "unknown"


def test_high_kl_warns(tmp_path):
    store = _store_with_summaries(tmp_path, [
        ("gen1", _summary(kl=APPROX_KL_WARN * 2)),
    ])
    try:
        dash = build_learning_dashboard(store, ["gen1"])
    finally:
        store.close()
    assert "kl_high" in dash["generations"][0]["flags"]
    assert dash["overall_status"] == "watch"


def test_return_regression_between_generations(tmp_path):
    store = _store_with_summaries(tmp_path, [
        ("gen1", _summary(mean_return=10.0)),
        ("gen2", _summary(mean_return=5.0)),   # regression
        ("gen3", _summary(mean_return=12.0)),  # recovers
    ])
    try:
        dash = build_learning_dashboard(store, ["gen1", "gen2", "gen3"])
    finally:
        store.close()
    gens = dash["generations"]
    assert gens[0]["return_trend"] == "n/a"
    assert gens[1]["return_trend"] == "down"
    assert "return_regressed" in gens[1]["flags"]
    assert gens[1]["status"] == "regressed"
    assert gens[2]["return_trend"] == "up"
    # Overall stays regressed even after recovery.
    assert dash["overall_status"] == "regressed"


def test_missing_generation_reported(tmp_path):
    store = WorldStore(tmp_path / "empty.db")
    try:
        dash = build_learning_dashboard(store, ["ghost"])
    finally:
        store.close()
    assert dash["generations"][0]["status"] == "missing"
    assert dash["overall_status"] == "watch"


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

def test_markdown_renders_table(tmp_path):
    store = _store_with_summaries(tmp_path, [
        ("gen1", _summary()), ("gen2", _summary(mean_return=2.0)),
    ])
    try:
        dashboard = build_learning_dashboard(store, ["gen1", "gen2"])
    finally:
        store.close()
    md = render_health_dashboard_markdown(dashboard)
    assert "| Generation | Return | Trend | Entropy | EV band | KL | Flags |" in md
    assert "Overall status: **" in md
    assert "| ghost | — | — | — | — | — | missing |" not in md


def test_health_plot_created(tmp_path):
    store = _store_with_summaries(tmp_path, [("gen1", _summary())])
    try:
        dash = build_learning_dashboard(store, ["gen1"])
    finally:
        store.close()
    out = tmp_path / "health.png"
    generate_health_dashboard_plot(dash, out)
    assert out.stat().st_size > 1000


# ----------------------------------------------------------------------
# CLI + contract
# ----------------------------------------------------------------------

def test_cli_rl_health_parses():
    parser = build_parser()
    args = parser.parse_args([
        "rl", "health", "--gens", "gen1,gen2",
        "--png", "h.png", "--markdown", "h.md",
    ])
    assert args.gens == "gen1,gen2"
    assert args.png == "h.png" and args.markdown == "h.md"


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
