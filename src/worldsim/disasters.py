"""Natural disasters: drought, fire, plague (Sprint 5).

Events are regional (center + radius) and season-weighted. The event RNG is
a pure function of (world_seed, tick) so runs stay fully reproducible
(architecture_detailed.md A1 — entropy only via seeded streams).
"""

from __future__ import annotations

import enum
import random
import uuid
from dataclasses import dataclass, field

from .clock import TICKS_PER_SEASON, SEASON_NAMES as SEASONS

DISASTER_SEED_OFFSET = 3_000_000
EVENT_CHECK_INTERVAL_TICKS = 50
BASE_EVENT_CHANCE = 0.10  # per check (~2-4 events per 1000 ticks)
DISASTER_RADIUS = 24

DROUGHT_DURATION_TICKS = 200
DROUGHT_FARM_MULTIPLIER = 0.5
PLAGUE_MORTALITY = 0.30

# Nuclear events (Sprint 42).
NUKE_RADIUS = 12
NUKE_POPULATION_FRACTION = 0.6   # share of pop annihilated in the fireball
CONTAMINATION_TICKS = 7_300      # ~20 years at ~365 ticks/year
CONTAMINATION_YIELD_FACTOR = 0.25
# Outweighs the baseline happiness recovery rate (~0.005/tick): despair
# from living in fallout beats ordinary good news.
# Per-covering-zone decay. Scaled by zone count: one strike grinds,
# stacked strikes crush morale toward the floor. The floor sits just
# above LOW_HAPPINESS_THRESHOLD so fallout alone never triggers the
# misery-collapse — famine and war still can (S60).
CONTAMINATION_HAPPINESS_DECAY = 0.02  # per tick per active zone
DESPAIR_HAPPINESS_FLOOR = 0.12


def season_of(tick: int) -> str:
    return SEASONS[(tick // TICKS_PER_SEASON) % len(SEASONS)]


class DisasterType(enum.IntEnum):
    DROUGHT = 0
    FIRE = 1
    PLAGUE = 2


# Per-season relative weights per disaster type.
SEASON_WEIGHTS: dict[str, dict[DisasterType, float]] = {
    "spring": {DisasterType.DROUGHT: 0.5, DisasterType.FIRE: 0.5, DisasterType.PLAGUE: 1.0},
    "summer": {DisasterType.DROUGHT: 3.0, DisasterType.FIRE: 2.0, DisasterType.PLAGUE: 1.0},
    "autumn": {DisasterType.DROUGHT: 1.0, DisasterType.FIRE: 2.0, DisasterType.PLAGUE: 1.0},
    "winter": {DisasterType.DROUGHT: 0.25, DisasterType.FIRE: 0.25, DisasterType.PLAGUE: 1.0},
}


@dataclass
class DisasterEvent:
    type: DisasterType
    center_x: int
    center_y: int
    radius: int = DISASTER_RADIUS
    start_tick: int = 0
    duration: int = 1
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def end_tick(self) -> int:
        return self.start_tick + self.duration

    def is_active(self, tick: int) -> bool:
        return self.start_tick <= tick < self.end_tick

    def covers(self, x: int, y: int) -> bool:
        return max(abs(x - self.center_x), abs(y - self.center_y)) <= self.radius


@dataclass
class ContaminationZone:
    """Long-lasting fallout from a nuclear event (Sprint 42).

    Suppresses food yields inside the zone and bleeds happiness from
    affected settlements until it decays on a fixed schedule."""

    center_x: int
    center_y: int
    radius: int
    start_tick: int
    end_tick: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def is_active(self, tick: int) -> bool:
        return self.start_tick <= tick < self.end_tick

    def covers(self, x: int, y: int) -> bool:
        return max(abs(x - self.center_x), abs(y - self.center_y)) <= self.radius


def roll_event(
    seed: int,
    tick: int,
    size: int,
    chance: float = BASE_EVENT_CHANCE,
) -> DisasterEvent | None:
    """Deterministically decide whether a disaster starts at this tick."""
    rng = random.Random((seed ^ DISASTER_SEED_OFFSET) + tick * 7919)
    if tick % EVENT_CHECK_INTERVAL_TICKS != 0 or rng.random() >= chance:
        return None
    season = season_of(tick)
    weights = SEASON_WEIGHTS[season]
    types = list(DisasterType)
    total = sum(weights[t] for t in types)
    roll = rng.random() * total
    cumulative = 0.0
    chosen = types[-1]
    for t in types:
        cumulative += weights[t]
        if roll <= cumulative:
            chosen = t
            break
    duration = DROUGHT_DURATION_TICKS if chosen == DisasterType.DROUGHT else 1
    # Margin clamped for small worlds (training uses 32-tile grids).
    margin = min(DISASTER_RADIUS + 1, max(1, size // 2))
    return DisasterEvent(
        type=chosen,
        center_x=rng.randint(margin, max(margin, size - margin)),
        center_y=rng.randint(margin, max(margin, size - margin)),
        start_tick=tick,
        duration=duration,
        # Deterministic id so identical runs produce identical events (A4).
        id=f"ev-{seed}-{tick}",
    )
