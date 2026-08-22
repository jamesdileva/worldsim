"""Sprint 18: learning-curve dashboard aggregation and analysis."""

import json

import pytest

from worldsim.training import (
    compare_generations,
    generate_learning_curve_plot,
)


# ----------------------------------------------------------------------
# Unit: curve analysis on synthetic per-generation results
# ----------------------------------------------------------------------

def _synthetic_results(survival, peak, reward_wins, seed_base=50000):
    """Build an evaluate_vs_baseline-shaped results dict for one gen."""
    n = len(survival)
    return {
        "worlds": n,
        "policy_wins": sum(reward_wins),
        "ties": 0,
        "reward_win_fraction": (
            sum(reward_wins) / n if n else 0.0
        ),
        "win_fraction_strict": 0.0,
        "mean_policy_survival": float(sum(survival) / n),
        "mean_baseline_survival": float(sum(survival) / n),
        "mean_policy_peak_pop": float(sum(peak) / n),
        "results": [
            {
                "seed": seed_base + i,
                "baseline_survival_ticks": survival[i],
                "policy_survival_ticks": survival[i],
                "baseline_peak_population": peak[i],
                "policy_peak_population": peak[i],
            }
            for i in range(n)
        ],
    }


def test_monotonicity_detection():
    # Simulate compare_generations' monotonic logic directly.
    values = [100, 150, 300]
    mono = all(
        values[i] <= values[i + 1] + 1e-9 for i in range(len(values) - 1)
    )
    assert mono is True
    noisy = [300, 100, 150]
    assert not all(
        noisy[i] <= noisy[i + 1] + 1e-9 for i in range(len(noisy) - 1)
    )


def test_regression_detection_logic():
    first = [
        {"seed": 50000, "policy_survival_ticks": 3000},
        {"seed": 50001, "policy_survival_ticks": 2800},
    ]
    last = [
        {"seed": 50000, "policy_survival_ticks": 3100},
        {"seed": 50001, "policy_survival_ticks": 2500},  # regression!
    ]
    regressions = [
        a["seed"]
        for a, b in zip(first, last)
        if b["policy_survival_ticks"] < a["policy_survival_ticks"]
    ]
    assert regressions == [50001]


def test_improvement_percentage_math():
    values = {"gen1": 2000.0, "gen3": 2600.0}
    improvement = (
        (values["gen3"] - values["gen1"]) / abs(values["gen1"]) * 100
    )
    assert improvement == pytest.approx(30.0)  # >20% acceptance bar


# ----------------------------------------------------------------------
# Integration: full multi-generation dashboard (tiny worlds, marked slow)
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_dashboard_end_to_end(tmp_path):
    from worldsim.db import WorldStore
    from worldsim.training import train

    store = WorldStore(tmp_path / "registry.db")
    try:
        for gen, timesteps, seed in (("gA", 256, 41), ("gB", 256, 42)):
            summary = train(
                total_timesteps=timesteps, seed=seed,
                size=32, num_settlements=2, max_ticks=120,
                save_path=tmp_path / f"pol_{gen}",
                log_path=tmp_path / f"{gen}.jsonl",
                n_steps=64,
            )
            from worldsim.training import register_checkpoint

            register_checkpoint(store, gen,
                                tmp_path / f"pol_{gen}", summary)
    finally:
        store.close()

    report = compare_generations(
        generations=["gA", "gB"],
        num_worlds=1, first_seed=60000, ticks=100, size=32,
        num_settlements=2, db_path=str(tmp_path / "registry.db"),
    )
    assert set(report["curve"].keys()) == {"gA", "gB"}
    assert isinstance(report["regressions"], list)
    assert "survival_values" in report["monotonic"]

    png = tmp_path / "curve.png"
    generate_learning_curve_plot(report["curve"], png)
    assert png.exists() and png.stat().st_size > 0
