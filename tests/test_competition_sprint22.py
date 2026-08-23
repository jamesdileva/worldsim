"""Sprint 22: self-play / head-to-head civilization competition."""

import json

import pytest


@pytest.fixture(scope="module")
def two_models(tmp_path_factory):
    """Two distinct tiny trained policies, shared across tests."""
    from stable_baselines3 import PPO

    from worldsim.training import train

    out_dir = tmp_path_factory.mktemp("policies")
    paths = []
    for i, seed in enumerate((61, 62)):
        summary = train(
            total_timesteps=256, seed=seed,
            size=32, num_settlements=2, max_ticks=120,
            save_path=out_dir / f"pol_{i}",
            log_path=out_dir / f"log_{i}.jsonl",
            n_steps=64,
        )
        paths.append(summary["checkpoint_path"])
    models = [PPO.load(p, device="cpu") for p in paths]
    return paths, models


# ----------------------------------------------------------------------
# Runner mechanics
# ----------------------------------------------------------------------

def test_run_head_to_head_deterministic(two_models):
    from worldsim.competition import run_head_to_head

    paths, _ = two_models
    kwargs = dict(seed=5000, size=32, num_settlements=2, ticks=100)

    r1 = run_head_to_head(models={"A": paths[0], "B": paths[1]}, **kwargs)
    r2 = run_head_to_head(models={"A": paths[0], "B": paths[1]}, **kwargs)
    assert r1 == r2


def test_controllers_coexist_without_death_by_interference(two_models):
    from worldsim.competition import run_head_to_head

    paths, _ = two_models
    result = run_head_to_head(
        models={"A": paths[0], "B": paths[1]},
        seed=42, size=32, num_settlements=2, ticks=150,
    )
    a = result["per_controller"]["A"]
    b = result["per_controller"]["B"]
    # Both controllers survived the whole short run: no controller killed
    # the other through action interference.
    assert a["survival_ticks"] == 150
    assert b["survival_ticks"] == 150


def test_shares_sum_to_one(two_models):
    from worldsim.competition import run_head_to_head

    paths, _ = two_models
    result = run_head_to_head(
        models={"A": paths[0], "B": paths[1]},
        seed=77, size=32, num_settlements=3, ticks=120,
    )
    pc = result["per_controller"]
    terr_share = sum(m["territory_share"] for m in pc.values())
    res_share = sum(m["resource_share"] for m in pc.values())
    # Shares are computed across controllers only (third settlement is
    # rule-based and excluded), so they sum to ~1.
    assert terr_share == pytest.approx(1.0)
    assert res_share == pytest.approx(1.0)


def test_more_settlements_than_controllers_supported(two_models):
    from worldsim.competition import run_head_to_head

    paths, _ = two_models
    result = run_head_to_head(
        models={"A": paths[0], "B": paths[1]},
        seed=5, size=32, num_settlements=4, ticks=80,
    )
    assert set(result["per_controller"].keys()) == {"A", "B"}


def test_requires_one_settlement_per_controller(two_models):
    from worldsim.competition import run_head_to_head

    paths, _ = two_models
    with pytest.raises(ValueError, match="one settlement per controller"):
        run_head_to_head(
            models={"A": paths[0], "B": paths[1]},
            seed=5, size=32, num_settlements=1, ticks=50,
        )


# ----------------------------------------------------------------------
# Winner determination & evaluation
# ----------------------------------------------------------------------

def test_determine_winner_survival_then_territory():
    from worldsim.competition import ControllerMetrics, determine_winner

    def m(label, surv, share):
        return ControllerMetrics(
            label=label, survival_ticks=surv, peak_population=10,
            final_population=10, territory=int(share * 100),
            territory_share=share, resource_total=100,
            resource_share=share, buildings=1, routes_established=0,
            cumulative_reward=1.0,
        )

    win, reason = determine_winner({
        "A": m("A", surv=3000, share=0.4),
        "B": m("B", surv=2500, share=0.6),
    })
    assert (win, reason) == ("A", "survival")

    # Survival tie -> territory share decides.
    win, reason = determine_winner({
        "A": m("A", surv=3000, share=0.35),
        "B": m("B", surv=3000, share=0.65),
    })
    assert (win, reason) == ("B", "territory share")


def test_head_to_head_eval_structure_and_persistence(
        tmp_path, two_models):
    from worldsim.competition import head_to_head_eval
    from worldsim.db import WorldStore

    paths, _ = two_models
    results = head_to_head_eval(
        model_path_a=paths[0],
        model_path_b=paths[1],
        num_worlds=2,
        first_seed=70000,
        ticks=120,
        size=32,
        num_settlements=2,
    )
    assert results["worlds"] == 2
    assert results["a_wins"] + results["b_wins"] + results["ties"] == 2
    assert {"reward_permutation_p", "territory_permutation_p"} <= set(results)
    json.dumps(results)  # SQLite storage path must serialize

    store = WorldStore(tmp_path / "runs.db")
    try:
        row_id = store.insert_training_run({
            "policy_generation_a": "genX",
            "policy_generation_b": "genY",
            "eval_seed_base": 70000,
            "worlds": 2,
            "wins_a": results["a_wins"],
            "ties": results["ties"],
            "win_fraction": results["a_wins"] / 2,
            "mean_survival_a": results["mean_reward_a"],
            "mean_survival_b": results["mean_reward_b"],
            "results_json": results,
            "agent_type": "head_to_head",
        })
        rows = store._conn.execute(
            "SELECT policy_generation_a, policy_generation_b, agent_type "
            "FROM training_runs WHERE id = ?",
            (row_id,),
        ).fetchall()
        assert rows[0] == ("genX", "genY", "head_to_head")
    finally:
        store.close()


def test_same_model_both_sides_is_symmetric(two_models):
    """Sanity: one policy on both sides should produce near-symmetric
    aggregate outcomes (no systematic A advantage)."""
    from worldsim.competition import head_to_head_eval

    paths, _ = two_models
    results = head_to_head_eval(
        model_path_a=paths[0],
        model_path_b=paths[0],
        num_worlds=2,
        first_seed=80000,
        ticks=100,
        size=32,
        num_settlements=2,
    )
    # With the same policy on both sides there is no systematic skill
    # advantage, but perfect symmetry is NOT expected: A acts first each
    # tick (turn-order advantage) and the two controllers occupy different
    # spawn sites with different terrain quality.
    assert abs(results["mean_reward_a"] - results["mean_reward_b"]) < 3.0
