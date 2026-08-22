"""Reward components, rolling normalization, hacking detection (Sprint 13).

Reward breakdowns follow docs/detailed_sprint_plan.md Sprint 13: per-tick
named components, a rolling normalizer over the last 1000 ticks, and a
detector that flags agents earning >80% of reward from a single source.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

ROLLING_WINDOW_TICKS = 1000
HACKING_SHARE_THRESHOLD = 0.80
HACKING_MIN_TICKS = 200  # don't judge before the agent has a track record

REWARD_POPULATION_GAIN = 0.02
REWARD_POPULATION_LOSS = 0.2
REWARD_SURVIVAL_PER_TICK = 0.001
REWARD_BUILDING_DELTA = 0.05
REWARD_ROUTE_DELTA = 0.1
PENALTY_STARVING_TICK = 0.02
PENALTY_REDUNDANT_ACTION = 0.01  # escalates with repetition count
BONUS_EFFECTIVE_ACTION = 0.005


def compute_reward_components(
    prev_population: int,
    population: int,
    building_delta: int,
    route_delta: int,
    food_stock: float,
    starvation_progress: int,
    repeated_action_count: int,
    action_executed: bool,
) -> dict[str, float]:
    """Named reward components for one tick (§6.4-shaped). Returns a dict
    whose values sum to the total step reward (pre-clamp); callers clamp."""
    c: dict[str, float] = {
        "survival": REWARD_SURVIVAL_PER_TICK,
        "population": 0.0,
        "buildings": REWARD_BUILDING_DELTA * max(0, building_delta),
        "routes": REWARD_ROUTE_DELTA * max(0, route_delta),
        "starvation": 0.0,
        "redundant_action": 0.0,
        "effective_action": 0.0,
    }
    pop_delta = population - prev_population
    if pop_delta > 0:
        c["population"] += REWARD_POPULATION_GAIN * pop_delta
    elif pop_delta < 0:
        c["population"] -= REWARD_POPULATION_LOSS * abs(pop_delta)
    if food_stock <= 0 and starvation_progress > 10:
        c["starvation"] -= PENALTY_STARVING_TICK
    # Redundant-action shaping: repeating the identical action gets
    # progressively costlier from the 5th consecutive tick.
    if repeated_action_count >= 5:
        c["redundant_action"] -= PENALTY_REDUNDANT_ACTION * min(
            repeated_action_count - 4, 10
        )
    if action_executed:
        c["effective_action"] += BONUS_EFFECTIVE_ACTION
    return c


def total_of(components: dict[str, float]) -> float:
    return float(sum(components.values()))


@dataclass
class RollingNormalizer:
    """Rolling mean/std over the last `window` raw rewards."""

    window: int = ROLLING_WINDOW_TICKS
    _values: deque = field(default_factory=lambda: deque(maxlen=1000))

    def __post_init__(self) -> None:
        self._values = deque(maxlen=self.window)

    def record(self, value: float) -> None:
        self._values.append(value)

    def mean(self) -> float:
        return sum(self._values) / len(self._values) if self._values else 0.0

    def std(self) -> float:
        n = len(self._values)
        if n < 2:
            return 1.0
        mu = self.mean()
        var = sum((v - mu) ** 2 for v in self._values) / n
        return max(var**0.5, 1e-6)

    def normalize(self, value: float) -> float:
        """(value - rolling_mean) / rolling_std; identity until warmed up."""
        if len(self._values) < 50:
            return value
        return (value - self.mean()) / self.std()


class RewardHackingDetector:
    """Flags agents whose reward comes predominantly from one component.

    Tracks absolute earned amounts per component over a sliding window; when
    any single source exceeds HACKING_SHARE_THRESHOLD of all earned reward
    (after HACKING_MIN_TICKS), it is flagged."""

    def __init__(
        self,
        share_threshold: float = HACKING_SHARE_THRESHOLD,
        min_ticks: int = HACKING_MIN_TICKS,
        window: int = ROLLING_WINDOW_TICKS,
    ) -> None:
        self.share_threshold = share_threshold
        self.min_ticks = min_ticks
        self._window: deque = deque(maxlen=window)
        self.ticks_seen = 0
        self.flagged_since: int | None = None

    def record(self, tick: int, components: dict[str, float]) -> bool:
        """Feed one tick's breakdown. Returns True if currently flagged."""
        self._window.append(components)
        self.ticks_seen += 1
        totals: dict[str, float] = {}
        grand = 0.0
        for comps in self._window:
            for name, value in comps.items():
                totals[name] = totals.get(name, 0.0) + abs(value)
                grand += abs(value)
        flagged = False
        if grand > 0 and self.ticks_seen >= self.min_ticks:
            for name, amount in totals.items():
                if amount / grand > self.share_threshold:
                    flagged = True
                    break
        if flagged:
            if self.flagged_since is None:
                self.flagged_since = tick
        else:
            self.flagged_since = None
        return flagged

    def dominant_source(self) -> str | None:
        totals: dict[str, float] = {}
        grand = 0.0
        for comps in self._window:
            for name, value in comps.items():
                totals[name] = totals.get(name, 0.0) + abs(value)
                grand += abs(value)
        if not totals or grand == 0:
            return None
        best = max(totals.items(), key=lambda kv: kv[1])
        if best[1] / grand > self.share_threshold:
            return best[0]
        return None
