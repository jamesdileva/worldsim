"""PPO training and evaluation against the rule-based baseline (Sprint 14).

Training runs PPO over WorldSimEnv; evaluation performs paired A/B runs on
identical world seeds: settlement 0 driven by the trained policy vs the same
settlement under the rule-based baseline, comparing survival time.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

POLICIES_DIR = Path("data/world_sim/policies")
DEFAULT_LOG_PATH = POLICIES_DIR / "train_log.jsonl"


class EpisodeMetricsCallback:
    """SB3-native metrics capture: per-episode returns/lengths (via Monitor's
    info["episode"]) and policy/value losses + entropy (via the logger)."""

    def __init__(self, log_path: str | Path = DEFAULT_LOG_PATH) -> None:
        self.log_path = Path(log_path)
        self.episodes: list[dict] = []
        self.losses: list[float] = []
        self.entropies: list[float] = []

    def record_episode(self, timesteps: int, episode_return: float,
                       length: int) -> None:
        self.episodes.append({
            "episode": len(self.episodes),
            "timesteps": timesteps,
            "return": round(episode_return, 4),
            "length": length,
        })
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.episodes[-1]) + "\n")

    def capture_losses(self, name_to_value: dict) -> None:
        for key in ("train/policy_loss", "train/value_loss"):
            if key in name_to_value:
                try:
                    self.losses.append(float(name_to_value[key]))
                except (TypeError, ValueError):
                    pass
        if "train/entropy" in name_to_value:
            try:
                self.entropies.append(float(name_to_value["train/entropy"]))
            except (TypeError, ValueError):
                pass

    def summary(self) -> dict:
        returns = [e["return"] for e in self.episodes]
        return {
            "episodes": len(self.episodes),
            "mean_return": (
                round(sum(returns) / len(returns), 4) if returns else 0.0
            ),
            "mean_policy_loss": (
                round(sum(self.losses) / len(self.losses), 5)
                if self.losses else None
            ),
            "mean_entropy": (
                round(sum(self.entropies) / len(self.entropies), 5)
                if self.entropies else None
            ),
        }


def make_sb3_callback(metrics: EpisodeMetricsCallback):
    """Build a standard SB3 BaseCallback that feeds `metrics`."""
    from stable_baselines3.common.callbacks import BaseCallback

    class _CaptureCallback(BaseCallback):
        def _on_step(self) -> bool:
            if self.model is not None:
                metrics.capture_losses(self.model.logger.name_to_value)
            for info in self.locals.get("infos", []):
                if "episode" in info:
                    metrics.record_episode(
                        int(self.num_timesteps),
                        info["episode"]["r"],
                        info["episode"]["l"],
                    )
            return True

    return _CaptureCallback()


def train(
    total_timesteps: int = 50_000,
    seed: int = 42,
    size: int = 64,
    num_settlements: int = 3,
    max_ticks: int = 1000,
    save_path: str | Path = POLICIES_DIR / "policy_gen1",
    log_path: str | Path = DEFAULT_LOG_PATH,
    n_steps: int = 512,
    verbose: int = 0,
) -> dict:
    """Train PPO via model.learn(). Saves checkpoint to save_path (SB3
    appends .zip). Returns metrics summary."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor

    from .env import WorldSimEnv

    env = Monitor(WorldSimEnv(seed=seed, size=size,
                              num_settlements=num_settlements,
                              max_ticks=max_ticks))
    model = PPO(
        "MlpPolicy",
        env,
        seed=seed,
        verbose=verbose,
        n_steps=min(n_steps, max_ticks),
        batch_size=64,
        learning_rate=3e-4,
        policy_kwargs=dict(net_arch=[128, 128]),
    )
    metrics = EpisodeMetricsCallback(log_path)
    callback = make_sb3_callback(metrics)

    start = time.time()
    model.learn(total_timesteps=total_timesteps, callback=callback,
                progress_bar=False)
    wall_time = time.time() - start

    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(save_path))

    summary = {
        "total_timesteps": total_timesteps,
        "wall_time_seconds": round(wall_time, 1),
        "checkpoint_path": f"{save_path}.zip",
        **metrics.summary(),
    }
    with (save_path.parent / f"{save_path.stem}_summary.json").open(
        "w", encoding="utf-8"
    ) as fh:
        json.dump(summary, fh, indent=2)
    return summary


