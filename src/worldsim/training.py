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
    baseline_territory: int = 0
    policy_territory: int = 0
    baseline_buildings: int = 0
    policy_buildings: int = 0
    baseline_routes_established: int = 0
    policy_routes_established: int = 0
    baseline_reward: float = 0.0
    policy_reward: float = 0.0


def _run_baseline(seed: int, size: int, num_settlements: int, ticks: int,
                  disaster_mult: float = 1.0, gather_mult: float = 1.0):
    """Rule-based-only run; returns settlement 0's survival/peak/end-state
    stats plus its cumulative §6.4 reward (shared measurement with the RL
    side — Sprint 17)."""
    from .rewards import compute_reward_components, total_of
    from .simulation import Simulation
    from .world import World

    sim = Simulation(World(seed=seed, size=size),
                     disaster_chance_mult=disaster_mult,
                     gather_mult=gather_mult)
    settlements = sim.spawn_settlements(count=num_settlements)
    target = settlements[0]
    peak = target.population
    survived_ticks = 0
    reward_total = 0.0
    prev_pop = target.population
    prev_buildings = sum(sim.buildings_of(target).values())
    routes_before = target.routes_established
    for t in range(1, ticks + 1):
        sim.step()
        if not target.is_alive:
            continue
        survived_ticks = t
        peak = max(peak, target.population)
        buildings_now = sum(sim.buildings_of(target).values())
        comps = compute_reward_components(
            prev_population=prev_pop,
            population=target.population,
            building_delta=buildings_now - prev_buildings,
            route_delta=target.routes_established - routes_before,
            food_stock=target.food_stock,
            starvation_progress=target.starvation_progress,
            repeated_action_count=0,
            action_executed=True,
        )
        reward_total += total_of(comps)
        prev_pop = target.population
        prev_buildings = buildings_now
        routes_before = target.routes_established
    end = {
        "territory": len(sim.territory_of(target)),
        "buildings": sum(sim.buildings_of(target).values()),
        "routes": target.routes_established,
        "food": round(target.food_stock, 1),
    }
    return survived_ticks, peak, end, round(reward_total, 3)


def _run_policy(model, seed: int, size: int, num_settlements: int,
                ticks: int, disaster_mult: float = 1.0,
                gather_mult: float = 1.0):
    """World where settlement 0 is driven by the trained policy."""
    from .env import WorldSimEnv

    env = WorldSimEnv(seed=seed, size=size, num_settlements=num_settlements,
                      max_ticks=ticks, disaster_chance_mult=disaster_mult,
                      gather_mult=gather_mult)
    obs, _ = env.reset(seed=seed)
    assert env.controlled is not None
    peak = env.controlled.population
    survived_ticks = 0
    reward_total = 0.0
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(int(action))
        done = terminated or truncated
        s = env.controlled
        if s.is_alive:
            survived_ticks = env.sim.tick
            peak = max(peak, s.population)
        reward_total += reward
    counts = env.sim.buildings_of(env.controlled)
    end = {
        "territory": len(env.sim.territory_of(env.controlled)),
        "buildings": sum(counts.values()),
        "routes": env.controlled.routes_established,
        "food": round(env.controlled.food_stock, 1),
    }
    return survived_ticks, peak, end, round(reward_total, 3)


def paired_permutation_pvalue(a, b, n_perm: int = 10_000,
                              seed: int = 0) -> float | None:
    """Two-sided paired permutation test. Returns None when there is no
    variance (all differences zero) — no signal to test."""
    import numpy as np

    diff = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    if not diff.any():
        return None
    rng = np.random.default_rng(seed)
    observed = abs(diff.mean())
    hits = 0
    for _ in range(n_perm):
        signs = rng.choice([-1.0, 1.0], size=len(diff))
        if abs(float((diff * signs).mean())) >= observed - 1e-12:
            hits += 1
    return round((hits + 1) / (n_perm + 1), 4)


