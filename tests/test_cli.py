import sqlite3

from worldsim.cli import main


def test_simulate_no_save_deterministic(capsys):
    rc1 = main(["simulate", "--seed", "12345", "--ticks", "300", "--no-save"])
    out1 = capsys.readouterr().out
    assert rc1 == 0
    main(["simulate", "--seed", "12345", "--ticks", "300", "--no-save"])
    out2 = capsys.readouterr().out
    assert out1 == out2
    assert "pop" in out1
    assert "tick    300" in out1


def test_simulate_persists_final_state(tmp_path, capsys):
    db = tmp_path / "world.db"
    rc = main(["simulate", "--seed", "42", "--ticks", "120", "--db", str(db)])
    assert rc == 0
    conn = sqlite3.connect(db)
    try:
        worlds = conn.execute("SELECT COUNT(*) FROM worlds").fetchone()[0]
        snaps = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        settle = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    finally:
        conn.close()
    assert (worlds, snaps, settle) == (1, 1, 1)


def test_simulate_reports_growth(capsys):
    main(["simulate", "--seed", "99", "--ticks", "200", "--report-interval", "100",
          "--no-save"])
    out = capsys.readouterr().out
    # With a food-rich spawn the population should grow within 200 ticks.
    lines = [ln for ln in out.splitlines() if "pop" in ln]
    pops = [int(ln.split("pop")[1].split("|")[0]) for ln in lines]
    assert pops[-1] > pops[0]


def test_generate_still_works(capsys):
    rc = main(["generate", "--seed", "5", "--size", "16", "--no-save"])
    assert rc == 0
