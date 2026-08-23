"""Sprint 30: paired rule-based vs LLM-advised comparison.

Same paired-per-seed methodology as Sprint 17's evaluate_vs_baseline:
world i uses seed = first_seed + i for BOTH arms; the baseline arm is a
pure rule-based run, the policy arm is an identical world where
settlement 0 is driven by LLMDrivenAgent (rule fallback + scheduled LLM
advice). Reward is measured with the same raw §6.4 component math on
both sides, so deltas isolate exactly one variable: does advice help?

Determinism property: with the LLM fully down, the LLMDrivenAgent's
fallback is byte-identical to the replaced RuleBasedAgent (same seed and
index), so both arms produce identical numbers — degradation is honest.

The returned dict matches evaluate_vs_baseline's shape so Sprint 17's
generate_report() works unchanged.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
from scipy.stats import wilcoxon

from .llm import LLMConfig, OllamaClient
from .reasoning import BackgroundAdvisor, ReasoningConfig
from .training import PairedResult, _run_baseline, paired_permutation_pvalue


def _run_llm_arm(seed: int, size: int, num_settlements: int, ticks: int,
                 client: OllamaClient,
                 advice_interval_ticks: int = 60,
                 disaster_mult: float = 1.0,
                 gather_mult: float = 1.0):
    """Identical to _run_baseline except settlement 0 is LLMDriven."""
    from .llm_agent import attach_llm_agent
    from .rewards import compute_reward_components, total_of
    from .simulation import Simulation
    from .world import World

    sim = Simulation(World(seed=seed, size=size),
                     disaster_chance_mult=disaster_mult,
                     gather_mult=gather_mult)
    settlements = sim.spawn_settlements(count=num_settlements)
    target = settlements[0]
    agent = attach_llm_agent(
        sim, target.id, client=client,
        config=ReasoningConfig(interval_ticks=advice_interval_ticks))
    assert agent is not None

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
        "advice_failures": agent.telemetry.advice_failures,
        "validated_actions": agent.telemetry.actions_validated,
    }
    return survived_ticks, peak, end, round(reward_total, 3)


def compare_llm_vs_baseline(
    num_worlds: int = 10,
    first_seed: int = 50_000,
    ticks: int = 600,
    size: int = 64,
    num_settlements: int = 3,
    disaster_mult: float = 1.0,
    gather_mult: float = 1.0,
    client: OllamaClient | None = None,
    advice_interval_ticks: int = 60,
) -> dict:
    """Paired worlds: pure rules vs rules + scheduled LLM advice.

    Returns evaluate_vs_baseline-shaped dict (report-compatible) plus an
    "advice" block summarizing live telemetry."""
    if client is None:
        client = OllamaClient(LLMConfig())

    results: list[PairedResult] = []
    total_validated = 0
    total_advice_failures = 0
    for i in range(num_worlds):
        seed = first_seed + i
        base_surv, base_peak, base_end, base_reward = _run_baseline(
            seed, size, num_settlements, ticks, disaster_mult, gather_mult)
        advisor = BackgroundAdvisor(client)
        try:
            pol_surv, pol_peak, pol_end, pol_reward = _run_llm_arm(
                seed, size, num_settlements, ticks, client,
                advice_interval_ticks=advice_interval_ticks,
                disaster_mult=disaster_mult, gather_mult=gather_mult)
        finally:
            advisor.shutdown(timeout=5.0)
        total_validated += pol_end.get("validated_actions", 0)
        total_advice_failures += pol_end.get("advice_failures", 0)
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

    wins = sum(1 for r in results
               if r.policy_survival_ticks > r.baseline_survival_ticks)
    ties = sum(1 for r in results
               if r.policy_survival_ticks == r.baseline_survival_ticks)
    reward_wins = sum(1 for r in results
                      if r.policy_reward > r.baseline_reward)
    decided = num_worlds - ties

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
            p_value = None
        perm_p = paired_permutation_pvalue(b_vals, p_vals)
        metrics[name] = {
            "baseline_mean": round(float(np.mean(b_vals)), 2),
            "policy_mean": round(float(np.mean(p_vals)), 2),
            "delta": round(float(np.mean(p_vals)) - float(np.mean(b_vals)),
                           2),
            "wilcoxon_p": p_value,
            "perm_p": perm_p,
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
            round(wins / decided, 3) if decided else None),
        "reward_win_fraction": round(reward_wins / num_worlds, 3),
        "hacking_telemetry": {"flagged_ticks": 0, "quarantined_runs": 0},
        "mean_baseline_survival": round(
            float(np.mean([r.baseline_survival_ticks for r in results])), 1),
        "mean_policy_survival": round(
            float(np.mean([r.policy_survival_ticks for r in results])), 1),
        "mean_baseline_peak_pop": float(np.mean(
            [r.baseline_peak_population for r in results])),
        "mean_policy_peak_pop": float(np.mean(
            [r.policy_peak_population for r in results])),
        "metrics": metrics,
        "results": [asdict(r) for r in results],
        "advice": {
            "interval_ticks": advice_interval_ticks,
            "validated_llm_actions": total_validated,
            "advice_failures": total_advice_failures,
            "degraded": total_validated == 0,
        },
    }


def verdict_text(results: dict) -> str:
    """Honest one-line verdict; never overstates significance."""
    advice = results.get("advice", {})
    if advice.get("degraded"):
        return ("NO ADVICE REACHED THE SIMULATION (all requests failed) — "
                "arms are effectively identical; verdict inconclusive.")
    sig = [name for name, m in results["metrics"].items()
           if m["wilcoxon_p"] is not None and m["wilcoxon_p"] < 0.05]
    better = [n for n in sig if results["metrics"][n]["delta"] > 0]
    worse = [n for n in sig if results["metrics"][n]["delta"] < 0]
    if not sig:
        return ("No statistically significant differences "
                "(Wilcoxon p < 0.05 on any metric).")
    parts = []
    if better:
        parts.append("LLM arm significantly BETTER at: " + ", ".join(better))
    if worse:
        parts.append("significantly WORSE at: " + ", ".join(worse))
    return "; ".join(parts) + "."
