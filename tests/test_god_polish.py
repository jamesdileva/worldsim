"""Sprint 38: God Mode polish — surface completion, freeze, audit trail."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.db import _decode_settlement, _encode_settlement
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# §16.2: spawn settlements / freeze — new controls
# ----------------------------------------------------------------------

def test_god_spawn_settlement_registers_agent_and_logs():
    sim = _sim(n=2)
    count_before = len(sim.settlements)
    agents_before = len(sim.agents)
    _before, after = sim.god_spawn_settlement(30, 30)
    assert after["settlements"] == count_before + 1
    assert len(sim.settlements) == count_before + 1
    # agent registered and index-aligned
    assert len(sim.agents) > agents_before
    spawned = sim.settlements[-1]
    assert sim.agents[sim.settlements.index(spawned)] is not None


def test_god_spawn_settlement_deterministic_name():
    sim1, sim2 = _sim(n=2), _sim(n=2)
    _b1, a1 = sim1.god_spawn_settlement(30, 30)
    _b2, a2 = sim2.god_spawn_settlement(30, 30)
    assert a1["name"] == a2["name"]


def test_god_spawn_explicit_name_respected():
    sim = _sim(n=2)
    _before, after = sim.god_spawn_settlement(31, 31, name="Eden")
    assert after["name"] == "Eden"
    assert sim.settlements[-1].name == "Eden"


def test_freeze_halts_time_for_the_settlement():
    sim = _sim(n=1)
    s = sim.settlements[0]
    s.food_stock = 1000.0
    pop_before = s.population
    food_before = s.food_stock
    before, after = sim.god_toggle_freeze(s)
    assert (before["frozen"], after["frozen"]) == (False, True)
    for _ in range(50):
        sim.step()
    assert s.population == pop_before  # no growth while frozen
    assert s.food_stock == food_before  # no consumption/production
    assert s.is_alive


def test_unfreeze_resumes_time():
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_toggle_freeze(s)
    sim.god_toggle_freeze(s)  # unfreeze
    pop_before = s.population
    for _ in range(25):
        sim.step()
    assert s.population >= pop_before  # growth resumed
    assert not s.frozen


def test_frozen_survives_what_would_kill():
    """Freeze as absolute protection: no scarcity decay while frozen."""
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_toggle_freeze(s)
    s.resource_inventory.update({"wood": -500.0, "stone": -500.0})
    for _ in range(200):
        sim.step()
    assert s.is_alive  # frozen: counters never advance


def test_bless_happiness_restores_and_clears_misery():
    sim = _sim(n=1)
    s = sim.settlements[0]
    s.happiness = 0.05
    s.negative_food_streak = 99
    s.low_happiness_progress = 55
    before, after = sim.god_bless_happiness(s)
    assert before["happiness"] == pytest.approx(0.05)
    assert s.happiness == pytest.approx(1.0)
    assert s.negative_food_streak == 0
    assert s.low_happiness_progress == 0


# ----------------------------------------------------------------------
# §16.3: divine audit trail on EVERY intervention
# ----------------------------------------------------------------------

@pytest.mark.parametrize("invoke", [
    lambda sim: sim.god_smite(sim.settlements[0], 1),
    lambda sim: sim.god_bless_resources(sim.settlements[0], "food", 5),
    lambda sim: sim.god_destroy_improvement(10, 10),
    lambda sim: sim.god_spawn_settlement(30, 30),
    lambda sim: sim.god_toggle_freeze(sim.settlements[0]),
    lambda sim: sim.god_bless_happiness(sim.settlements[0]),
])
def test_every_god_action_leaves_divine_event(invoke):
    sim = _sim(n=2)
    invoke(sim)
    divine = [e for e in sim.event_log if e.type == "divine"]
    assert len(divine) == 1
    assert divine[0].description.startswith("GOD: ")


def test_destroy_improvement_on_empty_tile_is_still_audited():
    sim = _sim(n=1)
    sim.god_destroy_improvement(5, 5)
    divine = [e for e in sim.event_log if e.type == "divine"]
    assert len(divine) == 1


# ----------------------------------------------------------------------
# Persistence + frozen contract
# ----------------------------------------------------------------------

def test_frozen_flag_round_trips():
    s = Settlement(name="Alpha", spawn_x=1, spawn_y=1)
    s.frozen = True
    restored = _decode_settlement(_encode_settlement(s))
    assert restored.frozen is True


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60


# ----------------------------------------------------------------------
# CLI guardrails (§16.4 confirmation)
# ----------------------------------------------------------------------

def test_cli_parser_has_new_actions_and_force_flag():
    from worldsim.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "god", "--world-id", "w", "--action", "spawn_settlement",
        "--x", "10", "--y", "10",
    ])
    assert args.action == "spawn_settlement"
    args2 = parser.parse_args([
        "god", "--world-id", "w", "--action", "smite",
        "--amount", "99", "--force",
    ])
    assert args2.force is True
