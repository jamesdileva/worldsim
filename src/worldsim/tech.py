"""Technology and civilization eras (Sprint 31, Phase 6).

Design constraints:
- Deterministic: tech order is fixed, thresholds are constants; a
  settlement's era is a pure function of its researched technologies.
- Frozen RL contract untouched: no new actions, no observation changes.
  Eras gate EXISTING mechanics (Mine/Granary need Era II) and grant
  small production bonuses at Era III.
- Research accumulates from population each tick; rate scales with era.
"""

from __future__ import annotations

from .buildings import BuildingType

# Fixed research order — no choice tree yet (S32 economies may revisit).
TECHNOLOGIES: tuple[str, ...] = (
    "agriculture",
    "masonry",
    "engineering",
    "administration",
)

TECH_RESEARCH_COSTS: dict[str, float] = {
    "agriculture": 100.0,
    "masonry": 250.0,
    "engineering": 450.0,
    "administration": 700.0,
}

# Era II gates heavy construction; Era III grants civic bonuses.
ERA_TECH_REQUIREMENTS: dict[int, tuple[str, ...]] = {
    1: (),
    2: ("agriculture", "masonry"),
    3: ("engineering", "administration"),
}

BUILDING_ERA_REQUIREMENTS: dict[BuildingType, int] = {
    BuildingType.MINE: 2,
    BuildingType.GRANARY: 2,
}

BASE_RESEARCH_RATE = 0.05          # points per population point per tick
ERA_RESEARCH_MULTIPLIER: dict[int, float] = {1: 1.0, 2: 1.25, 3: 1.5}

ERA3_FARM_OUTPUT_BONUS = 0.15      # +15% farm food at Era III
ERA3_ROUTE_TRANSFER_BONUS = 0.25   # +25% trade transfer size at Era III


def next_technology(technologies: list[str]) -> str | None:
    """First not-yet-researched technology in fixed order."""
    researched = set(technologies)
    for tech in TECHNOLOGIES:
        if tech not in researched:
            return tech
    return None


def era_for(technologies: list[str]) -> int:
    """Highest era whose full tech requirement is satisfied."""
    researched = set(technologies)
    era = 1
    for candidate in sorted(ERA_TECH_REQUIREMENTS):
        if all(t in researched for t in ERA_TECH_REQUIREMENTS[candidate]):
            era = max(era, candidate)
    return era
