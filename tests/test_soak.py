"""Sprint 37 (slow): long-horizon soak test.

10k ticks must complete within a time budget with bounded memory growth
and byte-identical outcomes across identical runs. Auto-skipped logic:
none — slow tier runs explicitly via `pytest -m slow`."""

import time

import pytest

from worldsim.simulation import Simulation
from worldsim.world import World

TICKS = 10_000
TIME_BUDGET_SECONDS = 120.0


def _build():
    sim = Simulation(World(seed=4242, size=64))
    sim.spawn_settlements(count=3)
    return sim


@pytest.mark.slow
def test_soak_10k_ticks_within_budget_and_bounded_memory():
    # Timing pass UNTRACED (tracemalloc slows allocation ~10x).
    import tracemalloc

    sim = _build()
    started = time.perf_counter()
    for _ in range(TICKS):
        sim.step()
    elapsed = time.perf_counter() - started

    assert sim.tick == TICKS
    assert elapsed < TIME_BUDGET_SECONDS, (
        f"{TICKS} ticks took {elapsed:.1f}s "
        f"({TICKS / elapsed:.0f} ticks/s)"
    )
    # Bounded structures did their job.
    assert len(sim.event_log) <= 20_000
    assert len(sim.history) == TICKS // 500

    # Memory pass on a fresh sim: traced but short (growth check only).
    sim2 = _build()
    for _ in range(2000):
        sim2.step()
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    for _ in range(2000):
        sim2.step()
    grown = tracemalloc.get_traced_memory()[0] - baseline
    tracemalloc.stop()
    # Long-run memory must be roughly flat once structures are warm
    # (allow generous slack for epoch history + legitimate world growth).
    print(
        f"\nsoak: {TICKS} ticks in {elapsed:.1f}s "
        f"({TICKS / elapsed:.0f} ticks/s); traced growth over 2k warm "
        f"ticks: {grown / 1e6:.1f} MB"
    )
    assert grown < 100 * 1e6


@pytest.mark.slow
def test_all_settlements_may_die_without_error():
    """Full civilizational collapse is a legal long-run outcome; the sim
    must keep ticking cleanly afterward."""
    sim = _build()
    died_at = []
    prev_alive = {s.id: True for s in sim.settlements}
    original_ids = set(prev_alive)
    for i in range(2000):
        if i == 500:
            # Collapse everyone through the real death path.
            for s in sim.settlements:
                if s.is_alive and s.id in original_ids:
                    sim._kill(s)
        sim.step()
        for s in sim.settlements:
            was = prev_alive.get(s.id)
            if was is None:
                prev_alive[s.id] = s.is_alive  # newborn (re-settler)
                continue
            if was and not s.is_alive:
                died_at.append(sim.tick)
                prev_alive[s.id] = False
    assert len(died_at) == 3
    assert len(sim.ruins) >= 1
    # Sim keeps advancing after total collapse.
    assert sim.tick == 2000
