"""Sprint 63 round 3: run pacing, timeline tail, road cost escalation,
viable god colonies, visible counsel failures."""

import time

import pytest

from worldsim.db import WorldStore
from worldsim.simulation import Simulation
from worldsim.webapp import WorldSession
from worldsim.world import World


def test_timeline_tail_keeps_newest():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    for i in range(30):
        sim.log_event("strategy", [sim.settlements[0].id], f"event {i}")
    from worldsim.timeline import build_timeline

    head = build_timeline(sim, limit=10)
    tail = build_timeline(sim, limit=10, tail=True)
    assert head[0].description == "event 0"
    assert tail[-1].description == "event 29"


def test_road_cost_escalates_with_network_size():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    cheap = sim.road_cost(s)
    sprawling = {(s.spawn_y, s.spawn_x + i) for i in range(100)}
    sim.roads_of = lambda settlement: sprawling
    expensive = sim.road_cost(s)
    assert cheap > 0
    assert expensive > cheap * 4


def test_god_colony_claims_starter_territory_inside_foreign_land():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=2)
    big = next(s for s in sim.settlements if s.is_alive)
    # Grow its territory so the colony site is deep inside foreign land.
    for _ in range(400):
        sim.step()
    target = max(
        (s for s in sim.settlements if s.is_alive),
        key=lambda s: int((sim.world.ownership == 
                           sim.settlements.index(s)).sum()),
    )
    ty, tx = None, None
    idx = sim.settlements.index(target)
    import numpy as np

    owned = np.argwhere(sim.world.ownership == idx)
    oy, ox = int(owned[len(owned) // 2][0]), int(owned[len(owned) // 2][1])
    _before, after = sim.god_spawn_settlement(ox, oy)
    newcomer_index = len(sim.settlements) - 1
    owned_now = int((sim.world.ownership == newcomer_index).sum())
    assert owned_now >= 5, (
        f"colony owns only {owned_now} tiles - cannot survive")
    assert after["settlements"] == len(sim.settlements)


def test_failed_advice_is_visible_in_event_log():
    from worldsim.advice import AdviceResult
    from worldsim.llm_agent import attach_llm_agent
    from worldsim.reasoning import ReasoningConfig

    class FailingAdvisor:
        def __init__(self):
            self._result = AdviceResult(ok=False, error="connection refused")

        @property
        def busy(self):
            return False

        def poll(self, key):
            result = self._result
            self._result = None
            return result

        def submit(self, key, summary, name):
            return True

    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    agent = attach_llm_agent(
        sim, s.id,
        client=object(),
        advisor=FailingAdvisor(),
        config=ReasoningConfig(interval_ticks=None),
    )
    agent.observe(sim, s)
    advice_events = [e for e in sim.event_log if e.type == "advice"]
    assert len(advice_events) == 1
    assert "could not reach" in advice_events[0].description
    assert "connection refused" in advice_events[0].description
