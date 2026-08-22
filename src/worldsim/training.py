"""PPO training and evaluation against the rule-based baseline (Sprint 14),
with parallel VecEnv training and speedup measurement (Sprint 15).

Training runs PPO over WorldSimEnv; evaluation performs paired A/B runs on
identical world seeds: settlement 0 driven by the trained policy vs the same
settlement under the rule-based baseline, comparing survival time.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

POLICIES_DIR = Path("data/world_sim/policies")
DEFAULT_LOG_PATH = POLICIES_DIR / "train_log.jsonl"


def file_sha256(path: str | Path) -> str:
    """SHA-256 of a file, streamed (checkpoint corruption detection)."""
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def verify_policy_checksum(path: str | Path, expected_sha256: str | None) -> bool:
    if not Path(path).exists():
        return False
    # Legacy records predate checksums (None) — nothing to verify.
    if expected_sha256 is None:
        return True
    return file_sha256(path) == expected_sha256


def register_checkpoint(db_store, generation: str, path: str | Path,
                        summary: dict) -> dict:
    """Hash the checkpoint file and record it in policy_checkpoints.
    Returns the registry record."""
    zip_path = Path(f"{path}.zip")
    record = {
        "generation": generation,
        "path": str(zip_path),
        "total_timesteps": summary["total_timesteps"],
        "episodes": summary.get("episodes"),
        "mean_episode_return": summary.get("mean_return"),
        "wall_time_seconds": summary.get("wall_time_seconds"),
        "checksum": file_sha256(zip_path),
        "size_bytes": zip_path.stat().st_size,
    }
    record["id"] = db_store.insert_policy_checkpoint(record)
    return record


def resolve_policy_path(db_store, policy_id: str, explicit_path=None):
    """Resolve a checkpoint path by registry id (generation) or explicit
    path; verifies the recorded checksum when resolving via registry.

    Returns (resolved_path, registry_record | None)."""
    if explicit_path is not None:
        return str(explicit_path), None
    record = db_store.get_latest_policy_checkpoint(policy_id)
    if record is None:
        raise ValueError(
            f"No registered checkpoint for generation '{policy_id}'"
        )
    if not verify_policy_checksum(record["path"], record["checksum"]):
        raise ValueError(
            f"Checkpoint corruption detected for '{policy_id}': "
            f"{record['path']} does not match recorded checksum"
        )
    return record["path"], record


class CpuUsageSampler:
    """Background thread sampling per-core CPU utilization during training
    (Sprint 15: track which cores are used and how hard)."""

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self.samples_overall: list[float] = []
        self.max_core_samples: list[float] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import psutil

        psutil.cpu_percent(interval=None, percpu=True)  # prime counters

        def loop():
            while not self._stop_event.is_set():
                per_core = psutil.cpu_percent(interval=self.interval,
                                              percpu=True)
                if per_core:
                    self.samples_overall.append(sum(per_core) / len(per_core))
                    self.max_core_samples.append(max(per_core))

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=self.interval * 2 + 1)
            self._thread = None
        overall = (
            sum(self.samples_overall) / len(self.samples_overall)
            if self.samples_overall else 0.0
        )
        max_core = (
            max(self.max_core_samples) if self.max_core_samples else 0.0
        )
        return {
            "avg_cpu_utilization_pct": round(overall, 1),
            "max_single_core_pct": round(max_core, 1),
        }


def benchmark_parallel(
    timesteps: int = 4000,
    n_envs_configs: list[int] | None = None,
    seed: int = 42,
    size: int = 64,
    num_settlements: int = 3,
    max_ticks: int = 1000,
) -> dict:
    """Compare sequential vs parallel wall-clock at identical total
    timesteps. Returns per-config timings, speedup ratios, and CPU stats."""
    if n_envs_configs is None:
        n_envs_configs = [1, 2, 4]
    results = {}
    baseline_wall = None
    cpu = CpuUsageSampler()
    for n_envs in n_envs_configs:
        save_path = POLICIES_DIR / f"bench_n{n_envs}"
        log_path = POLICIES_DIR / f"bench_n{n_envs}_log.jsonl"
        cpu.start()
        summary = train(
            total_timesteps=timesteps,
            seed=seed,
            size=size,
            num_settlements=num_settlements,
            max_ticks=max_ticks,
            save_path=save_path,
            log_path=log_path,
            n_envs=n_envs,
        )
        stats = cpu.stop()
        wall = summary["wall_time_seconds"]
        if baseline_wall is None:
            baseline_wall = wall
        speedup = round(baseline_wall / wall, 2) if wall else None
        results[n_envs] = {
            "wall_time_seconds": wall,
            "speedup_vs_sequential": speedup,
            "ticks_per_second": summary["ticks_per_second"],
            **stats,
        }
        print(
            f"  n_envs={n_envs}: {wall}s ({summary['ticks_per_second']} "
            f"ticks/s), speedup x{speedup}, avg CPU {stats['avg_cpu_utilization_pct']}%"
        )
    return {
        "timesteps": timesteps,
        "configs": results,
    }


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
    n_envs: int = 1,
) -> dict:
    """Train PPO via model.learn(). Saves checkpoint to save_path (SB3
    appends .zip). Returns metrics summary.

    n_envs > 1 runs parallel simulation workers (SubprocVecEnv); SB3's
    total_timesteps then counts TOTAL steps across all envs, so equal-
    timesteps comparisons measure throughput gains fairly."""
    from stable_baselines3 import PPO
    from stable_baselines3.common.monitor import Monitor

    from .env import WorldSimEnv

    env_kwargs = dict(size=size, num_settlements=num_settlements,
                      max_ticks=max_ticks)
    if n_envs > 1:
        from stable_baselines3.common.env_util import make_vec_env
        from stable_baselines3.common.vec_env import SubprocVecEnv

        env = make_vec_env(
            WorldSimEnv,
            n_envs=n_envs,
            seed=seed,
            vec_env_cls=SubprocVecEnv,
            env_kwargs=env_kwargs,
        )
        rollout_steps = max(1, min(n_steps, max_ticks))
    else:
        env = Monitor(WorldSimEnv(seed=seed, **env_kwargs))
        rollout_steps = min(n_steps, max_ticks)

    model = PPO(
        "MlpPolicy",
        env,
        seed=seed,
        verbose=verbose,
        n_steps=rollout_steps,
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
        "n_envs": n_envs,
        "wall_time_seconds": round(wall_time, 1),
        "ticks_per_second": round(total_timesteps / wall_time, 1),
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