def compare_generations(
    generations: list[str],
    num_worlds: int = 10,
    first_seed: int = 50_000,
    ticks: int = 3000,
    size: int = 256,
    num_settlements: int = 5,
    disaster_mult: float = 1.0,
    gather_mult: float = 1.0,
    db_path: str | Path | None = None,
) -> dict:
    """Sprint 18: evaluate each generation vs baseline on identical worlds
    and analyze the learning curve.

    Returns per-generation aggregates, monotonicity checks across
    generations (on survival / reward / peak population), and a per-seed
    regression check of the newest generation against the first."""
    per_gen = {}
    from .db import DEFAULT_DB_PATH, WorldStore

    for gen in generations:
        store = WorldStore(db_path or DEFAULT_DB_PATH)
        try:
            resolved, record = resolve_policy_path(store, gen)
        finally:
            store.close()
        results = evaluate_vs_baseline(
            model_path=resolved,
            num_worlds=num_worlds,
            first_seed=first_seed,
            ticks=ticks,
            size=size,
            num_settlements=num_settlements,
            disaster_mult=disaster_mult,
            gather_mult=gather_mult,
        )
        results["agent_type"] = f"policy_{gen}_vs_rulebased"
        per_gen[gen] = results

    # Learning-curve aggregates per generation.
    curve = {
        gen: {
            "mean_survival": r["mean_policy_survival"],
            "reward_win_fraction": r["reward_win_fraction"],
            "mean_peak_pop": round(r["mean_policy_peak_pop"], 1),
            "episodes_trained": _gen_training_episodes(gen),
        }
        for gen, r in per_gen.items()
    }

    # Monotonicity across generations (in generation order).
    gens_in_order = list(generations)
    monotonic = {}
    for key, getter in (
        ("survival", lambda g: curve[g]["mean_survival"]),
        ("reward_wins", lambda g: curve[g]["reward_win_fraction"]),
        ("peak_pop", lambda g: curve[g]["mean_peak_pop"]),
    ):
        values = [getter(g) for g in gens_in_order]
        monotonic[key] = all(
            values[i] <= values[i + 1] + 1e-9 for i in range(len(values) - 1)
        ) or all(
            values[i] >= values[i + 1] - 1e-9 for i in range(len(values) - 1)
        )
        monotonic[f"{key}_values"] = values

    # Regression check: newest gen vs first gen, per seed.
    first_results = per_gen[gens_in_order[0]]["results"]
    last_results = per_gen[gens_in_order[-1]]["results"]
    regressions = []
    for a, b in zip(first_results, last_results):
        if b["policy_survival_ticks"] < a["policy_survival_ticks"]:
            regressions.append({
                "seed": a["seed"],
                f"{gens_in_order[0]}_survival": a["policy_survival_ticks"],
                f"{gens_in_order[-1]}_survival": b["policy_survival_ticks"],
            })

    return {
        "generations": gens_in_order,
        "curve": curve,
        "monotonic": monotonic,
        "regressions": regressions,
        "per_generation_results": per_gen,
    }


def _gen_training_episodes(gen: str):
    """Training episodes for a generation from its registry summary."""
    summary_path = POLICIES_DIR / f"policy_{gen}_summary.json"
    if Path(summary_path).exists():
        data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        return data.get("episodes")
    return None


