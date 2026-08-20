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
# Starting reserves sized to afford the first Farm (5w3s) + Sawmill (4w2s).
STARTING_RESOURCES = {"wood": 30.0, "stone": 15.0}
FOOD_PER_WORKER_PER_TICK = 1.0


@dataclass
class Settlement:
    name: str
    spawn_x: int
    spawn_y: int
    population: int = STARTING_POPULATION
    food_stock: float = STARTING_FOOD
    resource_inventory: dict[str, float] = field(
        default_factory=lambda: dict(STARTING_RESOURCES)
    )
    # Pending construction orders (BuildingType names), processed FIFO.
    build_queue: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at_tick: int = 0
    destroyed_at_tick: int | None = None
    # Counters tracking progress toward the next growth/starvation event.
    growth_progress: int = 0
    starvation_progress: int = 0
    # Ticks spent with any negative inventory (economic collapse timer).
    negative_inventory_progress: int = 0
    # Food income minus consumption for the most recent tick (set by sim).
    net_food_rate: float = 0.0

    @property
    def is_alive(self) -> bool:
        return self.population > 0

    @property
    def is_in_scarcity(self) -> bool:
        """True while any resource inventory is negative (poverty slowdown)."""
        return any(v < 0 for v in self.resource_inventory.values())

    def consume_food(
        self, income: float, capacity: float | None = None
    ) -> None:
        """Apply one tick of food income and consumption.

        If capacity is given, income above free storage is wasted — but the
        recorded net_food_rate reflects true production (used by expansion
        decisions), not the capped amount."""
        consumption = self.population * FOOD_PER_WORKER_PER_TICK
        effective = income
        if capacity is not None:
            effective = min(income, max(0.0, capacity - self.food_stock))
        self.food_stock += effective - consumption
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
