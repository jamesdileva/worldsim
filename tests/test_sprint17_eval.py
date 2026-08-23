"""Sprint 17: difficulty knobs, statistical significance, comparison reports."""

import json

import numpy as np
import pytest

from worldsim.rewards import compute_reward_components, total_of
from worldsim.world import World
from worldsim.training import (
    evaluate_vs_baseline,
    generate_report,
    paired_permutation_pvalue,
)


# ----------------------------------------------------------------------
# Difficulty knobs
# ----------------------------------------------------------------------

def test_gather_mult_reduces_passive_income():
    from worldsim.buildings import Improvement
    from worldsim.simulation import Simulation
    from worldsim.tiles import TerrainType
    from worldsim.world import World

    def wood_after(mult):
        sim = Simulation(World(seed=5), gather_mult=mult)
        (s,) = sim.spawn_settlements(1)
        s.food_stock = 400.0  # avoid famine branch interference
        s.resource_inventory["wood"] = 0.0
        # Grant ownership of every forest tile so gathering has a source.
        forest = np.argwhere(
            sim.world.terrain == TerrainType.FOREST.value
        )
        assert len(forest) > 0
        for y, x in forest:
            sim.world.ownership[y, x] = 0
        sim._invalidate_cache()
        sim.step()
        return s.resource_inventory.get("wood", 0.0)

    full = wood_after(1.0)
    half = wood_after(0.5)
    assert 0.0 < half < full


def test_disaster_multiplier_increases_event_frequency():
    from worldsim.simulation import Simulation
    from worldsim.disasters import DisasterEvent

    def count_events(mult):
        sim = Simulation(World(seed=50000), disaster_chance_mult=mult)
        sim.spawn_settlements(2)
        for _ in range(2000):
            sim.step()
        return len(sim.disaster_events)

    normal = count_events(1.0)
    hard = count_events(3.0)
    assert hard >= normal


# ----------------------------------------------------------------------
# Statistical significance
# ----------------------------------------------------------------------

def test_permutation_test_no_variance_returns_none():
    a = [100, 100, 100]
    b = [100, 100, 100]
    assert paired_permutation_pvalue(a, b) is None


def test_permutation_test_identical_arrays_no_variance():
    # Identical paired samples have zero differences -> no signal to test.
    a = [10.0, 20.0, 30.0]
    assert paired_permutation_pvalue(a, a) is None


def test_permutation_test_separated_arrays_low_p():
    # With 6 pairs the minimum achievable two-sided permutation p is
    # 2/2^6 = 0.03125 < 0.05.
    a = [3000, 2950, 2900, 3050, 2980, 3020]
    b = [1200, 1150, 1100, 1250, 1180, 1220]
    p = paired_permutation_pvalue(a, b)
    assert p is not None and p < 0.05


# ----------------------------------------------------------------------
# Evaluation with extended metrics & significance
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def trained_policy(tmp_path_factory):
    """Module-scoped tiny trained policy shared by evaluation tests."""
    from stable_baselines3 import PPO

    from worldsim.training import train

    out = tmp_path_factory.mktemp("pol") / "policy_shared"
    summary = train(
        total_timesteps=512, seed=4,
        size=32, num_settlements=2, max_ticks=150,
        save_path=out, log_path=tmp_path_factory.mktemp("log") / "l.jsonl",
        n_steps=64,
    )
    model = PPO.load(summary["checkpoint_path"], device="cpu")
    return summary, model


def test_evaluation_extended_metrics_and_significance(trained_policy):
    _, model = trained_policy
    results = evaluate_vs_baseline(
        model_path=_model_path_for(trained_policy),
        num_worlds=2, first_seed=9000, ticks=150, size=32,
        num_settlements=2,
    )
    assert "metrics" in results
    for name in ("survival_ticks", "peak_population", "territory",
                 "buildings", "routes_established", "cumulative_reward"):
        m = results["metrics"][name]
        assert {"baseline_mean", "policy_mean", "delta",
                "wilcoxon_p"} <= set(m)
        assert m["wilcoxon_p"] is None or 0.0 <= m["wilcoxon_p"] <= 1.0
    assert results["difficulty"] == {
        "disaster_chance_mult": 1.0, "gather_mult": 1.0,
    }
    per_world = results["results"][0]
    assert all(k in per_world for k in (
        "baseline_territory", "policy_territory",
        "baseline_buildings", "policy_buildings",
        "baseline_reward", "policy_reward",
    ))


def _model_path_for(trained_policy):
    summary, _ = trained_policy
    return summary["checkpoint_path"]


def test_evaluation_difficulty_threaded(trained_policy):
    _, model = trained_policy
    normal = evaluate_vs_baseline(
        model_path=_model_path_for(trained_policy),
        num_worlds=1, first_seed=9500, ticks=150, size=32,
        num_settlements=2,
    )
    hard = evaluate_vs_baseline(
        model_path=_model_path_for(trained_policy),
        num_worlds=1, first_seed=9500, ticks=150, size=32,
        num_settlements=2,
        disaster_mult=2.0, gather_mult=0.5,
    )
    assert normal["difficulty"]["gather_mult"] == 1.0
    assert hard["difficulty"]["gather_mult"] == 0.5


# ----------------------------------------------------------------------
# Report generation
# ----------------------------------------------------------------------

def test_report_generation_markdown_and_chart(trained_policy, tmp_path):
    results = evaluate_vs_baseline(
        model_path=_model_path_for(trained_policy),
        num_worlds=2, first_seed=9600, ticks=150, size=32,
        num_settlements=2,
    )
    md_path = tmp_path / "report.md"
    png_path = tmp_path / "chart.png"
    written_md = generate_report(results, md_path, png_path)
    assert written_md == md_path
    text = md_path.read_text(encoding="utf-8")
    assert "# Policy vs Rule-Based Baseline" in text
    assert "| Metric | Baseline | Policy | Delta | Wilcoxon p |" in text
    assert png_path.exists() and png_path.stat().st_size > 0


def test_report_json_serializable_results(trained_policy):
    results = evaluate_vs_baseline(
        model_path=_model_path_for(trained_policy),
        num_worlds=1, first_seed=9700, ticks=120, size=32,
        num_settlements=2,
    )
    payload = json.dumps(results)  # must not raise (SQLite storage path)
    assert '"cumulative_reward"' in payload
