"""Self-play / civilization competition (Sprint 22, Phase 4).

Runs k learned policies simultaneously inside ONE shared world: each policy
controls a distinct settlement (its rule-based agent is bypassed), all
mechanics remain shared, and competitive metrics (survival, peak population,
territory/resource shares, cumulative reward) are collected per controller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .agents import observe_vector
from .rewards import compute_reward_components, total_of


@dataclass
class ControllerMetrics:
    label: str
    survival_ticks: int
    peak_population: int
    final_population: int
    territory: int
    territory_share: float
    resource_total: float
    resource_share: float
    buildings: int
    routes_established: int
    cumulative_reward: float


def run_head_to_head(
    models: dict[str, object],
    seed: int,
    size: int = 256,
    num_settlements: int | None = None,
    ticks: int = 3000,
    disaster_mult: float = 1.0,
    gather_mult: float = 1.0,
) -> dict:
    """Run one shared world with len(models) external controllers.

    Controller i drives settlement i; every remaining settlement runs its
    rule-based agent. Returns per-controller metrics plus world metadata."""
    from .rewards import compute_reward_components, total_of
    from .simulation import Simulation
    from .world import World

    if num_settlements is None:
        num_settlements = max(len(models), 2)
    if num_settlements < len(models):
        raise ValueError("need at least one settlement per controller")

    sim = Simulation(World(seed=seed, size=size),
                     disaster_chance_mult=disaster_mult,
                     gather_mult=gather_mult)
    settlements = sim.spawn_settlements(count=num_settlements)
    labels = list(models.keys())

    # Accept checkpoint paths OR loaded models for convenience.
    from stable_baselines3 import PPO

    loaded = {
        label: (PPO.load(str(m), device="cpu") if isinstance(m, (str, Path))
                else m)
        for label, m in models.items()
    }
    controlled = {label: settlements[i] for i, label in enumerate(labels)}
    controlled_ids = {s.id for s in controlled.values()}

    prev_pop = {label: s.population for label, s in controlled.items()}
    prev_buildings = {
        label: sum(sim.buildings_of(s).values())
        for label, s in controlled.items()
    }
    prev_routes = {label: s.routes_established
                   for label, s in controlled.items()}
    stats = {
        label: {"reward": 0.0, "peak": s.population, "survived_ticks": 0}
        for label, s in controlled.items()
    }

    for tick in range(1, ticks + 1):
        # Controllers act first (simultaneously); mechanics follow.
        for label in labels:
            s = controlled[label]
            if not s.is_alive:
                continue
            obs = np.asarray(observe_vector(sim, s), dtype=np.float32)
            action = int(loaded[label].predict(obs, deterministic=True)[0])
            executed = sim.execute_action(s, action)
            comps = compute_reward_components(
                prev_population=prev_pop[label],
                population=s.population,
                building_delta=sum(
                    sim.buildings_of(s).values()) - prev_buildings[label],
                route_delta=s.routes_established - prev_routes[label],
                food_stock=s.food_stock,
                starvation_progress=s.starvation_progress,
                repeated_action_count=0,
                action_executed=executed,
            )
            stats[label]["reward"] += total_of(comps)

        sim.step(skip_agent_ids=controlled_ids)

        for label in labels:
            s = controlled[label]
            if s.is_alive:
                stats[label]["peak"] = max(stats[label]["peak"],
                                           s.population)
                stats[label]["survived_ticks"] = tick
            prev_pop[label] = s.population
            prev_buildings[label] = sum(sim.buildings_of(s).values())
            prev_routes[label] = s.routes_established

    # End-state shares across living controllers.
    territories = {}
    resources = {}
    for label in labels:
        s = controlled[label]
        idx = sim.settlements.index(s) if s in sim.settlements else -1
        if s.is_alive:
            territories[label] = int((sim.world.ownership == idx).sum())
            inv = s.resource_inventory
            resources[label] = (
                s.food_stock
                + inv.get("wood", 0.0)
                + inv.get("stone", 0.0)
                + inv.get("metal", 0.0)
            )
        else:
            territories[label] = 0
            resources[label] = 0.0
    total_terr = sum(territories.values()) or 1
    total_res = sum(resources.values()) or 1.0

    per_controller: dict[str, ControllerMetrics] = {}
    for label in labels:
        s = controlled[label]
        idx = sim.settlements.index(s) if s in sim.settlements else -1
        per_controller[label] = ControllerMetrics(
            label=label,
            survival_ticks=stats[label]["survived_ticks"],
            peak_population=stats[label]["peak"],
            final_population=s.population,
            territory=territories[label],
            territory_share=round(territories[label] / total_terr, 4),
            resource_total=round(resources[label], 1),
            resource_share=round(resources[label] / total_res, 4),
            buildings=(sum(sim.buildings_of(s).values())
                       if s.is_alive else 0),
            routes_established=s.routes_established,
            cumulative_reward=round(stats[label]["reward"], 3),
        )
    return {
        "seed": seed,
        "ticks_run": ticks,
        "per_controller": {k: asdict(v) for k, v in per_controller.items()},
    }


def determine_winner(per_controller: dict) -> tuple[str, str]:
    """Winner by survival ticks, tie-broken by territory share. Accepts
    ControllerMetrics objects or their asdict() dicts. Returns
    (winner_label, reason)."""
    def field(m, name):
        return m[name] if isinstance(m, dict) else getattr(m, name)

    ranked = sorted(
        per_controller.values(),
        key=lambda m: (-field(m, "survival_ticks"),
                       -field(m, "territory_share")),
    )
    best = ranked[0]
    runner = ranked[1]
    if field(best, "survival_ticks") > field(runner, "survival_ticks"):
        return field(best, "label"), "survival"
    if field(best, "territory_share") > field(runner, "territory_share"):
        return field(best, "label"), "territory share"
    return field(best, "label"), "tie"


def head_to_head_eval(
    model_path_a: str,
    model_path_b: str,
    num_worlds: int = 10,
    first_seed: int = 50_000,
    ticks: int = 3000,
    size: int = 256,
    num_settlements: int | None = None,
    disaster_mult: float = 1.0,
    gather_mult: float = 1.0,
) -> dict:
    """Paired head-to-head: A vs B on identical seeds across many worlds.
    Returns win counts (by survival and territory share), mean metric
    deltas, and a permutation-test p-value on the reward difference."""
    from stable_baselines3 import PPO

    model_a = PPO.load(model_path_a, device="cpu")
    model_b = PPO.load(model_path_b, device="cpu")

    a_wins = b_wins = ties = 0
    a_rewards, b_rewards = [], []
    a_terr_shares, b_terr_shares = [], []
    a_surv, b_surv = [], []
    per_world = []

    for i in range(num_worlds):
        seed = first_seed + i
        result = run_head_to_head(
            models={"A": model_a, "B": model_b},
            seed=seed, size=size, num_settlements=num_settlements,
            ticks=ticks, disaster_mult=disaster_mult, gather_mult=gather_mult,
        )
        pc = result["per_controller"]
        winner, reason = determine_winner(pc)
        if winner == "A":
            a_wins += 1
        elif winner == "B":
            b_wins += 1
        else:
            ties += 1
        a_m, b_m = pc["A"], pc["B"]
        a_rewards.append(a_m["cumulative_reward"])
        b_rewards.append(b_m["cumulative_reward"])
        a_terr_shares.append(a_m["territory_share"])
        b_terr_shares.append(b_m["territory_share"])
        a_surv.append(a_m["survival_ticks"])
        b_surv.append(b_m["survival_ticks"])
        per_world.append({
            "seed": seed,
            "winner": winner,
            "reason": reason,
            **{f"A_{k}": v for k, v in a_m.items()},
            **{f"B_{k}": v for k, v in b_m.items()},
        })

    from worldsim.training import paired_permutation_pvalue

    reward_p = paired_permutation_pvalue(a_rewards, b_rewards)
    terr_p = paired_permutation_pvalue(a_terr_shares, b_terr_shares)

    def _mean(xs):
        return round(float(np.mean(xs)), 2) if xs else 0.0

    return {
        "worlds": num_worlds,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "mean_reward_a": _mean(a_rewards),
        "mean_reward_b": _mean(b_rewards),
        "mean_territory_share_a": _mean(a_terr_shares),
        "mean_territory_share_b": _mean(b_terr_shares),
        "reward_permutation_p": reward_p,
        "territory_permutation_p": terr_p,
        "results": per_world,
    }
