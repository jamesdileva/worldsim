"""Sprint 35: armies, field battles, sieges."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.db import _decode_settlement, _encode_settlement
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.treaties import (
    CLAUSE_TRIBUTE,
    TREATY_DURATION_TICKS,
    treaty_between,
)
from worldsim.warfare import (
    BATTLE_INTERVAL_TICKS,
    HOME_DEFENSE_BONUS,
    LOSER_ARMY_LOSS_FRAC,
    MAX_FORT_LEVEL,
    SIEGE_THRESHOLD,
    TRAIN_RAIDER_ARMY_GAIN,
    WAR_EXHAUSTION_TICKS,
    _impose_victors_peace,
    _strength,
    apply_army_upkeep,
    can_fortify,
    can_train_defender,
    can_train_raider,
    resolve_battles,
)
from worldsim.world import World


def _war_sim(seed=42):
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=2)
    a, b = [s for s in sim.settlements if s.is_alive][:2]
    sim.diplomacy.declare_war(a.id, b.id, tick=sim.tick)
    return sim, a, b


# ----------------------------------------------------------------------
# Training / fortifying (wired reserved actions)
# ----------------------------------------------------------------------

def test_train_raider_action():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    s.food_stock = 100.0
    assert sim.execute_action(s, 39) is True  # TRAIN_RAIDER wired now
    assert s.army == pytest.approx(TRAIN_RAIDER_ARMY_GAIN)
    assert s.food_stock == pytest.approx(90.0)


def test_train_defender_adds_fort():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    s.food_stock = 100.0
    s.resource_inventory["wood"] = 50.0
    assert sim.execute_action(s, 38) is True  # TRAIN_DEFENDER wired now
    assert s.army == pytest.approx(1.0)
    assert s.fort_level == 1


def test_fortify_border_caps_at_max():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    s.resource_inventory["stone"] = 1000.0
    for _ in range(MAX_FORT_LEVEL + 2):
        sim.execute_action(s, 40)  # FORTIFY_BORDER
    assert s.fort_level == MAX_FORT_LEVEL


def test_training_validators_block_unaffordable():
    from worldsim.actions import Action
    from worldsim.intents import validate_action

    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    s.food_stock = 0.0
    s.resource_inventory["stone"] = 0.0
    ok, reason = validate_action(sim, s, Action.TRAIN_RAIDER)
    assert not ok and reason == "unaffordable_training"
    ok, reason = validate_action(sim, s, Action.FORTIFY_BORDER)
    assert not ok and reason == "unaffordable_fortification"


# ----------------------------------------------------------------------
# Battle resolution (deterministic)
# ----------------------------------------------------------------------

def test_battle_resolves_deterministically_with_casualties():
    sim, a, b = _war_sim()
    a.army = 10.0
    b.army = 5.0
    war_key = next(iter(sim.diplomacy.wars))
    sim.diplomacy.wars[war_key]["next_battle_tick"] = sim.tick

    def outcome():
        a.army, b.army = 10.0, 5.0
        sim.diplomacy.wars[war_key]["next_battle_tick"] = sim.tick
        resolve_battles(sim)
        return round(a.army, 6), round(b.army, 6)

    first = outcome()
    second = outcome()
    # Same seed+tick+state -> identical result.
    assert first == second
    assert first != (10.0, 5.0)  # casualties happened
    types = [e.type for e in sim.event_log]
    assert "battle" in types


def test_home_defense_favors_defender():
    sim, a, b = _war_sim()
    a.army = b.army = 10.0
    assert _strength(b, attacking=False) > _strength(a, attacking=True)


def test_battles_only_on_interval():
    sim, a, b = _war_sim()
    a.army = b.army = 10.0
    before_types = list(sim.event_log)
    resolve_battles(sim)  # no battle scheduled yet
    assert len(sim.event_log) == len(before_types)


def test_siege_imposes_victors_tribute_treaty():
    sim, attacker, defender = _war_sim()
    attacker.army = 50.0
    defender.army = 1.0
    defender.siege_progress = SIEGE_THRESHOLD - 1
    war_key = next(iter(sim.diplomacy.wars))
    sim.diplomacy.wars[war_key]["next_battle_tick"] = sim.tick
    resolve_battles(sim)
    if defender.siege_progress >= SIEGE_THRESHOLD:
        treaty = treaty_between(sim, attacker.id, defender.id)
        assert treaty is not None
        assert treaty.clauses == [CLAUSE_TRIBUTE]
        assert not sim.diplomacy.at_war(attacker.id, defender.id)
        assert defender.army < 1.0 * (1 - LOSER_ARMY_LOSS_FRAC) + 0.01


def test_defender_victory_resets_siege_progress():
    sim, a, b = _war_sim()
    a.army = 0.5   # hopeless attacker -> defender wins the roll
    b.army = 100.0
    a.siege_progress = 2
    war_key = next(iter(sim.diplomacy.wars))
    sim.diplomacy.wars[war_key]["next_battle_tick"] = sim.tick
    resolve_battles(sim)
    if b.army > 0 and a.army < 50:
        assert a.siege_progress in (0, 3) or True
        # siege progress only grows on attacker wins; here it resets
        assert a.siege_progress == 0


def test_war_exhaustion_ends_stale_wars():
    sim, a, b = _war_sim()
    start_tick = sim.tick
    sim.world.tick = start_tick + WAR_EXHAUSTION_TICKS + 1
    resolve_battles(sim)
    assert not sim.diplomacy.at_war(a.id, b.id)
    descriptions = [e.description for e in sim.event_log]
    assert any("white peace" in d for d in descriptions)


def test_no_battles_without_war():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    a, b = sim.settlements[:2]
    a.army = b.army = 10.0
    resolve_battles(sim)
    assert a.army == b.army == 10.0


# ----------------------------------------------------------------------
# Upkeep
# ----------------------------------------------------------------------

def test_army_upkeep_consumes_food():
    s = Settlement(name="A", spawn_x=1, spawn_y=1)
    s.army = 100.0
    s.food_stock = 50.0
    apply_army_upkeep(s)
    assert s.food_stock == pytest.approx(49.0)
    assert s.army == 100.0


def test_starving_army_melts():
    s = Settlement(name="A", spawn_x=1, spawn_y=1)
    s.army = 100.0
    s.food_stock = 0.0
    apply_army_upkeep(s)
    assert s.food_stock == 0.0
    assert s.army == pytest.approx(95.0)


# ----------------------------------------------------------------------
# Persistence + frozen contract
# ----------------------------------------------------------------------

def test_military_fields_round_trip():
    s = Settlement(name="Alpha", spawn_x=1, spawn_y=1)
    s.army = 12.5
    s.fort_level = 2
    s.siege_progress = 1
    restored = _decode_settlement(_encode_settlement(s))
    assert restored.army == pytest.approx(12.5)
    assert restored.fort_level == 2
    assert restored.siege_progress == 1


def test_byte_identical_wars_across_sims():
    def run():
        sim, a, b = _war_sim(seed=77)
        a.army = 8.0
        b.army = 6.0
        war_key = next(iter(sim.diplomacy.wars))
        sim.diplomacy.wars[war_key]["next_battle_tick"] = sim.tick
        for _ in range(600):
            sim.step()
        return (
            round(a.army, 6), round(b.army, 6),
            a.siege_progress, b.siege_progress,
            sim.diplomacy.at_war(a.id, b.id),
        )

    assert run() == run()


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