def generate_learning_curve_plot(curve: dict, out_png: str | Path) -> Path:
    """Learning curve PNG: mean survival + reward wins per generation."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gens = list(curve.keys())
    x = range(len(gens))
    fig, ax1 = plt.subplots(figsize=(8, 4.5))
    ax1.plot(x, [curve[g]["mean_survival"] for g in gens], "o-",
             label="mean survival (ticks)", color="tab:blue")
    ax1.set_xticks(x)
    ax1.set_xticklabels(gens)
    ax1.set_ylabel("mean survival (ticks)")
    ax2 = ax1.twinx()
    ax2.plot(x, [curve[g]["reward_win_fraction"] * 100 for g in gens], "s--",
             label="reward win fraction (%)", color="tab:orange")
    ax2.set_ylabel("reward win fraction (%)")
    ax1.set_title("Learning curve across generations")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="lower right",
               fontsize=8)
    fig.tight_layout()
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def evaluate_vs_baseline(
    model_path: str | Path,
    num_worlds: int = 10,
    first_seed: int = 50_000,
    ticks: int = 3000,
    size: int = 256,
    num_settlements: int = 5,
    disaster_mult: float = 1.0,
    gather_mult: float = 1.0,
) -> dict:
    """Paired A/B evaluation on identical world seeds.

    Sprint 17: collects survival, peak population, end-state territory/
    buildings/routes, and cumulative §6.4 reward for BOTH controllers
    (shared reward measurement), plus per-metric statistical significance."""
    import numpy as np

    from scipy.stats import wilcoxon

    from stable_baselines3 import PPO

    model = PPO.load(str(model_path), device="cpu")
    results: list[PairedResult] = []
    wins = 0
    ties = 0
    reward_wins = 0
    for i in range(num_worlds):
        seed = first_seed + i
        base_surv, base_peak, base_end, base_reward = _run_baseline(
            seed, size, num_settlements, ticks, disaster_mult, gather_mult
        )
        pol_surv, pol_peak, pol_end, pol_reward = _run_policy(
            model, seed, size, num_settlements, ticks, disaster_mult,
            gather_mult
        )
        if pol_surv > base_surv:
            wins += 1
        elif pol_surv == base_surv:
            ties += 1
        if pol_reward > base_reward:
            reward_wins += 1
        results.append(PairedResult(
            seed=seed,
            baseline_survival_ticks=base_surv,
            policy_survival_ticks=pol_surv,
            baseline_peak_population=base_peak,
            policy_peak_population=pol_peak,
            baseline_territory=base_end["territory"],
            policy_territory=pol_end["territory"],
            baseline_buildings=base_end["buildings"],
            policy_buildings=pol_end["buildings"],
            baseline_routes_established=base_end["routes"],
            policy_routes_established=pol_end["routes"],
            baseline_reward=base_reward,
            policy_reward=pol_reward,
        ))
    mean_base = float(np.mean([r.baseline_survival_ticks for r in results]))
    mean_pol = float(np.mean([r.policy_survival_ticks for r in results]))
    decided = num_worlds - ties

    # Per-metric means + Wilcoxon signed-rank significance (Sprint 17).
    metric_defs = {
        "survival_ticks": ("baseline_survival_ticks",
                           "policy_survival_ticks"),
        "peak_population": ("baseline_peak_population",
                            "policy_peak_population"),
        "territory": ("baseline_territory", "policy_territory"),
        "buildings": ("baseline_buildings", "policy_buildings"),
        "routes_established": ("baseline_routes_established",
                               "policy_routes_established"),
        "cumulative_reward": ("baseline_reward", "policy_reward"),
    }
    metrics = {}
    for name, (b_field, p_field) in metric_defs.items():
        b_vals = [getattr(r, b_field) for r in results]
        p_vals = [getattr(r, p_field) for r in results]
        try:
            _, p_value = wilcoxon(b_vals, p_vals)
            p_value = round(float(p_value), 4)
        except ValueError:
            p_value = None  # all differences zero — no variance
        metrics[name] = {
            "baseline_mean": round(float(np.mean(b_vals)), 2),
            "policy_mean": round(float(np.mean(p_vals)), 2),
            "delta": round(float(np.mean(p_vals) - np.mean(b_vals)), 2),
            "wilcoxon_p": p_value,
        }

    return {
        "worlds": num_worlds,
        "difficulty": {
            "disaster_chance_mult": disaster_mult,
            "gather_mult": gather_mult,
        },
        "policy_wins": wins,
        "ties": ties,
        "reward_wins": reward_wins,
        "win_fraction_strict": round(wins / num_worlds, 3),
        "win_fraction_of_decided": (
            round(wins / decided, 3) if decided else None
        ),
        "reward_win_fraction": round(reward_wins / num_worlds, 3),
        "mean_baseline_survival": round(mean_base, 1),
        "mean_policy_survival": round(mean_pol, 1),
        "mean_baseline_peak_pop": float(np.mean(
            [r.baseline_peak_population for r in results])),
        "mean_policy_peak_pop": float(np.mean(
            [r.policy_peak_population for r in results])),
        "metrics": metrics,
        "results": [asdict(r) for r in results],
    }


def generate_report(results: dict, out_md: str | Path,
                    out_png: str | Path | None = None) -> Path:
    """Write a markdown comparison report (+ optional bar-chart PNG)."""
    md_path = Path(out_md)
    lines = [
        "# Policy vs Rule-Based Baseline — Evaluation Report",
        "",
        f"- Worlds evaluated: **{results['worlds']}**",
        f"- Difficulty: {results.get('difficulty', {})}",
        f"- Survival win fraction (strict): "
        f"**{results['win_fraction_strict']*100:.0f}%** "
        f"(ties: {results['ties']})",
        f"- Reward win fraction: **{results['reward_win_fraction']*100:.0f}%**",
        "",
        "## Metric summary",
        "",
        "| Metric | Baseline | Policy | Delta | Wilcoxon p |",
        "|---|---|---|---|---|",
    ]
    for name, m in results.get("metrics", {}).items():
        p_str = str(m["wilcoxon_p"]) if m["wilcoxon_p"] is not None else "n/a"
        sig = " *" if m["wilcoxon_p"] is not None and m["wilcoxon_p"] < 0.05 else ""
        lines.append(
            f"| {name} | {m['baseline_mean']} | {m['policy_mean']} | "
            f"{m['delta']:+} | {p_str}{sig} |"
        )
    lines += ["", "* p < 0.05 (Wilcoxon signed-rank)", ""]
    lines += ["## Per-world detail", "",
              "| Seed | Surv B | Surv P | Peak B | Peak P | Terr B | Terr P |"
              " Bld B | Bld P | Reward B | Reward P |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results["results"]:
        lines.append(
            f"| {r['seed']} | {r['baseline_survival_ticks']} | "
            f"{r['policy_survival_ticks']} | "
            f"{r['baseline_peak_population']} | "
            f"{r['policy_peak_population']} | {r['baseline_territory']} | "
            f"{r['policy_territory']} | {r['baseline_buildings']} | "
            f"{r['policy_buildings']} | {r['baseline_reward']} | "
            f"{r['policy_reward']} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    if out_png is not None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        names = list(results.get("metrics", {}).keys())
        base_means = [results["metrics"][n]["baseline_mean"] for n in names]
        pol_means = [results["metrics"][n]["policy_mean"] for n in names]
        x = np.arange(len(names))
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(x - 0.2, base_means, 0.4, label="Baseline")
        ax.bar(x + 0.2, pol_means, 0.4, label="Policy")
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=20, ha="right")
        ax.set_title("Policy vs Rule-Based Baseline (means over worlds)")
        ax.legend()
        fig.tight_layout()
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_png, dpi=120)
        plt.close(fig)
    return md_path
