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
    champion = max(result["candidates"], key=lambda c: c["mean_return"])
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
    assert gens[1]["parent"] == "gen1"
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
        assert "parent" in cols
    finally:
        store.close()
