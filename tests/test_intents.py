"""Sprint 28: intent mapping, validation, LLMDrivenAgent.

Live sims exercise validation against real mechanics; the LLM is always
a fake client (no network in fast tier)."""

import numpy as np
import pytest

from worldsim.actions import Action, NUM_ACTIONS
from worldsim.advice import AdviceResult, StrategicAdvice
from worldsim.agents import RAID_CADENCE_TICKS
from worldsim.buildings import BUILDING_SPECS, BuildingType
from worldsim.intents import (
    IntentTelemetry,
    map_advice_to_actions,
    validate_action,
)
from worldsim.llm import LLMResult
from worldsim.llm_agent import LLMDrivenAgent, attach_llm_agent
from worldsim.settlement import STARTING_RESOURCES, Settlement
from worldsim.simulation import Simulation
from worldsim.world import World


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------

@pytest.fixture
def sim_two_settlements():
    world = World(seed=42, size=64)
    sim = Simulation(world=world)
    sim.spawn_settlements(2)
    return sim


def _advice(*priorities: str) -> AdviceResult:
    return AdviceResult(ok=True,
                        advice=StrategicAdvice(list(priorities), "why"))


class FakeLLMClient:
    """Duck-typed OllamaClient returning canned output."""

    def __init__(self, result: LLMResult | Exception):
        self.result = result
        self.calls = 0

    def generate(self, prompt, system=None):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


GOOD_JSON = ('{"priorities": ["Build more farms", "Open trade routes"], '
             '"rationale": "Grow economy."}')


# ----------------------------------------------------------------------
# map_advice_to_actions
# ----------------------------------------------------------------------

def test_farm_phrase_maps_to_build_farm():
    actions = map_advice_to_actions(StrategicAdvice(["build more farms"], "r"))
    assert actions == [Action.BUILD_FARM]


@pytest.mark.parametrize("phrase, expected", [
    ("raid the weakest neighbor", Action.INITIATE_RAID),
    ("seek peace with enemies", Action.OFFER_PEACE),
    ("build a granary for storage", Action.BUILD_GRANARY),
    ("raise a sawmill to harvest timber", Action.BUILD_SAWMILL),
    ("open a mine for metal income", Action.BUILD_MINE),
    ("construct roads between tiles", Action.BUILD_ROAD),
    ("establish new trade routes", Action.ESTABLISH_TRADE_ROUTE),
    ("claim territory to the north", Action.CLAIM_TERRITORY),
    ("wait and consolidate", Action.WAIT),
])
def test_phrase_matrix(phrase, expected):
    assert map_advice_to_actions(StrategicAdvice([phrase], "r")) == [expected]


def test_dedup_preserves_first_occurrence_order():
    advice = StrategicAdvice(
        ["open trade routes", "build more farms", "another farm push"], "r")
    actions = map_advice_to_actions(advice)
    assert actions == [Action.ESTABLISH_TRADE_ROUTE, Action.BUILD_FARM]


def test_unmapped_phrases_counted_not_fatal():
    telemetry = IntentTelemetry()
    advice = StrategicAdvice(["meditate on the stars",
                              "build more farms"], "r")
    actions = map_advice_to_actions(advice, telemetry=telemetry)
    assert actions == [Action.BUILD_FARM]
    assert telemetry.phrases_seen == 2
    assert telemetry.phrases_mapped == 1
    assert telemetry.phrases_unmapped == 1


# ----------------------------------------------------------------------
# validate_action (real sim mechanics)
# ----------------------------------------------------------------------

def test_build_farm_valid_at_start(sim_two_settlements):
    s = sim_two_settlements.settlements[0]
    ok, reason = validate_action(sim_two_settlements, s, Action.BUILD_FARM)
    assert ok, reason


def test_build_farm_unaffordable_after_drain(sim_two_settlements):
    s = sim_two_settlements.settlements[0]
    s.resource_inventory.update({"wood": 0.0, "stone": 0.0})
    ok, reason = validate_action(sim_two_settlements, s, Action.BUILD_FARM)
    assert not ok
    assert reason == "unaffordable_farm"


def test_claim_invalid_when_fully_surrounded(sim_two_settlements):
    sim = sim_two_settlements
    s = sim.settlements[0]
    idx = sim.settlements.index(s)
    size = sim.world.size
    # territory_of yields (y, x) pairs.
    for ty, tx in sim.territory_of(s):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = tx + dx, ty + dy
                if 0 <= nx < size and 0 <= ny < size:
                    if sim.world.ownership[ny, nx] == -1:
                        sim.world.ownership[ny, nx] = idx
    ok, reason = validate_action(sim, s, Action.CLAIM_TERRITORY)
    assert not ok
    assert reason == "no_unowned_adjacent"


def test_trade_invalid_without_neighbors():
    world = World(seed=5, size=64)
    solo = Simulation(world=world)
    solo.spawn_settlements(1)
    ok, reason = validate_action(solo, solo.settlements[0],
                                 Action.ESTABLISH_TRADE_ROUTE)
    assert not ok
    assert reason == "no_valid_trade_partner"


