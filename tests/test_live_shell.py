"""Sprint 51: interactive living-world shell."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.db import WorldStore
from worldsim.live import WorldShell, start_live_shell


@pytest.fixture
def shell(tmp_path):
    store = WorldStore(tmp_path / "w.db")
    sh = WorldShell(store=store, stdout=__import__("io").StringIO())
    yield sh
    store.close()


def run(shell, *commands):
    for command in commands:
        shell.onecmd(command)


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------

def test_new_creates_world_and_updates_prompt(shell):
    run(shell, "new alpha --seed 42")
    assert shell.sim is not None
    assert shell.world_id == "alpha"
    assert len(shell.sim.settlements) == 3
    assert shell.prompt.startswith("alpha@t")


def test_commands_without_world_print_hint(shell):
    shell.onecmd("step 5")
    assert shell.sim is None  # nothing crashed; hint printed to stdout


def test_save_then_load_round_trip(shell):
    run(shell, "new beta --seed 42", "step 30", "save")
    wid = shell.world_id
    pop_at_save = sum(s.population for s in shell.sim.settlements)
    run(shell, "new gamma --seed 7")          # switch away
    run(shell, f"load {wid}")
    assert sum(s.population for s in shell.sim.settlements) == pop_at_save
    assert shell.sim.tick == 30


# ----------------------------------------------------------------------
# Time control + inspection smoke
# ----------------------------------------------------------------------

def test_step_advances_ticks(shell):
    run(shell, "new --seed 1", "step 25")
    assert shell.sim.tick == 25


def test_status_and_map_run_without_error(shell, capsys):
    run(shell, "new --seed 2", "step 10",
        "map 0 0 20 20", "panels", "chronicle", "timeline")
    out = capsys.readouterr().out
    assert "tick" in out
    assert "legend" in out
    assert "Chronicle" in out or "alive" in out


# ----------------------------------------------------------------------
# God commands share CLI semantics (incl. undo)
# ----------------------------------------------------------------------

def test_smite_kills_population_and_undo_restores(shell):
    run(shell, "new --seed 42")
    name = sorted(
        (s for s in shell.sim.settlements if s.is_alive),
        key=lambda s: s.name)[0].name
    before_pop = next(
        s.population for s in shell.sim.settlements if s.name == name)
    # Small smite: no confirmation needed (<25).
    run(shell, f"smite {name} 3")
    assert next(
        s.population for s in shell.sim.settlements
        if s.name == name) == before_pop - 3
    run(shell, "undo")  # restores a NEW sim; resolve by name again
    assert next(
        s.population for s in shell.sim.settlements
        if s.name == name) == before_pop


def test_big_smite_requires_confirmation(shell, monkeypatch):
    run(shell, "new --seed 42")
    victim = sorted(
        (s for s in shell.sim.settlements if s.is_alive),
        key=lambda s: s.name)[0]
    before_pop = victim.population
    monkeypatch.setattr("builtins.input", lambda *_: "n")  # decline
    run(shell, f"smite {victim.name} 50")
    assert victim.population == before_pop  # cancelled
    monkeypatch.setattr("builtins.input", lambda *_: "y")  # accept
    run(shell, f"smite {victim.name} 50")
    assert victim.population < before_pop


def test_nuke_always_confirms_and_leaves_zone(shell, monkeypatch):
    from worldsim.disasters import CONTAMINATION_TICKS

    run(shell, "new --seed 42")
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    run(shell, "nuke 30 30")
    assert len(shell.sim.contamination_zones) == 1
    zone = shell.sim.contamination_zones[0]
    assert zone.end_tick - zone.start_tick == CONTAMINATION_TICKS


def test_bless_food_increases_stock(shell):
    run(shell, "new --seed 42")
    target = sorted(
        (s for s in shell.sim.settlements if s.is_alive),
        key=lambda s: s.name)[0]
    before = target.food_stock
    run(shell, f"bless {target.name} food 100")
    assert target.food_stock > before


def test_spawn_adds_settlement_with_agent(shell):
    run(shell, "new --seed 42")
    count = len(shell.sim.settlements)
    agents = len(shell.sim.agents)
    run(shell, "spawn 32 32 Eden")
    assert len(shell.sim.settlements) == count + 1
    assert len(shell.sim.agents) >= agents


def test_freeze_toggles(shell):
    run(shell, "new --seed 42")
    target = sorted(shell.sim.settlements, key=lambda s: s.name)[0]
    run(shell, f"freeze {target.name}")
    assert target.frozen is True
    run(shell, f"freeze {target.name}")
    assert target.frozen is False


def test_undo_captures_persisted_for_saved_worlds(shell):
    run(shell, "new delta --seed 42", "save")
    victim = sorted(shell.sim.settlements, key=lambda s: s.name)[0]
    run(shell, f"smite {victim.name} 1")
    assert shell.store.count_undo_points("delta") == 1


# ----------------------------------------------------------------------
# Branching + contract
# ----------------------------------------------------------------------

def test_branch_saves_coexisting_world(shell):
    run(shell, "new origin --seed 42", "save")
    run(shell, "branch evil-timeline")
    assert shell.store.world_exists("evil-timeline")
    assert shell.store.world_exists("origin")


def test_unknown_command_reports(shell, capsys):
    shell.onecmd("frobnicate 1 2 3")
    out = capsys.readouterr().out
    assert "unknown command" in out


def test_start_live_shell_returns_configured_shell(tmp_path):
    store = WorldStore(tmp_path / "w.db")

    class InstantQuit(WorldShell):
        def cmdloop(self, intro=None):
            return  # don't block the test

    import worldsim.live as live_mod

    original = live_mod.WorldShell
    live_mod.WorldShell = InstantQuit
    try:
        shell = start_live_shell(store=store)
        assert isinstance(shell, WorldShell)
    finally:
        live_mod.WorldShell = original
    store.close()


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
