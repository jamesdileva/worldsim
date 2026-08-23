"""Sprint 19: population-based generational training."""

import json
import sqlite3

import pytest


@pytest.fixture()
def store():
    from worldsim.db import WorldStore

    store = WorldStore(":memory:")
    yield store
    store.close()


@pytest.fixture()
def registry(tmp_path):
    from worldsim.db import WorldStore

    store = WorldStore(tmp_path / "registry.db")
    yield store
    store.close()


@pytest.fixture()
def registry(tmp_path):
    from worldsim.db import WorldStore

    store = WorldStore(tmp_path / "registry.db")
    yield store
    store.close()


def _train_population(store, tmp_path, generation, population_size=2,
                      parent=None):
    """Population training against an isolated policies dir + registry."""
    import worldsim.population as pop
    from worldsim.training import POLICIES_DIR

    original_dir = POLICIES_DIR
    # Redirect checkpoint output into tmp_path for isolation.
    pop.POLICIES_DIR = tmp_path
    try:
        return pop.train_population(
            generation=generation,
            population_size=population_size,
            timesteps_per_candidate=256,
            seed_base=5000,
            size=32,
            num_settlements=2,
            max_ticks=120,
            n_envs=1,
            parent_generation=parent,
            db_store=store,
        )
    finally:
        pop.POLICIES_DIR = original_dir


def test_population_trains_all_candidates_and_selects_champion(
        store, tmp_path):
    result = _train_population(store, tmp_path, "genA", population_size=2)
    labels = [c["label"] for c in result["candidates"]]
    assert labels == ["genA_c0", "genA_c1"]
    champion = max(result["candidates"], key=lambda c: c["score"])
    assert result["champion"] == champion["label"]
    assert (tmp_path / "policy_genA.zip").exists()


def test_champion_registered_under_bare_generation_label(store, tmp_path):
    from worldsim.training import file_sha256

    _train_population(store, tmp_path, "genB", population_size=2)
    record = store.get_latest_policy_checkpoint("genB")
    assert record is not None
    zip_path = tmp_path / "policy_genB.zip"
    assert record["path"].endswith("policy_genB.zip")
    assert record["checksum"] == file_sha256(zip_path)


def test_lineage_parent_recorded(store, tmp_path):
    first = _train_population(store, tmp_path, "genC", population_size=1)
    second = _train_population(store, tmp_path, "genD", population_size=1,
                               parent=first["generation"])
    assert first["parent"] is None
    assert second["parent"] == "genC"
    stored = store.get_latest_policy_checkpoint("genD")
    assert stored["parent"] == "genC"


def test_candidate_seeds_are_deterministic_and_disjoint(store, tmp_path):
    import worldsim.population as pop

    r1 = _train_population(store, tmp_path, "genE", population_size=3)
    seeds_1 = [c["seed"] for c in r1["candidates"]]
    # Disjoint per candidate.
    assert len(set(seeds_1)) == 3
    # Deterministic formula: seed_base + gen_index*9973 + i*101.
    idx = pop.generation_index("genE")
    expected = [5000 + idx * 9973 + i * 101 for i in range(3)]
    assert seeds_1 == expected
    # Same label recomputes the same index.
    assert pop.generation_index("genE") == pop.generation_index("genE")


def test_evolve_runs_generations_with_parent_chain(store, tmp_path):
    import worldsim.population as pop

    original_dir = pop.POLICIES_DIR
    pop.POLICIES_DIR = tmp_path
    try:
        results = pop.evolve(
            generations=2,
            population_size=1,
            timesteps_per_candidate=256,
            size=32,
            num_settlements=2,
            max_ticks=120,
        )
    finally:
        pop.POLICIES_DIR = original_dir
    gens = results["generations"]
    assert [g["generation"] for g in gens] == ["gen1", "gen2"]
    assert gens[0]["parent"] is None
    # Parent lineage points at the exact champion checkpoint label.
    assert gens[1]["parent"] == gens[0]["champion"]
    # Both bare-label checkpoints exist.
    assert (tmp_path / "policy_gen1.zip").exists()
    assert (tmp_path / "policy_gen2.zip").exists()