def test_trade_valid_with_neighbor(sim_two_settlements):
    s = sim_two_settlements.settlements[0]
    ok, reason = validate_action(sim_two_settlements, s,
                                 Action.ESTABLISH_TRADE_ROUTE)
    assert ok, reason


def test_raid_blocked_by_cadence(sim_two_settlements):
    sim = sim_two_settlements
    s = sim.settlements[0]
    sim.last_raid_tick[s.id] = sim.tick  # raided just now
    ok, reason = validate_action(sim, s, Action.INITIATE_RAID)
    assert not ok
    assert reason == "raid_cadence"


def test_peace_invalid_when_not_at_war(sim_two_settlements):
    s = sim_two_settlements.settlements[0]
    ok, reason = validate_action(sim_two_settlements, s, Action.OFFER_PEACE)
    assert not ok
    assert reason == "not_at_war"


@pytest.mark.parametrize("action", [
    Action.WAIT, Action.IDLE, Action.BOOST_MORALE,
])
def test_always_valid_actions(sim_two_settlements, action):
    ok, _ = validate_action(sim_two_settlements,
                            sim_two_settlements.settlements[0], action)
    assert ok


# ----------------------------------------------------------------------
# LLMDrivenAgent
# ----------------------------------------------------------------------

def _agent_with(client=None, **kwargs) -> tuple[Simulation, Settlement,
                                                LLMDrivenAgent]:
    world = World(seed=42, size=64)
    sim = Simulation(world=world)
    sim.spawn_settlements(2)
    s = sorted(sim.settlements, key=lambda x: x.name)[0]
    agent = attach_llm_agent(sim, s.id, client=client, **kwargs)
    return sim, s, agent


def test_valid_intent_overrides_rule_based():
    client = FakeLLMClient(LLMResult(ok=True, text=GOOD_JSON))
    sim, s, agent = _agent_with(client=client)
    obs = agent.observe(sim, s)
    action = agent.decide(obs)
    # first priority "build more farms" validated OK -> executed
    assert action == int(Action.BUILD_FARM)
    assert agent.telemetry.actions_validated == 1
    assert client.calls == 1


def test_server_failure_degrades_to_rules():
    client = FakeLLMClient(LLMResult(ok=False, error="unreachable"))
    sim, s, agent = _agent_with(client=client)
    action = agent.decide(agent.observe(sim, s))
    assert 0 <= action < NUM_ACTIONS
    assert agent.telemetry.fallback_decisions == 1
    assert agent.telemetry.advice_failures == 1


def test_garbage_output_degrades_to_rules():
    client = FakeLLMClient(LLMResult(ok=True,
                                     text="Have a wonderful day!"))
    sim, s, agent = _agent_with(client=client)
    action = agent.decide(agent.observe(sim, s))
    assert 0 <= action < NUM_ACTIONS
    assert agent.telemetry.fallback_decisions == 1


def test_provider_exception_never_crashes():
    client = FakeLLMClient(RuntimeError("boom"))
    sim, s, agent = _agent_with(client=client)
    action = agent.decide(agent.observe(sim, s))
    assert 0 <= action < NUM_ACTIONS
    assert agent.telemetry.advice_failures == 1


def test_invalid_intent_dropped_uses_fallback():
    # Raid intent while cadence blocks it: dropped with reason, fallback acts.
    client = FakeLLMClient(LLMResult(
        ok=True,
        text='{"priorities": ["raid the enemy"], "rationale": "war"}'))
    sim, s, agent = _agent_with(client=client)
    sim.last_raid_tick[s.id] = sim.tick
    action = agent.decide(agent.observe(sim, s))
    assert 0 <= action < NUM_ACTIONS
    assert agent.telemetry.fallback_decisions == 1
    assert agent.telemetry.drop_reasons.get("stale_raid_cadence") or \
        agent.telemetry.drop_reasons.get("raid_cadence")


def test_interval_prevents_refetch():
    client = FakeLLMClient(LLMResult(ok=True, text=GOOD_JSON))
    sim, s, agent = _agent_with(client=client, advice_interval_ticks=10)
    for _ in range(5):
        agent.decide(agent.observe(sim, s))
    assert client.calls == 1  # one request within the interval window


def test_pure_fallback_survives_full_episode():
    sim, s, agent = _agent_with(client=None)
    start_pop = s.population
    for _ in range(200):
        sim.step()
    assert sim.tick == 200
    assert agent.telemetry.fallback_decisions >= 200
    assert s.population >= 1  # alive; rule-based baseline never dies here
    assert agent.llm_active is False
    assert start_pop > 0


def test_attach_unknown_settlement_returns_none(sim_two_settlements):
    assert attach_llm_agent(sim_two_settlements, "nope") is None


def test_attach_keeps_index_alignment(sim_two_settlements):
    s = sorted(sim_two_settlements.settlements, key=lambda x: x.name)[0]
    idx = sim_two_settlements.settlements.index(s)
    original_other = sim_two_settlements.agents[1 - idx]
    agent = attach_llm_agent(sim_two_settlements, s.id)
    assert sim_two_settlements.agents[idx] is agent
    assert sim_two_settlements.agents[1 - idx] is original_other
