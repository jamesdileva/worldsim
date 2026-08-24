"""Sprint 37: long-horizon stability — bounded memory, epoch history,
memoized food math."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.simulation import (
    EVENT_LOG_MAX,
    EXPERIENCE_BUFFER_MAX,
    HISTORY_INTERVAL_TICKS,
    Simulation,
)
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Bounded event log
# ----------------------------------------------------------------------

def test_event_log_drops_oldest_beyond_cap():
    sim = _sim(n=1)
    for i in range(EVENT_LOG_MAX + 50):
        sim.log_event("test", ["x"], f"event {i}")
    assert len(sim.event_log) == EVENT_LOG_MAX
    assert sim.event_log[0].description == "event 50"
    assert sim.event_log[-1].description == f"event {EVENT_LOG_MAX + 49}"


def test_event_log_cap_is_deterministic():
    logs = []
    for _ in range(2):
        sim = _sim(n=1)
        for i in range(1000):
            sim.log_event("raid", [f"s{i % 3}"], f"r{i}")
        logs.append([e.description for e in sim.event_log])
    assert logs[0] == logs[1]


# ----------------------------------------------------------------------
# Epoch history
# ----------------------------------------------------------------------

def test_history_epochs_recorded_at_interval():
    sim = _sim(n=2)
    for _ in range(HISTORY_INTERVAL_TICKS * 3 + 5):
        sim.step()
    assert len(sim.history) == 3
    assert [h["tick"] for h in sim.history] == [
        HISTORY_INTERVAL_TICKS,
        HISTORY_INTERVAL_TICKS * 2,
        HISTORY_INTERVAL_TICKS * 3,
    ]


def test_history_epoch_fields_are_populated():
    sim = _sim(n=2)
    for _ in range(HISTORY_INTERVAL_TICKS):
        sim.step()
    epoch = sim.history[0]
    assert set(epoch) == {
        "tick", "settlements_alive", "total_population",
        "wars_active", "routes_active", "mean_happiness", "prices",
        "populations",
    }
    assert epoch["settlements_alive"] >= 1
    assert epoch["total_population"] > 0
    assert set(epoch["prices"]) == {"food", "wood", "stone", "metal"}


def test_history_identical_across_identical_sims():
    def run():
        sim = _sim(n=3, seed=99)
        for _ in range(HISTORY_INTERVAL_TICKS * 2 + 7):
            sim.step()
        return sim.history

    assert run() == run()


def test_history_stays_compact_at_long_horizons():
    sim = _sim(n=2)
    # Simulate the record cadence without paying full tick cost: drive
    # the recorder directly.
    for i in range(1, 1001):
        sim.world.tick = i * HISTORY_INTERVAL_TICKS
        sim._record_history_epoch()
    assert len(sim.history) == 1000  # ~200 records per 100k ticks


# ----------------------------------------------------------------------
# Experience buffer bound
# ----------------------------------------------------------------------

def test_experience_buffer_drops_oldest():
    sim = _sim(n=1)
    s = sim.settlements[0]
    agent = sim.agents[0]
    for i in range(EXPERIENCE_BUFFER_MAX + 20):
        obs = agent.observe(sim, s)
        sim._pending_transitions[s.id] = (obs, 58, s.population, 0)
        sim._finalize_transition(s, obs)
    assert len(sim.experience_buffer) <= EXPERIENCE_BUFFER_MAX
    assert len(sim.experience_buffer) == EXPERIENCE_BUFFER_MAX


# ----------------------------------------------------------------------
# Frozen contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
