"""Sprint 29: reasoning scheduler + background advisor.

All LLM clients are fakes; timing assertions use generous bounds."""

import json
import threading
import time

import pytest

from worldsim.actions import Action
from worldsim.advice import AdviceResult
from worldsim.llm import LLMResult
from worldsim.llm_agent import attach_llm_agent
from worldsim.reasoning import (
    BackgroundAdvisor,
    ReasoningConfig,
    is_struggling,
    prioritize,
    should_reason,
    struggle_score,
)
from worldsim.simulation import WorldEvent
from worldsim.simulation import Simulation
from worldsim.world import World


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _sim(seed=42, n=2) -> Simulation:
    world = World(seed=seed, size=64)
    sim = Simulation(world=world)
    sim.spawn_settlements(n)
    return sim


def _struggle(sim: Simulation, index: int = 0) -> None:
    s = sim.settlements[index]
    s.food_stock = 1.0
    s.happiness = 0.05
    s.net_food_rate = -2.0


GOOD = LLMResult(ok=True, text=json.dumps(
    {"priorities": ["build more farms"], "rationale": "food"}))


class FakeClient:
    def __init__(self, result=GOOD, delay=0.0, raise_exc=None):
        self.result = result
        self.delay = delay
        self.raise_exc = raise_exc
        self.calls = 0

    def generate(self, prompt, system=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        return self.result


# ----------------------------------------------------------------------
# Trigger matrix
# ----------------------------------------------------------------------

def test_interval_first_advice_always_due():
    sim = _sim()
    due, why = should_reason(ReasoningConfig(interval_ticks=24),
                             sim, sim.settlements[0], None)
    assert due and why == "first_advice"


def test_interval_due_at_threshold_not_before():
    from types import SimpleNamespace
    s = object()  # unused by interval mode
    cfg = ReasoningConfig(interval_ticks=10)
    sim5 = SimpleNamespace(tick=5, event_log=[])
    assert not should_reason(cfg, sim5, s, 0)[0]
    sim10 = SimpleNamespace(tick=10, event_log=[])
    due, why = should_reason(cfg, sim10, s, 0)
    assert due and why == "interval"


def test_interval_disabled_never_fires():
    sim = _sim()
    cfg = ReasoningConfig(interval_ticks=None)
    due, _ = should_reason(cfg, sim, sim.settlements[0],
                           last_reasoned_tick=sim.tick - 1000)
    assert not due


def test_event_mode_important_event_triggers():
    sim = _sim()
    s = sim.settlements[0]
    sim.event_log.append(
        WorldEvent(tick=sim.tick - 5, type="raid", actor_ids=[s.id],
                   description="raided"))
    due, why = should_reason(
        ReasoningConfig(interval_ticks=None, on_events=True),
        sim, s, last_reasoned_tick=sim.tick - 10)
    assert due and why == "event"


def test_event_mode_unrelated_event_does_not():
    sim = _sim()
    s = sim.settlements[0]
    other = sim.settlements[1]
    sim.event_log.append(
        WorldEvent(tick=sim.tick - 5, type="raid", actor_ids=[other.id],
                   description="raided someone else"))
    sim.event_log.append(
        WorldEvent(tick=sim.tick - 4, type="trade_route", actor_ids=[s.id],
                   description="routine trade"))
    due, _ = should_reason(
        ReasoningConfig(interval_ticks=None, on_events=True),
        sim, s, last_reasoned_tick=sim.tick - 10)
    assert not due


def test_struggling_mode_targets_bad_shape_only():
    sim = _sim()
    cfg = ReasoningConfig(interval_ticks=None, struggling_only=True)
    healthy = sim.settlements[0]
    assert not should_reason(cfg, sim, healthy, 0)[0]
    _struggle(sim, 1)
    starving = sim.settlements[1]
    due, why = should_reason(cfg, sim, starving, 0)
    assert due and why == "struggling"


def test_is_struggling_each_signal():
    sim = _sim()
    s = sim.settlements[0]
    cfg = ReasoningConfig()
    base = dict(s.__dict__) if hasattr(s, "__dict__") else {}
    s.happiness = 0.1
    assert is_struggling(s, cfg)
    for attr in ("happiness",):
        setattr(s, attr, 0.8)
    s.food_stock = 1.0  # 0.1/capita < threshold
    assert is_struggling(s, cfg)
    s.food_stock = 500.0
    s.net_food_rate = -3.0
    assert is_struggling(s, cfg)


# ----------------------------------------------------------------------
# Struggle prioritization
# ----------------------------------------------------------------------

def test_prioritize_worst_first_and_dead_last_infinite():
    sim = _sim(n=3)
    _struggle(sim, 2)
    ranked = prioritize(sim, [s for s in sim.settlements if s.is_alive])
    assert ranked[0] is sim.settlements[2]  # worst first


def test_struggle_score_starvation_dominates():
    sim = _sim()
    hungry = sim.settlements[0]
    unhappy = sim.settlements[1]
    hungry.starvation_progress = 40
    unhappy.happiness = 0.01
    assert struggle_score(hungry) > struggle_score(unhappy)


def test_struggle_score_dead_is_infinite():
    sim = _sim()
    dead = sim.settlements[0]
    dead.population = 0
    assert struggle_score(dead) == float("inf")


# ----------------------------------------------------------------------
# BackgroundAdvisor concurrency guard
# ----------------------------------------------------------------------

def test_submit_poll_lifecycle():
    advisor = BackgroundAdvisor(FakeClient())
    try:
        assert advisor.submit("s1", "SUMMARY", "Alpha")
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            result = advisor.poll("s1")
            if result is not None:
                break
            time.sleep(0.02)
        assert result is not None and result.ok
        assert advisor.completed == 1
    finally:
        advisor.shutdown()


def test_second_submit_rejected_while_busy():
    advisor = BackgroundAdvisor(FakeClient(delay=0.4))
    try:
        assert advisor.submit("s1", "S", "A")
        assert not advisor.submit("s2", "S", "B")  # one in flight max
        deadline = time.time() + 5
        while time.time() < deadline and advisor.busy:
            time.sleep(0.02)
        # slot freed after completion -> next submit succeeds
        assert advisor.submit("s2", "S", "B")
    finally:
        advisor.shutdown()


def test_worker_survives_client_exception():
    advisor = BackgroundAdvisor(FakeClient(raise_exc=RuntimeError("x")))
    try:
        assert advisor.submit("s1", "S", "A")
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            result = advisor.poll("s1")
            if result is not None:
                break
            time.sleep(0.02)
        assert result is not None and not result.ok
        assert advisor.failed >= 1
        # worker alive: next request still served
        assert advisor.submit("s2", "S", "B")
    finally:
        advisor.shutdown()


# ----------------------------------------------------------------------
# Agent integration: non-blocking + applied-next-cycle
# ----------------------------------------------------------------------

def test_observe_never_blocks_on_slow_llm():
    sim = _sim()
    s = sorted(sim.settlements, key=lambda x: x.name)[0]
    advisor = BackgroundAdvisor(FakeClient(delay=2.0))
    agent = attach_llm_agent(sim, s.id, advisor=advisor,
                             config=ReasoningConfig(interval_ticks=5))
    try:
        t0 = time.perf_counter()
        for _ in range(20):
            agent.observe(sim, s)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"observe blocked {elapsed:.2f}s"
    finally:
        advisor.shutdown()


def test_advice_applied_next_cycle_after_completion():
    sim = _sim()
    s = sorted(sim.settlements, key=lambda x: x.name)[0]
    advisor = BackgroundAdvisor(FakeClient(delay=0.2))
    agent = attach_llm_agent(sim, s.id, advisor=advisor,
                             config=ReasoningConfig(interval_ticks=24))
    try:
        obs = agent.observe(sim, s)          # submits
        action_a = agent.decide(obs)          # fallback this cycle
        assert agent.telemetry.fallback_decisions == 1
        time.sleep(0.6)                       # worker finishes
        obs = agent.observe(sim, s)           # polls result, queues intent
        action_b = agent.decide(obs)
        assert action_b == int(Action.BUILD_FARM)
        assert agent.telemetry.actions_validated == 1
    finally:
        advisor.shutdown()


def test_struggling_gate_suppresses_healthy_submissions():
    sim = _sim()
    s = sorted(sim.settlements, key=lambda x: x.name)[0]
    advisor = BackgroundAdvisor(FakeClient())
    agent = attach_llm_agent(sim, s.id, advisor=advisor,
                             config=ReasoningConfig(interval_ticks=None,
                                                    struggling_only=True))
    try:
        for _ in range(5):
            agent.observe(sim, s)
        assert advisor.submitted == 0  # healthy -> never reasoned
    finally:
        advisor.shutdown()


def test_struggling_settlement_reasoned_first():
    sim = _sim(n=3)
    _struggle(sim, 1)
    names = {s.id: s for s in sim.settlements}
    ordered = prioritize(sim, list(sim.settlements))
    # worst settlement sorts ahead of the healthy ones
    assert ordered[0].id != ordered[-1].id
    assert ordered[0] is sim.settlements[1]


def test_fallback_episode_survives_failing_advisor():
    sim = _sim()
    s = sorted(sim.settlements, key=lambda x: x.name)[0]
    advisor = BackgroundAdvisor(FakeClient(raise_exc=RuntimeError("down")))
    agent = attach_llm_agent(sim, s.id, advisor=advisor,
                             config=ReasoningConfig(interval_ticks=12))
    try:
        for _ in range(60):
            sim.step()
        assert sim.tick == 60
        assert s.is_alive
        assert agent.telemetry.fallback_decisions > 0
    finally:
        advisor.shutdown()


def test_event_config_reaches_agent_via_scheduler():
    """Event-triggered mode: a raid since last advice causes a submission."""
    sim = _sim()
    s = sorted(sim.settlements, key=lambda x: x.name)[0]
    advisor = BackgroundAdvisor(FakeClient())
    agent = attach_llm_agent(sim, s.id, advisor=advisor,
                             config=ReasoningConfig(interval_ticks=None,
                                                    on_events=False))
    try:
        agent.observe(sim, s)  # first_advice fires once
        agent.decide(agent.observe(sim, s))
        submitted_first = advisor.submitted
        sim.event_log.append(
            WorldEvent(tick=sim.tick, type="raid", actor_ids=[s.id],
                       description="raid"))
        # enable event mode dynamically
        agent.config.on_events = False
        agent.observe(sim, s)
        assert advisor.submitted == submitted_first  # still off
        agent.config.on_events = True
        agent.observe(sim, s)
        assert advisor.submitted == submitted_first + 1  # raid triggered
    finally:
        advisor.shutdown()
