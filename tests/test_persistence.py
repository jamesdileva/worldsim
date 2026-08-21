import sqlite3

import numpy as np
import pytest

from worldsim.clock import (
    TICKS_PER_SEASON,
    TICKS_PER_YEAR,
    describe,
    season_name,
    year_of,
)
from worldsim.cli import main
from worldsim.db import WorldStore
from worldsim.simulation import Simulation, simulation_from_state
from worldsim.world import World


# ----------------------------------------------------------------------
# Clock
# ----------------------------------------------------------------------

def test_clock_math():
    assert year_of(0) == 0
    assert year_of(511) == 0
    assert year_of(512) == 1
    assert season_name(0) == "spring"
    assert season_name(TICKS_PER_SEASON) == "summer"
    assert season_name(TICKS_PER_SEASON * 3) == "winter"
    assert season_name(TICKS_PER_YEAR) == "spring"


def test_describe_format():
    text = describe(768)  # 6 seasons in -> autumn of year 1
    assert "year 1" in text and "autumn" in text and "tick 768" in text


def test_disasters_share_clock_constants():
    from worldsim.disasters import season_of

    assert season_of(600) == season_name(600)


# ----------------------------------------------------------------------
# Save / load / step CLI
# ----------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "world.db")


def test_save_then_load_round_trip(db, capsys):
    rc = main(["save", "--seed", "12345", "--ticks", "300",
               "--world-id", "abc123", "--db", db])
    assert rc == 0
    capsys.readouterr()
    rc = main(["load", "--world-id", "abc123", "--db", db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "tick 300" in out
    assert "settlements: 5 alive / 5 ever" in out


def test_load_restores_exact_state(db):
    main(["save", "--seed", "42", "--ticks", "200",
          "--world-id", "w-exact", "--db", db])
    store = WorldStore(db)
    try:
        loaded, settlements, *_ = (
            store.load_latest_snapshot("w-exact")
        )
        # Regenerate deterministically and compare.
        sim = Simulation(World(seed=42))
        sim.spawn_settlements(5)
        sim.run(200)
        np.testing.assert_array_equal(loaded.terrain, sim.world.terrain)
        np.testing.assert_array_equal(loaded.ownership, sim.world.ownership)
        np.testing.assert_array_equal(loaded.improvements, sim.world.improvements)
        assert [s.population for s in settlements] == [
            s.population for s in sim.settlements
        ]
    finally:
        store.close()


def test_step_advances_exactly_n_ticks(db, capsys):
    main(["save", "--seed", "7", "--ticks", "100",
          "--world-id", "w-step", "--db", db])
    capsys.readouterr()
    main(["step", "--world-id", "w-step", "--ticks", "1", "--db", db])
    out = capsys.readouterr().out
    assert "from tick 100 to 101" in out
    main(["step", "--world-id", "w-step", "--ticks", "10", "--db", db])
    out = capsys.readouterr().out
    assert "from tick 101 to 111" in out


def test_step_preserves_continuation_determinism(db):
    """Stepping a saved world must match an uninterrupted run."""
    main(["save", "--seed", "99", "--ticks", "150",
          "--world-id", "w-cont", "--db", db])
    main(["step", "--world-id", "w-cont", "--ticks", "150", "--db", db])

    sim = Simulation(World(seed=99))
    sim.spawn_settlements(5)
    sim.run(300)

    store = WorldStore(db)
    try:
        loaded, settlements, *_ = store.load_latest_snapshot("w-cont")
        assert loaded.tick == 300
        assert [s.population for s in settlements] == [
            s.population for s in sim.settlements
        ]
        np.testing.assert_array_equal(
            loaded.improvements, sim.world.improvements
        )
    finally:
        store.close()


def test_load_missing_world_fails(db):
    with pytest.raises(ValueError):
        store = WorldStore(db)
        try:
            store.load_latest_snapshot("nope")
        finally:
            store.close()


# ----------------------------------------------------------------------
# Auto-save
# ----------------------------------------------------------------------

def test_autosave_writes_snapshots_at_interval(tmp_path, capsys):
    db = str(tmp_path / "auto.db")
    rc = main(["simulate", "--seed", "12345", "--ticks", "1000",
               "--save-interval", "500", "--report-interval", "500",
               "--world-id", "w-auto", "--db", db])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[auto-save] tick 500" in out
    assert "[auto-save] tick 1000" in out
    conn = sqlite3.connect(db)
    try:
        ticks = [
            r[0]
            for r in conn.execute(
                "SELECT tick FROM snapshots WHERE world_id = 'w-auto' "
                "ORDER BY tick"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert ticks == [500, 1000]


def test_autosave_disabled_with_zero(db, capsys):
    rc = main(["simulate", "--seed", "12345", "--ticks", "600",
               "--save-interval", "0", "--report-interval", "600",
               "--world-id", "w-noauto", "--db", db])
    assert rc == 0
    capsys.readouterr()
    conn = sqlite3.connect(db)
    try:
        ticks = [
            r[0]
            for r in conn.execute(
                "SELECT tick FROM snapshots WHERE world_id = 'w-noauto'"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert ticks == [600]  # only the final save


# ----------------------------------------------------------------------
# God Mode
# ----------------------------------------------------------------------

def test_god_smite_logs_before_after(db, capsys):
    main(["save", "--seed", "42", "--ticks", "50",
          "--world-id", "w-god", "--db", db])
    capsys.readouterr()
    rc = main(["god", "--world-id", "w-god", "--action", "smite",
               "--settlement-index", "0", "--amount", "3", "--db", db])
    out = capsys.readouterr().out
    assert rc == 0
    assert "'smite'" in out or "smite" in out

    store = WorldStore(db)
    try:
        events = store.get_god_events("w-god")
        assert len(events) == 1
        e = events[0]
        assert e["action_type"] == "smite"
        assert e["before_state"]["population"] - 3 == e["after_state"]["population"]
    finally:
        store.close()


def test_god_bless_food(db, capsys):
    main(["save", "--seed", "42", "--ticks", "50",
          "--world-id", "w-bless", "--db", db])
    capsys.readouterr()
    rc = main(["god", "--world-id", "w-bless", "--action", "bless_food",
               "--amount", "100", "--db", db])
    assert rc == 0
    store = WorldStore(db)
    try:
        _, settlements, *_ = store.load_latest_snapshot("w-bless")
        events = store.get_god_events("w-bless")
        before = events[0]["before_state"]["food_stock"]
        after = events[0]["after_state"]["food_stock"]
        assert after == pytest.approx(before + 100)
        assert settlements[0].food_stock == pytest.approx(after)
    finally:
        store.close()


def test_god_destroy_improvement(db, capsys):
    main(["save", "--seed", "12345", "--ticks", "400",
          "--world-id", "w-destroy", "--db", db])
    capsys.readouterr()
    # Find any improved tile to destroy.
    store = WorldStore(db)
    try:
        world, *_ = store.load_latest_snapshot("w-destroy")
        improved = np.argwhere(world.improvements != -1)
        if len(improved) == 0:
            pytest.skip("no improvements to destroy")
        y, x = int(improved[0][0]), int(improved[0][1])
    finally:
        store.close()
    rc = main(["god", "--world-id", "w-destroy", "--action", "destroy",
               "--x", str(x), "--y", str(y), "--db", db])
    assert rc == 0
    store = WorldStore(db)
    try:
        world2, *_ = store.load_latest_snapshot("w-destroy")
        assert world2.improvements[y, x] == -1
        events = store.get_god_events("w-destroy")
        assert events[-1]["before_state"]["improvement"] != -1
        assert events[-1]["after_state"]["improvement"] == -1
    finally:
        store.close()


def test_god_smite_to_death_records_ruin(db, capsys):
    main(["save", "--seed", "42", "--ticks", "50",
          "--world-id", "w-smitedeath", "--db", db])
    capsys.readouterr()
    store = WorldStore(db)
    try:
        _, settlements, *_ = store.load_latest_snapshot("w-smitedeath")
        pop = settlements[0].population
    finally:
        store.close()
    rc = main(["god", "--world-id", "w-smitedeath", "--action", "smite",
               "--settlement-index", "0", "--amount", str(pop), "--db", db])
    assert rc == 0
    store = WorldStore(db)
    try:
        _, settlements2, _, ruins, *_ = store.load_latest_snapshot("w-smitedeath")
        assert not settlements2[0].is_alive
        assert len(ruins) >= 1
    finally:
        store.close()


# ----------------------------------------------------------------------
# simulation_from_state round-trip
# ----------------------------------------------------------------------

def test_simulation_from_state_continues():
    sim = Simulation(World(seed=5))
    sim.spawn_settlements(2)
    sim.run(100)
    store = WorldStore(":memory:")
    try:
        wid = store.save_world(
            sim.world,
            sim.settlements,
            trade_routes=sim.trade_routes,
            ruins=sim.ruins,
            disaster_events=sim.disaster_events,
        )
        state = store.load_latest_snapshot(wid)
    finally:
        store.close()
    resumed = simulation_from_state(*state)
    resumed.run(50)
    fresh = Simulation(World(seed=5))
    fresh.spawn_settlements(2)
    fresh.run(150)
    assert resumed.tick == fresh.tick
    assert [s.population for s in resumed.settlements] == [
        s.population for s in fresh.settlements
    ]
