import sqlite3

import pytest

from worldsim.cli import main


def test_generate_no_save_outputs_stats_and_map(capsys):
    rc = main(["generate", "--seed", "12345", "--no-save"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "seed=12345" in out
    assert "Terrain breakdown:" in out
    for name in ["WATER", "DESERT", "PLAINS", "FERTILE", "FOREST", "MOUNTAIN"]:
        assert name in out
    assert "ASCII map:" in out


def test_generate_persists_world(tmp_path, capsys):
    db = tmp_path / "world.db"
    rc = main(["generate", "--seed", "42", "--db", str(db)])
    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        seeds = [r[0] for r in conn.execute("SELECT seed FROM worlds").fetchall()]
        snaps = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()
    assert seeds == [42]
    assert snaps == 1


def test_same_seed_identical_output(capsys):
    main(["generate", "--seed", "999", "--no-save"])
    out1 = capsys.readouterr().out
    main(["generate", "--seed", "999", "--no-save"])
    out2 = capsys.readouterr().out
    assert out1 == out2


def test_custom_size(capsys):
    rc = main(["generate", "--seed", "5", "--size", "16", "--no-save"])
    assert rc == 0