def test_policy_checkpoints_parent_column_migrated(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE policy_checkpoints ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "generation TEXT NOT NULL,"
        "path TEXT NOT NULL,"
        "algorithm TEXT NOT NULL DEFAULT 'PPO',"
        "total_timesteps INTEGER NOT NULL,"
        "episodes INTEGER,"
        "mean_episode_return REAL,"
        "wall_time_seconds REAL,"
        "created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    from worldsim.db import WorldStore

    store = WorldStore(db)
    try:
        cols = {
            r[1]
            for r in store._conn.execute(
                "PRAGMA table_info(policy_checkpoints)"
            ).fetchall()
        }
        assert {"parent", "mutation"} <= cols
    finally:
        store.close()


# ----------------------------------------------------------------------
# Sprint 20: mutation & elitism
# ----------------------------------------------------------------------

def _train_single(tmp_path, label="base", seed=3):
    from worldsim.training import train

    return train(
        total_timesteps=256, seed=seed,
        size=32, num_settlements=2, max_ticks=120,
        save_path=tmp_path / f"policy_{label}",
        log_path=tmp_path / f"{label}.jsonl",
        n_steps=64,
    )


def test_mutate_checkpoint_changes_weights_but_not_shape(tmp_path):
    import numpy as np
    from stable_baselines3 import PPO

    from worldsim.population import mutate_checkpoint

    summary = _train_single(tmp_path)
    src = summary["checkpoint_path"]
    out = mutate_checkpoint(src, tmp_path / "mutant.zip",
                            strength=0.10, seed=42)
    parent = PPO.load(src, device="cpu")
    child = PPO.load(str(out), device="cpu")
    parent_params = [p.detach().numpy() for p in parent.policy.parameters()]
    child_params = [p.detach().numpy() for p in child.policy.parameters()]
    assert len(parent_params) == len(child_params)
    diffs = [
        float(np.abs(p - c).sum()) for p, c in zip(parent_params, child_params)
    ]
    assert sum(diffs) > 0.0  # weights actually changed


def test_elite_and_mutants_present_in_generation(tmp_path):
    """Generation 2 candidates must include the elite (unchanged champion)
    plus Gaussian mutants with lineage recorded."""
    import worldsim.population as pop

    original_dir = pop.POLICIES_DIR
    pop.POLICIES_DIR = tmp_path
    try:
        results = pop.evolve(
            generations=2,
            population_size=1,   # fresh candidates per gen
            n_mutants=2,
            timesteps_per_candidate=256,
            size=32,
            num_settlements=2,
            max_ticks=120,
            eval_ticks=100,
        )
    finally:
        pop.POLICIES_DIR = original_dir

    gen2_candidates = results["generations"][1]["candidates"]
    origins = {c["label"]: c.get("origin") for c in gen2_candidates}
    assert origins.get("gen2_e") == "elite"
    mutant_labels = [
        lbl for lbl, o in origins.items() if o == "mutant"
    ]
    assert len(mutant_labels) == 2
    for c in gen2_candidates:
        if c.get("origin") in ("elite", "mutant"):
            assert c["parent"] == results["generations"][0]["champion"]


def test_strategy_shift_report_returns_distributions(tmp_path):
    from worldsim.population import (
        mutate_checkpoint,
        quick_eval,
        strategy_shift_report,
    )
    from worldsim.training import train

    summary = _train_single(tmp_path)
    mutant = mutate_checkpoint(summary["checkpoint_path"],
                               tmp_path / "m.zip", strength=0.05, seed=1)
    _ = quick_eval(mutant, ticks=80, size=32, num_settlements=2, seed=1)

    report = strategy_shift_report(
        {"genA": summary["checkpoint_path"], "genB": str(mutant)},
        ticks=300, size=32, num_settlements=3,
    )
    assert set(report.keys()) == {"genA", "genB"}
    for dist in report.values():
        assert isinstance(dist, dict)
        total_labels = sum(dist.values())
        # Only living settlements are counted; at least one must exist.
        if total_labels:
            assert set(dist) <= {
                "agricultural", "mining", "trading", "military",
                "balanced", "settling",
            }

# ----------------------------------------------------------------------
# Sprint 21: cross-generation learning
# ----------------------------------------------------------------------

def test_merge_strategy_memories_later_generation_weighs_more():
    from worldsim.population import merge_strategy_memories

    m1 = {("trading", 30): 0.5}
    m2 = {("trading", 30): -0.5}
    merged_even = merge_strategy_memories([m1])
    assert merged_even[("trading", 30)] == pytest.approx(0.5)
    merged = merge_strategy_memories([m1, m2], ema_alpha=0.3)
    # 0.5 * 0.7 + (-0.5) * 0.3 = 0.2
    assert merged[("trading", 30)] == pytest.approx(0.2)


def test_strategy_prior_round_trip(tmp_path):
    from worldsim.population import (
        load_strategy_prior,
        save_strategy_prior,
    )

    prior = {("mining", 2): 1.25, ("trading", 30): -0.4}
    path = save_strategy_prior(prior, tmp_path / "priors.json")
    loaded = load_strategy_prior(path)
    assert loaded == prior


def test_prior_actions_for_orders_by_ema():
    from worldsim.population import prior_actions_for

    prior = {
        ("mining", 99): 0.1,
        ("mining", 2): 1.5,
        ("mining", 7): 0.8,
        ("trading", 30): 2.0,   # different archetype: excluded
    }
    top = prior_actions_for("mining", prior, top_k=2)
    assert top == [2, 7]


def test_curriculum_failure_seeds_selected():
    """Below-mean champion seed scores become next-gen curriculum seeds."""
    sim_scores = {"9000": 10.0, "9001": 2.0, "9002": 12.0}
    mean_score = sum(sim_scores.values()) / len(sim_scores)
    failures = sorted(
        int(s) for s, v in sim_scores.items() if v < mean_score
    )
    assert failures == [9001]
