"""Sprint 14: PPO training pipeline tests.

Training smoke tests are inherently slow (SB3 + torch import and gradient
steps) — marked `slow` so the default suite stays fast.
"""

import json
from pathlib import Path

import pytest

import numpy as np
import pytest


@pytest.fixture()
def small_env_kwargs():
    return dict(size=32, num_settlements=2, max_ticks=200)


def test_training_smoke_trains_and_saves(tmp_path, small_env_kwargs):
    from stable_baselines3 import PPO

    from worldsim.training import train

    summary = train(
        total_timesteps=1024,
        seed=7,
        save_path=tmp_path / "policy_test",
        log_path=tmp_path / "train_log.jsonl",
        n_steps=128,
        **small_env_kwargs,
    )
    assert summary["total_timesteps"] == 1024
    assert Path(summary["checkpoint_path"]).exists()
    # Episode metrics were captured (at least one episode finished in 1024
    # ticks at 200 ticks/episode).
    assert summary["episodes"] >= 1
    assert summary["mean_return"] is not None
    # JSONL log written.
    lines = (tmp_path / "train_log.jsonl").read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[0])
    assert {"episode", "timesteps", "return", "length"} <= set(record)


def test_checkpoint_loads_and_predicts(tmp_path, small_env_kwargs):
    from stable_baselines3 import PPO

    from worldsim.training import train

    summary = train(
        total_timesteps=512,
        seed=8,
        save_path=tmp_path / "policy_rt",
        log_path=tmp_path / "log2.jsonl",
        n_steps=128,
        **small_env_kwargs,
    )
    model = PPO.load(summary["checkpoint_path"], device="cpu")
    obs = np.zeros(60, dtype=np.float32)
    action, _ = model.predict(obs, deterministic=True)
    assert isinstance(int(action), int)
    assert 0 <= int(action) < 62


def test_summary_json_written(tmp_path, small_env_kwargs):
    from worldsim.training import train

    train(total_timesteps=256, seed=9,
          save_path=tmp_path / "p", log_path=tmp_path / "l.jsonl",
          n_steps=64, **small_env_kwargs)
    summary_file = tmp_path / "p_summary.json"
    assert summary_file.exists()
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert data["total_timesteps"] == 256


def test_policy_checkpoints_table_round_trip():
    from worldsim.db import WorldStore

    store = WorldStore(":memory:")
    try:
        cols = {
            r[1]
            for r in store._conn.execute(
                "PRAGMA table_info(policy_checkpoints)"
            ).fetchall()
        }
        assert {"generation", "path", "algorithm", "total_timesteps",
                "episodes", "mean_episode_return", "wall_time_seconds",
                "created_at"} <= cols
        row_id = store.insert_policy_checkpoint({
            "generation": "gen1",
            "path": "data/world_sim/policies/policy_gen1.zip",
            "total_timesteps": 50_000,
            "episodes": 120,
            "mean_episode_return": 1.42,
            "wall_time_seconds": 3600.0,
        })
        rows = store._conn.execute(
            "SELECT generation, path FROM policy_checkpoints WHERE id = ?",
            (row_id,),
        ).fetchall()
        assert rows[0] == ("gen1",
                          "data/world_sim/policies/policy_gen1.zip")
    finally:
        store.close()


@pytest.mark.slow
def test_evaluation_paired_structure(tmp_path, small_env_kwargs):
    """Full paired evaluation on tiny worlds — verifies the A/B harness."""
    from stable_baselines3 import PPO

    from worldsim.training import evaluate_vs_baseline, train

    summary = train(
        total_timesteps=512, seed=5,
        save_path=tmp_path / "policy_eval",
        log_path=tmp_path / "le.jsonl",
        n_steps=128,
        **small_env_kwargs,
    )
    results = evaluate_vs_baseline(
        model_path=summary["checkpoint_path"],
        num_worlds=2,
        first_seed=8000,
        ticks=300,
        size=32,
        num_settlements=2,
    )
    assert results["worlds"] == 2
    for r in results["results"]:
        assert r["baseline_survival_ticks"] >= 0
        assert r["policy_survival_ticks"] >= 0
    assert 0.0 <= results["win_fraction_strict"] <= 1.0


# ----------------------------------------------------------------------
# Sprint 15: parallel training
# ----------------------------------------------------------------------

def test_parallel_training_smoke(tmp_path):
    """4 parallel SubprocVecEnv workers train without inter-process
    crashes and checkpoint correctly (acceptance criteria)."""
    from stable_baselines3 import PPO

    from worldsim.training import train

    summary = train(
        total_timesteps=1024,
        seed=3,
        size=32,
        num_settlements=2,
        max_ticks=200,
        save_path=tmp_path / "policy_par",
        log_path=tmp_path / "par.jsonl",
        n_steps=128,
        n_envs=4,
    )
    assert summary["n_envs"] == 4
    model = PPO.load(summary["checkpoint_path"], device="cpu")
    obs = np.zeros(60, dtype=np.float32)
    action, _ = model.predict(obs, deterministic=True)
    assert 0 <= int(action) < 62


def test_parallel_matches_sequential_step_count(tmp_path):
    """Equal timesteps config: parallel run processes exactly the requested
    total steps across all envs."""
    from worldsim.training import train

    summary = train(
        total_timesteps=800,
        seed=11,
        size=32,
        num_settlements=2,
        max_ticks=200,
        save_path=tmp_path / "p_cnt",
        log_path=tmp_path / "cnt.jsonl",
        n_steps=100,
        n_envs=4,
    )
    assert summary["total_timesteps"] == 800
    assert summary["ticks_per_second"] > 0


def test_cpu_sampler_reports():
    from worldsim.training import CpuUsageSampler

    sampler = CpuUsageSampler(interval=0.2)
    sampler.start()
    x = sum(i * i for i in range(10_000_000))  # burn CPU briefly
    stats = sampler.stop()
    assert x > 0
    assert stats["avg_cpu_utilization_pct"] > 0
    assert stats["max_single_core_pct"] >= stats["avg_cpu_utilization_pct"] - 1e-9


def test_benchmark_parallel_structure(tmp_path):
    """Speedup benchmark returns per-config timings and speedup ratios."""
    from worldsim.training import benchmark_parallel

    results = benchmark_parallel(
        timesteps=600,
        n_envs_configs=[1, 2],
        seed=21,
        size=32,
        num_settlements=2,
    )
    assert set(results["configs"].keys()) == {1, 2}
    seq = results["configs"][1]
    par = results["configs"][2]
    assert seq["wall_time_seconds"] > 0
    assert par["speedup_vs_sequential"] is not None
