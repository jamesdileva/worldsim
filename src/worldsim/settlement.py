"""Settlement entity and population dynamics.

Growth/starvation rules from docs/detailed_sprint_plan.md Sprint 2:
- +1 population per 24 ticks while food is positive
- -1 population per 48 ticks while food is exhausted
- settlement dies at population 0
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field

PERSONALITY_SEED_OFFSET = 5_000_000
PERSONALITY_TRAITS = ("expansionism", "industry", "commerce", "aggression")

# Sprint 11: five preset archetypes. Assigned at spawn (seeded), stored in
# the personality dict under "archetype"; biases action selection without
# fully constraining it.
ARCHETYPES = ("agricultural", "mining", "trading", "military", "balanced")
STRATEGY_LABEL_INTERVAL_TICKS = 250


def assign_archetype(seed: int, settlement_index: int) -> str:
    rng = random.Random(
        (seed ^ (PERSONALITY_SEED_OFFSET + 7777)) + settlement_index * 104729
    )
    return rng.choice(ARCHETYPES)


def assign_personality(seed: int, settlement_index: int) -> dict[str, float]:
    """Seeded personality vector in [0,1] per trait, plus archetype (Sprint 11).

    Traits bias the rule-based agent's thresholds: expansionism speeds
    claiming, industry favors sawmills/mines, commerce speeds trade,
    aggression gates raiding. The archetype adds a strategy bias without
    fully constraining behavior."""
    rng = random.Random(
        (seed ^ PERSONALITY_SEED_OFFSET) + settlement_index * 7919
    )
    personality = {trait: round(rng.random(), 3) for trait in PERSONALITY_TRAITS}
    personality["archetype"] = assign_archetype(seed, settlement_index)
    return personality

GROWTH_INTERVAL_TICKS = 24
STARVATION_INTERVAL_TICKS = 48
STARTING_POPULATION = 10
STARTING_FOOD = 50
# Starting reserves sized to afford the first Farm (5w3s) + Sawmill (4w2s).
STARTING_RESOURCES = {"wood": 30.0, "stone": 15.0}
FOOD_PER_WORKER_PER_TICK = 1.0

# Happiness/stability (Sprint 5).
STARTING_HAPPINESS = 0.5
HAPPINESS_DECAY_AFTER_TICKS = 10  # consecutive negative-net-food ticks
HAPPINESS_DECAY_RATE = 0.01
HAPPINESS_RECOVERY_RATE = 0.005
HAPPINESS_MAX = 1.0
HAPPINESS_MIN = 0.0
LOW_HAPPINESS_THRESHOLD = 0.1
LOW_HAPPINESS_COLLAPSE_TICKS = 100


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
    # Happiness/stability (Sprint 5).
    happiness: float = STARTING_HAPPINESS
    negative_food_streak: int = 0
    low_happiness_progress: int = 0
    # Set when founded on/near ruins: id of the origin RuinSite (Sprint 5).
    ruin_origin: str | None = None
    # Personality vector biasing agent decisions (Sprint 8): trait -> [0,1],
    # plus "archetype" key (Sprint 11).
    personality: dict[str, float] = field(default_factory=dict)
    # Sprint 11: emergent strategy label derived from building mix/actions.
    strategy_label: str = "settling"
    raids_committed: int = 0
    routes_established: int = 0
    # Food income minus consumption for the most recent tick (set by sim).
    net_food_rate: float = 0.0
    # Sprint 31: technology/eras. Era derives from researched technologies
    # (tech.era_for); research_points accumulate deterministically per tick.
    research_points: float = 0.0
    technologies: list[str] = field(default_factory=list)
    # Sprint 35: warfare. army is a float pool; fort_level adds battle
    # defense; siege_progress counts consecutive attacker victories.
    army: float = 0.0
    fort_level: int = 0
    siege_progress: int = 0
    # Sprint 38: God Mode freeze — time stops for this settlement
    # (no decisions, production, growth, or decay) while frozen.
    frozen: bool = False

    @property
    def era(self) -> int:
        from .tech import era_for

        return era_for(self.technologies)

    @property
    def is_alive(self) -> bool:
        return self.population > 0

    @property
    def is_in_scarcity(self) -> bool:
        """True while any resource inventory is negative (poverty slowdown)."""
        return any(v < 0 for v in self.resource_inventory.values())

    def step_happiness(self, building_count: int) -> None:
        """Advance happiness by one tick (Sprint 5).

        Decays after 10+ consecutive ticks of negative net food; recovers
        slowly otherwise, scaled slightly by building quality."""
        if self.net_food_rate < 0:
            self.negative_food_streak += 1
        else:
            self.negative_food_streak = 0
        if self.negative_food_streak > HAPPINESS_DECAY_AFTER_TICKS:
            self.happiness = max(
                HAPPINESS_MIN, self.happiness - HAPPINESS_DECAY_RATE
            )
        else:
            quality_bonus = min(building_count, 10) * 0.0005
            self.happiness = min(
                HAPPINESS_MAX,
                self.happiness + HAPPINESS_RECOVERY_RATE + quality_bonus,
            )
        if self.happiness < LOW_HAPPINESS_THRESHOLD:
            self.low_happiness_progress += 1
        else:
            self.low_happiness_progress = 0

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

    def step_population(self, growth_multiplier: int = 1) -> None:
        """Advance growth/starvation counters by one tick."""
        if not self.is_alive:
            return
        if self.food_stock > 0:
            self.starvation_progress = 0
            self.growth_progress += growth_multiplier
            while self.growth_progress >= GROWTH_INTERVAL_TICKS:
                self.growth_progress -= GROWTH_INTERVAL_TICKS
                self.population += 1
        else:
            self.growth_progress = 0
            self.starvation_progress += 1
            if self.starvation_progress >= STARVATION_INTERVAL_TICKS:
                self.starvation_progress -= STARVATION_INTERVAL_TICKS
                self.population -= 1