# ---------------------------------------------------------------------------
# Evaluation: trained policy vs rule-based baseline (paired per seed)
# ---------------------------------------------------------------------------

@dataclass
class PairedResult:
    seed: int
    baseline_survival_ticks: int
    policy_survival_ticks: int
    baseline_peak_population: int
    policy_peak_population: int


def _run_baseline(seed: int, size: int, num_settlements: int, ticks: int):
    """Rule-based-only run; returns settlement 0's survival/peak stats."""
    from .simulation import Simulation
    from .world import World

    sim = Simulation(World(seed=seed, size=size))
    settlements = sim.spawn_settlements(count=num_settlements)
    target = settlements[0]
    peak = target.population
    survived_ticks = 0
    for t in range(1, ticks + 1):
        sim.step()
        if target.is_alive:
            survived_ticks = t
            peak = max(peak, target.population)
    return survived_ticks, peak


def _run_policy(model, seed: int, size: int, num_settlements: int,
                ticks: int):
    """World where settlement 0 is driven by the trained policy."""
    from .env import WorldSimEnv

    env = WorldSimEnv(seed=seed, size=size, num_settlements=num_settlements,
                      max_ticks=ticks)
    obs, _ = env.reset(seed=seed)
    assert env.controlled is not None
    peak = env.controlled.population
    survived_ticks = 0
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, info = env.step(int(action))
        done = terminated or truncated
        s = env.controlled
        if s.is_alive:
            survived_ticks = env.sim.tick
            peak = max(peak, s.population)
    return survived_ticks, peak


def evaluate_vs_baseline(
    model_path: str | Path,
    num_worlds: int = 10,
    first_seed: int = 50_000,
    ticks: int = 3000,
    size: int = 256,
    num_settlements: int = 5,
) -> dict:
    """Paired A/B evaluation on identical world seeds.

    Returns aggregate results including win fraction on survival time
    (Sprint 14 acceptance: policy wins in >=60% of worlds)."""
    import numpy as np

    from stable_baselines3 import PPO

    model = PPO.load(str(model_path), device="cpu")
    results: list[PairedResult] = []
    wins = 0
    ties = 0
    for i in range(num_worlds):
        seed = first_seed + i
        base_surv, base_peak = _run_baseline(seed, size, num_settlements,
                                             ticks)
        pol_surv, pol_peak = _run_policy(model, seed, size, num_settlements,
                                         ticks)
        if pol_surv > base_surv:
            wins += 1
        elif pol_surv == base_surv:
            ties += 1
        results.append(PairedResult(
            seed=seed,
            baseline_survival_ticks=base_surv,
            policy_survival_ticks=pol_surv,
            baseline_peak_population=base_peak,
            policy_peak_population=pol_peak,
        ))
    mean_base = float(np.mean([r.baseline_survival_ticks for r in results]))
    mean_pol = float(np.mean([r.policy_survival_ticks for r in results]))
    decided = num_worlds - ties
    return {
        "worlds": num_worlds,
        "policy_wins": wins,
        "ties": ties,
        "win_fraction_strict": round(wins / num_worlds, 3),
        "win_fraction_of_decided": round(wins / decided, 3) if decided else None,
        "mean_baseline_survival": round(mean_base, 1),
        "mean_policy_survival": round(mean_pol, 1),
        "mean_baseline_peak_pop": float(np.mean(
            [r.baseline_peak_population for r in results])),
        "mean_policy_peak_pop": float(np.mean(
            [r.policy_peak_population for r in results])),
        "results": [asdict(r) for r in results],
    }
