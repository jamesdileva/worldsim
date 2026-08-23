"""Strategy discovery from behavioral signatures (Sprint 23, Phase 4).

Rollout behavior is summarized as a normalized signature vector (building
mix, routes, raids, roads). Signatures across generations/worlds are
clustered to detect emergent play styles; clusters far from every archetype
reference centroid are flagged as NOVEL. Discovered strategies persist with
exemplars (checkpoint path + seed) so they can be re-instantiated.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .buildings import BuildingType

DISCOVERY_LOG_PATH = Path("data/world_sim/policies/discoveries.json")

# Reference centroids per known strategy label (Sprint 11 weak supervision).
# Dimensions: [farms_share, income_buildings_share, granary_share,
#              routes_norm, raids_norm, roads_norm]
ARCHETYPE_CENTROIDS: dict[str, np.ndarray] = {
    "agricultural": np.array([0.55, 0.10, 0.30, 0.05, 0.00, 0.05]),
    "mining": np.array([0.15, 0.65, 0.05, 0.05, 0.00, 0.20]),
    "trading": np.array([0.35, 0.15, 0.10, 0.60, 0.00, 0.30]),
    "military": np.array([0.25, 0.15, 0.05, 0.05, 0.70, 0.30]),
    "balanced": np.array([0.40, 0.25, 0.15, 0.25, 0.10, 0.25]),
}

NOVELTY_THRESHOLD = 0.55


def behavior_signature(
    farms: int,
    granaries: int,
    sawmills: int,
    mines: int,
    routes_established: int,
    raids_committed: int,
    roads: int,
) -> np.ndarray:
    """Normalized behavioral signature vector (sums to <= 1 on the building
    dimensions; trade/military/infrastructure activity scaled by caps)."""
    total_bld = max(farms + granaries + sawmills + mines, 1)
    return np.array([
        farms / total_bld,
        (sawmills + mines) / total_bld,
        granaries / total_bld,
        min(routes_established / 6.0, 1.0),
        min(raids_committed / 8.0, 1.0),
        min(roads / 60.0, 1.0),
    ], dtype=np.float64)


def collect_signature(sim, settlement) -> dict:
    """End-of-run signature + Sprint 11 label for one settlement."""
    from .agents import derive_strategy_label

    counts = sim.buildings_of(settlement)
    sig = behavior_signature(
        farms=counts[BuildingType.FARM],
        granaries=counts[BuildingType.GRANARY],
        sawmills=counts[BuildingType.SAWMILL],
        mines=counts[BuildingType.MINE],
        routes_established=settlement.routes_established,
        raids_committed=settlement.raids_committed,
        roads=len(sim.roads_of(settlement)),
    )
    return {
        "signature": sig.tolist(),
        "strategy_label": derive_strategy_label(
            farms=counts[BuildingType.FARM],
            granaries=counts[BuildingType.GRANARY],
            sawmills=counts[BuildingType.SAWMILL],
            mines=counts[BuildingType.MINE],
            active_routes=0,
            routes_established=settlement.routes_established,
            raids_committed=settlement.raids_committed,
        ),
    }


def novelty_vs_archetypes(signature: np.ndarray) -> tuple[bool, str, float]:
    """Flag signatures far from EVERY archetype reference centroid.
    Returns (is_novel, closest_label, closest_distance)."""
    best_label, best_dist = None, float("inf")
    for label, centroid in ARCHETYPE_CENTROIDS.items():
        dist = float(np.linalg.norm(signature - centroid))
        if dist < best_dist:
            best_label, best_dist = label, dist
    return best_dist > NOVELTY_THRESHOLD, best_label, round(best_dist, 4)


@dataclass
class Discovery:
    name: str
    first_seen: str          # generation label
    exemplar_seed: int
    checkpoint_path: str
    signature: list[float]
    strategy_label: str      # Sprint 11 weak-supervision label
    novel: bool
    closest_known: str
    discovered_at: float = field(default_factory=time.time)


def cluster_signatures(signatures: np.ndarray, n_clusters: int = 3,
                       seed: int = 0):
    """k-means over signatures; returns (labels, centroids). Falls back to
    single-cluster when there are fewer samples than clusters."""
    from scipy.cluster.vq import kmeans2

    n = len(signatures)
    if n == 0:
        return np.array([]), np.zeros((0, signatures.shape[1] if n else 6))
    k = min(n_clusters, n)
    centroids, labels = kmeans2(signatures, k, minit="++", seed=seed,
                                iter=25)
    return labels, centroids


def discover_strategies(
    samples: list[dict],
    n_clusters: int = 3,
    novelty_threshold: float = NOVELTY_THRESHOLD,
) -> dict:
    """Cluster collected run samples into emergent strategies.

    Each sample: {generation, seed, checkpoint_path, signature (list),
    strategy_label}. Clusters are named by their dominant characteristic;
    clusters whose centroid is far from every archetype are marked novel."""
    if not samples:
        return {"clusters": [], "novel_clusters": []}
    sigs = np.array([s["signature"] for s in samples], dtype=np.float64)
    labels, centroids = cluster_signatures(sigs, n_clusters)

    feature_names = ["farm_share", "income_share", "granary_share",
                     "routes", "raids", "roads"]
    clusters = []
    for cid in range(len(centroids)):
        member_idx = [i for i, l in enumerate(labels) if l == cid]
        members = [samples[i] for i in member_idx]
        centroid = centroids[cid]
        dominant_feature = feature_names[int(np.argmax(centroid))]
        novel, closest, dist = novelty_vs_archetypes(centroid)
        # Weak supervision: most common Sprint-11 label among members.
        label_counts: dict[str, int] = {}
        for m in members:
            lbl = m.get("strategy_label", "unknown")
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
        weak_label = (
            max(label_counts.items(), key=lambda kv: kv[1])[0]
            if label_counts else "unknown"
        )
        clusters.append({
            "cluster_id": cid,
            "size": len(members),
            "dominant_feature": dominant_feature,
            "centroid": [round(float(v), 4) for v in centroid],
            "weak_supervision_label": weak_label,
            "novel": novel and dist > novelty_threshold,
            "closest_known": closest,
            "closest_distance": dist,
            "members": [
                {"generation": m.get("generation"), "seed": m.get("seed"),
                 "checkpoint_path": m.get("checkpoint_path")}
                for m in members
            ],
            "name": f"{dominant_feature}-focused"
                    + ("-novel" if novel and dist > novelty_threshold else ""),
        })
    novel_clusters = [c for c in clusters if c["novel"]]
    return {"clusters": clusters, "novel_clusters": novel_clusters}


def save_discovery_log(discoveries: list[dict],
                       path: Path = DISCOVERY_LOG_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(discoveries, indent=2), encoding="utf-8")
    return path


def load_discovery_log(path: Path = DISCOVERY_LOG_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def collect_generation_samples(
    model_path: str | Path,
    generation: str,
    seeds: list[int],
    size: int = 48,
    num_settlements: int = 3,
    ticks: int = 600,
) -> list[dict]:
    """Run one generation's champion across probe worlds; return behavior
    samples for every surviving settlement."""
    from stable_baselines3 import PPO

    from .env import WorldSimEnv

    model = PPO.load(str(model_path), device="cpu")
    samples: list[dict] = []
    for seed in seeds:
        env = WorldSimEnv(seed=seed, size=size,
                          num_settlements=num_settlements, max_ticks=ticks)
        obs, _ = env.reset(seed=seed)
        done = False
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, _, terminated, truncated, _ = env.step(int(action))
            done = terminated or truncated
        sim = env.sim
        for s in sim.settlements:
            sample = collect_signature(sim, s)
            sample.update({
                "generation": generation,
                "seed": seed,
                "checkpoint_path": str(model_path),
            })
            samples.append(sample)
    return samples
