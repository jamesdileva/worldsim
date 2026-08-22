"""Sprint 16: policy versioning, checksums, registry resolution, rollback."""

import hashlib
import sqlite3

import pytest

from worldsim.db import WorldStore
from worldsim.training import (
    file_sha256,
    register_checkpoint,
    resolve_policy_path,
    verify_policy_checksum,
)


@pytest.fixture()
def store():
    store = WorldStore(":memory:")
    yield store
    store.close()


def _train_and_register(store, tmp_path, generation, seed=5):
    from worldsim.training import train

    summary = train(
        total_timesteps=256, seed=seed,
        size=32, num_settlements=2, max_ticks=150,
        save_path=tmp_path / f"policy_{generation}",
        log_path=tmp_path / f"log_{generation}.jsonl",
        n_steps=64,
    )
    return register_checkpoint(
        store, generation, tmp_path / f"policy_{generation}", summary
    )


# ----------------------------------------------------------------------
# Checksums
# ----------------------------------------------------------------------

def test_file_sha256_deterministic(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"checkpoint-bytes-123")
    h1 = file_sha256(f)
    h2 = file_sha256(f)
    assert h1 == h2
    assert len(h1) == 64
    f.write_bytes(b"different")
    assert file_sha256(f) != h1


def test_verify_policy_checksum():
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        Path_write(path, b"model-weights")
        good = file_sha256(path)
        assert verify_policy_checksum(path, good)
        assert not verify_policy_checksum(path, "deadbeef")
        # Legacy records without a stored checksum skip verification.
        assert verify_policy_checksum(path, None)
        assert not verify_policy_checksum("missing.zip", good)
    finally:
        os.remove(path)


def Path_write(path, data: bytes):
    with open(path, "wb") as fh:
        fh.write(data)


# ----------------------------------------------------------------------
# Registry: registration + resolution + corruption detection
# ----------------------------------------------------------------------

def test_register_computes_checksum_and_size(store, tmp_path):
    from worldsim.training import train

    summary = train(
        total_timesteps=256, seed=3, size=32, num_settlements=2,
        max_ticks=150, save_path=tmp_path / "pol",
        log_path=tmp_path / "log.jsonl", n_steps=64,
    )
    record = register_checkpoint(
        store, "gen1", tmp_path / "pol", summary
    )
    zip_path = tmp_path / "pol.zip"
    expected_hash = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert record["checksum"] == expected_hash
    assert record["size_bytes"] == zip_path.stat().st_size
    stored = store.get_latest_policy_checkpoint("gen1")
    assert stored["checksum"] == expected_hash


def test_resolve_by_generation_uses_latest(store, tmp_path):
    _train_and_register(store, tmp_path, "gen1", seed=11)
    first = store.get_latest_policy_checkpoint("gen1")
    _train_and_register(store, tmp_path, "gen1", seed=12)
    second = store.get_latest_policy_checkpoint("gen1")
    assert first["id"] < second["id"]
    resolved, record = resolve_policy_path(store, "gen1")
    assert resolved == second["path"]
    assert record["id"] == second["id"]


def test_resolve_unknown_generation_raises(store):
    with pytest.raises(ValueError, match="No registered checkpoint"):
        resolve_policy_path(store, "gen999")


def test_corruption_detected_on_resolution(store, tmp_path):
    _train_and_register(store, tmp_path, "gen1")
    zip_path = next((tmp_path.glob("policy_gen1*.zip")))
    # Corrupt one byte in the middle.
    data = bytearray(zip_path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    zip_path.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="corruption detected"):
        resolve_policy_path(store, "gen1")


def test_explicit_model_path_bypasses_registry(store):
    resolved, record = resolve_policy_path(
        store, "whatever", explicit_path="some/model.zip"
    )
    assert resolved == "some/model.zip"
    assert record is None


# ----------------------------------------------------------------------
# Migration & training_runs
# ----------------------------------------------------------------------

def test_migration_adds_checksum_columns(tmp_path):
    """A pre-Sprint-16 database gets the new columns via guarded ALTER."""
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

    store = WorldStore(db)
    try:
        cols = {
            r[1]
            for r in store._conn.execute(
                "PRAGMA table_info(policy_checkpoints)"
            ).fetchall()
        }
        assert {"checksum", "size_bytes"} <= cols
    finally:
        store.close()


def test_training_runs_round_trip(store):
    row_id = store.insert_training_run({
        "policy_generation_a": "gen2",
        "policy_generation_b": None,
        "eval_seed_base": 50000,
        "worlds": 10,
        "wins_a": 7,
        "ties": 3,
        "win_fraction": 0.7,
        "mean_survival_a": 2950.0,
        "mean_survival_b": 3000.0,
        "results_json": {"note": "per-world detail"},
    })
    rows = store._conn.execute(
        "SELECT policy_generation_a, wins_a, win_fraction, results_json "
        "FROM training_runs WHERE id = ?",
        (row_id,),
    ).fetchall()
    import json

    assert rows[0][0] == "gen2"
    assert rows[0][1] == 7
    assert rows[0][2] == pytest.approx(0.7)
    assert json.loads(rows[0][3])["note"] == "per-world detail"


# ----------------------------------------------------------------------
# Rollback determinism
# ----------------------------------------------------------------------

def test_rollback_produces_identical_results(store, tmp_path):
    """Loading gen1 by registry id and evaluating twice on the same seeds
    yields identical results (Sprint 16 rollback acceptance)."""
    from worldsim.training import evaluate_vs_baseline, train

    _train_and_register(store, tmp_path, "gen1")
    kwargs = dict(
        model_path=None, model_unused=None, worlds=1, first_seed=60000,
        ticks=120, size=32, num_settlements=2,
    )
    resolved, _ = resolve_policy_path(store, "gen1")

    run_a = evaluate_vs_baseline(
        model_path=resolved, num_worlds=1, first_seed=60000,
        ticks=120, size=32, num_settlements=2,
    )
    run_b = evaluate_vs_baseline(
        model_path=resolved, num_worlds=1, first_seed=60000,
        ticks=120, size=32, num_settlements=2,
    )
    assert run_a["results"] == run_b["results"]
