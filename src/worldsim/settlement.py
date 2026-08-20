"""Settlement entity and population dynamics.

Growth/starvation rules from docs/detailed_sprint_plan.md Sprint 2:
- +1 population per 24 ticks while food is positive
- -1 population per 48 ticks while food is exhausted
- settlement dies at population 0
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

GROWTH_INTERVAL_TICKS = 24
STARVATION_INTERVAL_TICKS = 48
STARTING_POPULATION = 10
STARTING_FOOD = 50
FOOD_PER_WORKER_PER_TICK = 1.0


@dataclass
class Settlement:
    name: str
    spawn_x: int
    spawn_y: int
    population: int = STARTING_POPULATION
    food_stock: float = STARTING_FOOD
    resource_inventory: dict[str, float] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at_tick: int = 0
    destroyed_at_tick: int | None = None
    # Counters tracking progress toward the next growth/starvation event.
    growth_progress: int = 0
    starvation_progress: int = 0
    # Food income minus consumption for the most recent tick (set by sim).
    net_food_rate: float = 0.0

    @property
    def is_alive(self) -> bool:
        return self.population > 0

    def consume_food(self, income: float) -> None:
        """Apply one tick of food income and consumption."""
        consumption = self.population * FOOD_PER_WORKER_PER_TICK
        self.food_stock += income - consumption
        self.net_food_rate = income - consumption

    def step_population(self) -> None:
        """Advance growth/starvation counters by one tick."""
        if not self.is_alive:
            return
        if self.food_stock > 0:
            self.starvation_progress = 0
            self.growth_progress += 1
            if self.growth_progress >= GROWTH_INTERVAL_TICKS:
                self.growth_progress -= GROWTH_INTERVAL_TICKS
                self.population += 1
        else:
            self.growth_progress = 0
            self.starvation_progress += 1
            if self.starvation_progress >= STARVATION_INTERVAL_TICKS:
                self.starvation_progress -= STARVATION_INTERVAL_TICKS
                self.population -= 1
