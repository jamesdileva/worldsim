"""Population-based generational training (Sprint 19, Phase 4).

Trains N candidate policies per generation on disjoint world-seed subsets,
registers every candidate with lineage metadata, then selects the champion
(highest mean training return) and re-registers it under the bare generation
label so downstream tools (`rl dashboard`, `rl compare`) keep working.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .training import POLICIES_DIR, file_sha256, register_checkpoint, train


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
        obs, reward, terminated, truncated, _ = env.step(int(action))
        total += reward
        done = terminated or truncated
    return round(total, 4)


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


def select_champion(candidates: list[dict]) -> dict:
    """Highest score; first-listed candidate breaks ties (deterministic)."""
    def tiebreak(c: dict):
        seed = c.get("seed")
        return (c["score"], 0 if seed is None else -seed)
    return max(candidates, key=tiebreak)


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
) -> dict:
    """Evolutionary loop (Sprint 20): each generation after the first gets

    - the previous champion unchanged (**elitism**),
    - ``n_mutants`` Gaussian-noise children of the champion (scored by
      cheap evaluation rollouts), and
    - ``population_size`` freshly-trained random candidates.

    The highest-scoring candidate becomes the generation champion.
    Lineage (parent + mutation type) recorded per candidate."""
    store = _open_store()
    history = []
    prev_champion: dict | None = None
    try:
        for g in range(1, generations + 1):
            gen = f"gen{g}"
            print(f"[evolve] generation {gen}...")
            candidates: list[dict] = []
            started = time.time()

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
                    score = quick_eval(
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
                    })

            # --- Fresh random candidates ---------------------------------
            for i in range(population_size):
                label = f"{gen}_c{i}"
                seed = seed_base + g * 9973 + i * 101
                summary = train(
                    total_timesteps=timesteps_per_candidate,
                    seed=seed,
                    size=size,
                    num_settlements=num_settlements,
                    max_ticks=max_ticks,
                    save_path=POLICIES_DIR / f"policy_{label}",
                    log_path=POLICIES_DIR / f"{gen}_candidates.jsonl",
                    n_envs=n_envs,
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
                })

            champion = select_champion(candidates)
            promote_champion(store, gen, {
                "label": champion["label"],
                "mean_return": champion["score"],
                "episodes": None,
                "wall_time_seconds": round(time.time() - started, 1),
            }, parent_generation=(
                prev_champion["label"] if prev_champion else None
            ))
            entry = {
                "generation": gen,
                "champion": champion["label"],
                "champion_score": champion["score"],
                "champion_origin": champion.get("origin"),
                "parent": prev_champion["label"] if prev_champion else None,
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
