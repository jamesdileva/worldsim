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
    """Highest mean return; first-listed candidate breaks ties."""
    return max(candidates, key=lambda c: (c["mean_return"], -c["seed"]))


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
        "mean_episode_return": champion["mean_return"],
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
    population_size: int = 4,
    timesteps_per_candidate: int = 4096,
    seed_base: int = 1000,
    size: int = 64,
    num_settlements: int = 3,
    max_ticks: int = 1000,
    n_envs: int = 1,
) -> dict:
    """Run multiple generations; each generation's parent is the previous
    champion."""
    history = []
    parent = None
    for g in range(1, generations + 1):
        label = f"gen{g}"
        print(
            f"[evolve] generation {label}: training "
            f"{population_size} candidates x "
            f"{timesteps_per_candidate} timesteps..."
        )
        result = train_population(
            generation=label,
            population_size=population_size,
            timesteps_per_candidate=timesteps_per_candidate,
            seed_base=seed_base,
            size=size,
            num_settlements=num_settlements,
            max_ticks=max_ticks,
            n_envs=n_envs,
            parent_generation=parent,
        )
        print(
            f"[evolve] {label} champion: {result['champion']} "
            f"(return {result['champion_mean_return']})"
        )
        history.append(result)
        parent = label
    return {"generations": history}
