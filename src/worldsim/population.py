"""Population-based generational training (Sprint 19, Phase 4).

Trains N candidate policies per generation on disjoint world-seed subsets,
registers every candidate with lineage metadata, then selects the champion
(highest mean training return) and re-registers it under the bare generation
label so downstream tools (`rl dashboard`, `rl compare`) keep working.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .training import POLICIES_DIR, file_sha256, register_checkpoint, train

STRATEGY_PRIOR_PATH = POLICIES_DIR / "strategy_priors.json"


# ---------------------------------------------------------------------------
# Strategy-memory aggregation (Sprint 21)
# ---------------------------------------------------------------------------

def merge_strategy_memories(memories: list[dict],
                            ema_alpha: float = 0.3) -> dict[tuple, float]:
    """Merge multiple {(archetype, action): ema} tables into one prior.

    Later generations weigh more (each table applied with EMA weight)."""
    merged: dict[tuple, float] = {}
    for mem in memories:
        for key, value in mem.items():
            merged[key] = (
                value if key not in merged
                else merged[key] * (1 - ema_alpha) + value * ema_alpha
            )
    return merged


def save_strategy_prior(prior: dict, path: str | Path = STRATEGY_PRIOR_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"archetype": arch, "action": action, "ema": value}
        for (arch, action), value in prior.items()
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_strategy_prior(path: str | Path = STRATEGY_PRIOR_PATH) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (obj["archetype"], int(obj["action"])): obj["ema"]
        for obj in payload
    }


def prior_actions_for(archetype: str, prior: dict, top_k: int = 5):
    """Top-k action IDs by EMA reward for one archetype."""
    entries = [(action, ema) for (arch, action), ema in prior.items()
               if arch == archetype]
    entries.sort(key=lambda kv: -kv[1])
    return [action for action, _ in entries[:top_k]]


def mutate_checkpoint(model_path: str | Path, out_path: str | Path,
                      strength: float = 0.05, seed: int = 0) -> Path:
    """Create a mutated child checkpoint: Gaussian noise injected into all
    policy parameters, scaled by each tensor's own std (Sprint 20).

    Topology is preserved; the child is a loadable SB3 checkpoint."""
    import numpy as np
    import torch
    from stable_baselines3 import PPO

    model = PPO.load(str(model_path), device="cpu")
    rng = np.random.default_rng(seed)
    with torch.no_grad():
        for param in model.policy.parameters():
            if param.numel() <= 1:
                continue
            scale = float(param.std())
            noise = rng.normal(
                0.0, strength * max(scale, 1e-6), size=tuple(param.shape)
            ).astype(np.float32)
            param.data += torch.from_numpy(noise).to(param.dtype)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out_path))
    return out_path


def quick_eval(model_path: str | Path, ticks: int = 300, size: int = 32,
               num_settlements: int = 2, seed: int = 0) -> float:
    """Cheap cumulative-reward rollout for scoring mutants (no baseline run,
    no persistence)."""
    score, _, _ = quick_eval_guarded(model_path, ticks=ticks, size=size,
                                     num_settlements=num_settlements,
                                     seed=seed)
    return score


def quick_eval_guarded(model_path: str | Path, ticks: int = 300,
                       size: int = 32, num_settlements: int = 2,
                       seed: int = 0) -> tuple[float, str | None, bool]:
    """Score a checkpoint AND assess reward-hacking over the rollout.

    Returns (cumulative_reward, dominant_hack_source_or_None, quarantined).
    Quarantine triggers when the guard reaches level 3 during the rollout."""
    from stable_baselines3 import PPO

    from .env import WorldSimEnv

    model = PPO.load(str(model_path), device="cpu")
    env = WorldSimEnv(seed=seed, size=size, num_settlements=num_settlements,
                      max_ticks=ticks)
    obs, _ = env.reset(seed=seed)
    total = 0.0
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(int(action))
        total += reward
        done = terminated or truncated or info["quarantined"]
    dominant = env.reward_guard.dominant_source()
    quarantined = env.reward_guard.quarantined_at_tick is not None
    return round(total, 4), dominant, quarantined


def strategy_shift_report(generation_paths: dict[str, str | Path],
                          ticks: int = 600, size: int = 48,
                          num_settlements: int = 3) -> dict:
    """Run each generation's champion in a small world and report the
    settlement strategy-label distribution — how behavior mix shifts across
    generations (Sprint 11 labels, Sprint 20 report)."""
    from stable_baselines3 import PPO

    from .env import WorldSimEnv

    report: dict[str, dict[str, int]] = {}
    for gen, path in generation_paths.items():
        model = PPO.load(str(path), device="cpu")
        env = WorldSimEnv(seed=7, size=size, num_settlements=num_settlements,
                          max_ticks=ticks)
        env.reset(seed=7)
        done = False
        while not done:
            action, _ = model.predict(env._obs, deterministic=True)
            _, _, terminated, truncated, _ = env.step(int(action))
            done = terminated or truncated
        sim = env.sim
        sim._update_strategy_labels()
        dist = sim.strategy_distribution()
        report[gen] = dist
    return report


def train_population(
    generation: str,
    population_size: int = 4,
    timesteps_per_candidate: int = 4096,
    seed_base: int = 1000,
    size: int = 64,
    num_settlements: int = 3,
    max_ticks: int = 1000,
    n_envs: int = 1,
    parent_generation: str | None = None,
    db_store=None,
) -> dict:
    """Train one generation's candidate pool and select its champion.

    Candidates get labels ``{gen}_c{i}`` with deterministic per-candidate
    seeds; the champion is copied to the bare ``{gen}`` checkpoint label.
    Selection uses mean training return with first-candidate tie-break
    (deterministic)."""
    store = db_store if db_store is not None else _open_store()
    owned_store = db_store is None
    candidates: list[dict] = []
    try:
        for i in range(population_size):
            label = f"{generation}_c{i}"
            seed = seed_base + generation_index(generation) * 9973 + i * 101
            started = time.time()
            summary = train(
                total_timesteps=timesteps_per_candidate,
                seed=seed,
                size=size,
                num_settlements=num_settlements,
                max_ticks=max_ticks,
                save_path=POLICIES_DIR / f"policy_{label}",
                log_path=POLICIES_DIR / f"{generation}_candidates.jsonl",
                n_envs=n_envs,
            )
            record = register_checkpoint(
                store, label, POLICIES_DIR / f"policy_{label}", summary
            )
            candidates.append({
                "label": label,
                "seed": seed,
                "mean_return": summary["mean_return"],
                "episodes": summary.get("episodes"),
                "checkpoint": record["path"],
                "checksum": record["checksum"],
                "wall_time_seconds": round(time.time() - started, 1),
                "score": summary["mean_return"],
            })
        champion = select_champion(candidates)
        promote_champion(store, generation, champion, parent_generation)
    finally:
        if owned_store:
            store.close()
    return {
        "generation": generation,
        "parent": parent_generation,
        "champion": champion["label"],
        "champion_mean_return": champion["mean_return"],
        "candidates": candidates,
    }


def generation_index(generation: str) -> int:
    """Stable numeric index from a 'genN[r]' style label."""
    digits = "".join(ch for ch in generation if ch.isdigit())
    return int(digits) if digits else 0


def select_champion(candidates: list[dict],
                    quarantined_labels: set[str] | None = None) -> dict:
    """Highest score among non-quarantined candidates; first-listed
    candidate breaks ties (deterministic). Quarantined candidates (Sprint
    24 reward-hacking response) are excluded from selection entirely."""
    blocked = quarantined_labels or set()

    def tiebreak(c: dict):
        seed = c.get("seed")
        return (c["score"], 0 if seed is None else -seed)

    eligible = [c for c in candidates if c["label"] not in blocked]
    if not eligible:
        # Never return nothing: fall back to the highest-scoring candidate,
        # but the caller is expected to log the mass quarantine.
        return max(candidates, key=tiebreak)
    return max(eligible, key=tiebreak)


def promote_champion(db_store, generation: str, champion: dict,
                     parent_generation: str | None) -> str:
    """Copy the winning candidate's checkpoint under the bare generation
    label and register it (parent lineage recorded)."""
    src = Path(POLICIES_DIR / f"policy_{champion['label']}.zip")
    dst = Path(POLICIES_DIR / f"policy_{generation}.zip")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    record_id = db_store.insert_policy_checkpoint({
        "generation": generation,
        "path": str(dst),
        "algorithm": "PPO",
        "total_timesteps": 0,  # aggregate lives in candidate rows
        "episodes": champion.get("episodes"),
        "mean_episode_return": champion.get("score",
                                            champion.get("mean_return")),
        "wall_time_seconds": champion.get("wall_time_seconds"),
        "checksum": file_sha256(dst),
        "size_bytes": dst.stat().st_size,
        "parent": parent_generation,
    })
    return record_id


def _open_store():
    from .db import DEFAULT_DB_PATH, WorldStore

    return WorldStore(DEFAULT_DB_PATH)


def evolve(
    generations: int = 2,
    population_size: int = 2,
    n_mutants: int = 2,
    mutation_strength: float = 0.05,
    timesteps_per_candidate: int = 4096,
    seed_base: int = 1000,
    size: int = 64,
    num_settlements: int = 3,
    max_ticks: int = 1000,
    n_envs: int = 1,
    eval_ticks: int = 300,
    eval_seed_base: int = 9000,
    eval_seed_count: int = 3,
    curriculum: bool = True,
    strategy_prior_path: str | Path | None = STRATEGY_PRIOR_PATH,
) -> dict:
    """Evolutionary loop (Sprint 20/21): each generation after the first
    gets

    - the previous champion unchanged (**elitism**),
    - ``n_mutants`` Gaussian-noise children of the champion (scored by
      cheap evaluation rollouts), and
    - ``population_size`` freshly-trained random candidates.

    The highest-scoring candidate becomes the generation champion.

    Sprint 21 additions:
    - **Curriculum**: after each generation the champion is scored across an
      evaluation seed set; seeds scoring below the champion's own mean are
      marked failures and become the NEXT generation's fresh-candidate
      training worlds (failure-weighted curriculum).
    - **Strategy priors**: when a prior file exists, agents bias idle
      fallback decisions toward historically high-reward actions."""
    store = _open_store()
    history = []
    prev_champion: dict | None = None
    curriculum_failure_seeds: list[int] = []
    try:
        strategy_prior = (
            load_strategy_prior(strategy_prior_path)
            if strategy_prior_path else {}
        )
        if strategy_prior:
            print(
                f"[evolve] loaded strategy prior "
                f"({len(strategy_prior)} entries)"
            )
        eval_seeds = [
            eval_seed_base + i for i in range(max(eval_seed_count, 1))
        ]
        for g in range(1, generations + 1):
            gen = f"gen{g}"
            print(f"[evolve] generation {gen}...")
            candidates: list[dict] = []
            started = time.time()

            # Curriculum: failing seeds become fresh-candidate worlds.
            if curriculum and curriculum_failure_seeds:
                print(
                    f"[evolve] curriculum: prioritizing failure seeds "
                    f"{curriculum_failure_seeds}"
                )
            def _candidate_world_seed(i: int) -> int:
                if curriculum and curriculum_failure_seeds:
                    return curriculum_failure_seeds[i % len(curriculum_failure_seeds)]
                return seed_base + g * 9973 + i * 101

            if prev_champion is not None:
                # --- Elitism: champion survives unchanged ----------------
                elite_label = f"{gen}_e"
                shutil.copyfile(prev_champion["checkpoint"],
                                POLICIES_DIR / f"policy_{elite_label}.zip")
                register_checkpoint(
                    store, elite_label,
                    POLICIES_DIR / f"policy_{elite_label}.zip",
                    {"total_timesteps": 0, "episodes": None},
                    parent=prev_champion["label"], mutation="elite",
                )
                candidates.append({
                    "label": elite_label,
                    "seed": None,
                    "score": prev_champion["score"],
                    "origin": "elite",
                    "parent": prev_champion["label"],
                })

                # --- Mutants: Gaussian-noise children of the champion ---
                for m in range(n_mutants):
                    m_label = f"{gen}_m{m}"
                    strength = mutation_strength * (1 + m)
                    mutate_checkpoint(
                        prev_champion["checkpoint"],
                        POLICIES_DIR / f"policy_{m_label}.zip",
                        strength=strength,
                        seed=seed_base + g * 7919 + m,
                    )
                    register_checkpoint(
                        store, m_label,
                        POLICIES_DIR / f"policy_{m_label}.zip",
                        {"total_timesteps": 0, "episodes": None},
                        parent=prev_champion["label"],
                        mutation=f"gaussian:{strength:.3f}",
                    )
                    score, hack_source, quarantined = quick_eval_guarded(
                        POLICIES_DIR / f"policy_{m_label}.zip",
                        ticks=eval_ticks, size=size,
                        num_settlements=num_settlements,
                        seed=seed_base + g * 31 + m,
                    )
                    candidates.append({
                        "label": m_label,
                        "seed": seed_base + g * 7919 + m,
                        "score": score,
                        "origin": "mutant",
                        "mutation_strength": strength,
                        "parent": prev_champion["label"],
                        "quarantined": quarantined,
                        "hack_source": hack_source if quarantined else None,
                    })

            # --- Fresh random candidates ---------------------------------
            for i in range(population_size):
                label = f"{gen}_c{i}"
                seed = _candidate_world_seed(i)
                summary = train(
                    total_timesteps=timesteps_per_candidate,
                    seed=seed,
                    size=size,
                    num_settlements=num_settlements,
                    max_ticks=max_ticks,
                    save_path=POLICIES_DIR / f"policy_{label}",
                    log_path=POLICIES_DIR / f"{gen}_candidates.jsonl",
                    n_envs=n_envs,
                    strategy_prior=strategy_prior if strategy_prior else None,
                )
                register_checkpoint(
                    store, label, POLICIES_DIR / f"policy_{label}", summary,
                    mutation="fresh",
                )
                candidates.append({
                    "label": label,
                    "seed": seed,
                    "score": summary["mean_return"],
                    "origin": "fresh",
                    "parent": None,
                    "curriculum_world": (
                        seed if curriculum and curriculum_failure_seeds
                        else None
                    ),
                })

            quarantined = {
                c["label"] for c in candidates if c.get("quarantined")
            }
            if quarantined:
                print(
                    f"[evolve] {gen}: QUARANTINED reward hackers: "
                    f"{sorted(quarantined)}"
                )
            champion = select_champion(candidates,
                                       quarantined_labels=quarantined)
            promote_champion(store, gen, {
                "label": champion["label"],
                "mean_return": champion["score"],
                "episodes": None,
                "wall_time_seconds": round(time.time() - started, 1),
            }, parent_generation=(
                prev_champion["label"] if prev_champion else None
            ))

            # Sprint 21 curriculum: score the champion across the evaluation
            # seed set; below-mean seeds become next generation's worlds.
            champion_path = POLICIES_DIR / f"policy_{gen}.zip"
            seed_scores = {
                s: quick_eval(champion_path, ticks=eval_ticks, size=size,
                              num_settlements=num_settlements, seed=s)
                for s in eval_seeds
            }
            mean_score = sum(seed_scores.values()) / len(seed_scores)
            if curriculum:
                curriculum_failure_seeds = sorted(
                    s for s, v in seed_scores.items() if v < mean_score
                )
            else:
                curriculum_failure_seeds = []

            entry = {
                "generation": gen,
                "champion": champion["label"],
                "champion_score": champion["score"],
                "champion_origin": champion.get("origin"),
                "parent": prev_champion["label"] if prev_champion else None,
                "seed_scores": {str(k): v for k, v in seed_scores.items()},
                "curriculum_failure_seeds": list(curriculum_failure_seeds),
                "candidates": sorted(
                    candidates, key=lambda c: -c["score"]
                ),
            }
            print(
                f"[evolve] {gen} champion: {champion['label']} "
                f"({champion.get('origin')}, score "
                f"{champion['score']:.4f})"
            )
            history.append(entry)
            prev_champion = {
                "label": champion["label"],
                "checkpoint": str(POLICIES_DIR /
                                  f"policy_{champion['label']}.zip"),
                "score": champion["score"],
            }
    finally:
        store.close()
    return {"generations": history}
