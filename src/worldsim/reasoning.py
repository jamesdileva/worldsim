"""Budget-aware LLM reasoning scheduling (Sprint 29).

Three configurable triggers, combinable:
- interval: reason every N ticks
- event:    reason when important events hit the settlement since last
            advice (raid, war, disaster, collapse, peace)
- struggling: reason only when the settlement is in bad shape; the worst
            settlement sorts first when inference budget is scarce

Concurrency guard: BackgroundAdvisor runs at most ONE in-flight LLM call
per world on a daemon thread. The sim loop only builds a cheap summary
and submits; results are consumed non-blockingly on a later tick.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from .advice import AdviceResult, advise
from .llm import OllamaClient
from .settlement import Settlement

DEFAULT_IMPORTANT_EVENTS: tuple[str, ...] = (
    "raid", "war", "disaster", "collapse", "peace",
)


@dataclass
class ReasoningConfig:
    """All three modes configurable; disabled modes simply never fire."""

    interval_ticks: int | None = 24   # None disables interval mode
    on_events: bool = False           # reason after important events
    struggling_only: bool = False     # gate by struggle score
    happiness_threshold: float = 0.35
    food_per_capita_threshold: float = 1.0
    important_events: tuple[str, ...] = DEFAULT_IMPORTANT_EVENTS


def struggle_score(settlement: Settlement) -> float:
    """Higher = worse off. Starvation dominates, then unhappiness,
    then thin food reserves."""
    if not settlement.is_alive:
        return float("inf")
    starvation = settlement.starvation_progress / max(
        1, settlement.population * 2)
    food_per_capita = settlement.food_stock / max(1, settlement.population)
    reserve_deficit = max(0.0, 5.0 - food_per_capita) / 5.0
    unhappiness = max(0.0, 0.5 - settlement.happiness) / 0.5
    return starvation * 10.0 + reserve_deficit + unhappiness


def is_struggling(settlement: Settlement, config: ReasoningConfig) -> bool:
    food_per_capita = settlement.food_stock / max(
        1, settlement.population)
    return (
        settlement.happiness < config.happiness_threshold
        or food_per_capita < config.food_per_capita_threshold
        or settlement.net_food_rate < 0
    )


def should_reason(config: ReasoningConfig, sim, settlement: Settlement,
                  last_reasoned_tick: int | None) -> tuple[bool, str]:
    """(due, why) for one settlement under the given config."""
    tick = sim.tick
    if config.interval_ticks is not None:
        if last_reasoned_tick is None:
            return True, "first_advice"
        if tick - last_reasoned_tick >= config.interval_ticks:
            return True, "interval"
    if config.on_events and _important_event_since(
            sim, settlement, last_reasoned_tick, config.important_events):
        return True, "event"
    if config.struggling_only and is_struggling(settlement, config):
        return True, "struggling"
    return False, ""


def _important_event_since(sim, settlement: Settlement,
                           last_reasoned_tick: int | None,
                           types: tuple[str, ...]) -> bool:
    # No prior advice -> consider everything, including tick-0 events.
    floor = -1 if last_reasoned_tick is None else last_reasoned_tick
    for event in reversed(sim.event_log):
        if event.tick <= floor:
            break
        if event.type in types and settlement.id in event.actor_ids:
            return True
    return False


def prioritize(sim, settlements: list[Settlement]) -> list[Settlement]:
    """Struggling-settlements-first ordering (worst first)."""
    return sorted(settlements, key=struggle_score, reverse=True)


class BackgroundAdvisor:
    """At most one in-flight advise() call per world.

    submit() is non-blocking and silently ignored while busy/queued —
    the sim loop never waits on the network. poll() drains finished
    results keyed by settlement id."""

    def __init__(self, client: OllamaClient) -> None:
        self._client = client
        self._jobs: queue.Queue[tuple[str, str, str]] = queue.Queue()
        self._results: dict[str, AdviceResult] = {}
        self._lock = threading.Lock()
        self._in_flight_key: str | None = None
        self.submitted = 0
        self.completed = 0
        self.failed = 0
        self._worker = threading.Thread(
            target=self._work, name="worldsim-advisor", daemon=True)
        self._worker.start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._in_flight_key is not None

    def has_result(self, key: str) -> bool:
        with self._lock:
            return key in self._results

    def submit(self, key: str, summary: str, name: str) -> bool:
        """Queue an advice request unless one is already queued/in-flight.
        Returns True if accepted."""
        if self.busy:
            return False
        with self._lock:
            if self._in_flight_key is not None:
                return False
            self._in_flight_key = key
        self._jobs.put((key, summary, name))
        self.submitted += 1
        return True

    def poll(self, key: str) -> AdviceResult | None:
        with self._lock:
            return self._results.pop(key, None)

    def shutdown(self, timeout: float = 3.0) -> None:
        self._worker.join(timeout=timeout)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            key, summary, name = job
            try:
                result = advise(self._client, summary, name=name)
                with self._lock:
                    self._results[key] = result
                    self._in_flight_key = None
                if result.ok:
                    self.completed += 1
                else:
                    self.failed += 1
            except Exception:  # noqa: BLE001 - worker must never die
                with self._lock:
                    self._results[key] = AdviceResult(
                        ok=False, error="advisor worker exception")
                    self._in_flight_key = None
                self.failed += 1
