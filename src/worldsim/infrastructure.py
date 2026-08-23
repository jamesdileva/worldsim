"""Large-scale infrastructure: inter-settlement highway projects
(Sprint 33).

A highway is a joint construction project bridging two adjacent
settlements' territories across the unowned land between them. Unlike
regular roads (owned tiles only), highways may cross unowned ground —
that is their purpose.

Contract:
- Deterministic: paths, costs, and progress are pure functions of
  (state, tick); project IDs are uuid5 derivatives.
- Pay-as-you-go: the sponsoring settlement spends stone per segment per
  tick; projects PAUSE (never cancel) when funds run dry.
- Roads appear progressively as segments complete.
- Effect: trade routes between highway-connected endpoints ship +30%.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .buildings import Improvement
from .settlement import Settlement

HIGHWAY_STONE_PER_SEGMENT = 3.0
HIGHWAY_SEGMENTS_PER_TICK = 1        # Era III sponsors lay 2
HIGHWAY_ERA_REQUIREMENT = 2          # masonry
HIGHWAY_TRADE_BONUS = 0.30           # +30% shipment size when connected
HIGHWAY_MIN_STONE_RESERVE = HIGHWAY_STONE_PER_SEGMENT


@dataclass
class HighwayProject:
    """Multi-tick road link between two settlements."""

    a_id: str
    b_id: str
    sponsor_id: str                     # pays the stone
    path: list[tuple[int, int]]         # (y, x) tiles to convert to road
    segments_done: int = 0
    start_tick: int = 0
    completed: bool = False
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"worldsim/highway/{self.a_id}/{self.b_id}/"
                f"{self.start_tick}",
            ))

    @property
    def segments_total(self) -> int:
        return len(self.path)

    @property
    def done(self) -> bool:
        return self.segments_done >= self.segments_total

    def partner_of(self, settlement_id: str) -> str | None:
        if settlement_id == self.a_id:
            return self.b_id
        if settlement_id == self.b_id:
            return self.a_id
        return None


def plan_path(sim, a: Settlement, b: Settlement) -> list[tuple[int, int]]:
    """L-shaped path between spawn points, restricted to tiles that still
    need a road (skip water and existing roads). Deterministic."""
    size = sim.world.size
    ay, ax = a.spawn_y, a.spawn_x
    by, bx = b.spawn_y, b.spawn_x
    y, x = ay, ax
    tiles: list[tuple[int, int]] = []
    # Vertical leg first, then horizontal (fixed order -> deterministic).
    step_y = (by > y) - (by < y)
    while y != by:
        y += step_y
        tiles.append((y, x))
    step_x = (bx > x) - (bx < x)
    while x != bx:
        x += step_x
        tiles.append((y, x))
    from .tiles import TerrainType

    return [
        (ty, tx) for ty, tx in tiles
        if 0 <= tx < size and 0 <= ty < size
        and sim.world.improvements[ty, tx] == Improvement.NONE.value
        and TerrainType(sim.world.terrain[ty, tx]) != TerrainType.WATER
    ]


def can_start_highway(sim, a: Settlement,
                      b: Settlement) -> tuple[bool, str]:
    if not (a.is_alive and b.is_alive):
        return False, "settlement_dead"
    if a.era < HIGHWAY_ERA_REQUIREMENT:
        return False, f"{a.name}_era_too_low"
    idx_a = sim.settlements.index(a)
    idx_b = sim.settlements.index(b)
    if not sim._territories_adjacent(idx_a, idx_b):
        return False, "territories_not_adjacent"
    for project in sim.highway_projects:
        if {project.a_id, project.b_id} == {a.id, b.id}:
            return False, "highway_exists"
    if len(plan_path(sim, a, b)) == 0:
        return False, "no_path_needed"
    return True, ""


def start_highway(sim, a: Settlement, b: Settlement,
                  tick: int) -> HighwayProject | None:
    ok, _reason = can_start_highway(sim, a, b)
    if not ok:
        return None
    project = HighwayProject(
        a_id=a.id,
        b_id=b.id,
        sponsor_id=a.id,
        path=plan_path(sim, a, b),
        start_tick=tick,
    )
    sim.highway_projects.append(project)
    sim.log_event(
        "infrastructure",
        [a.id, b.id],
        f"{a.name} began a highway toward {b.name} "
        f"({project.segments_total} segments)",
    )
    return project


def highway_connected(sim, settlement_a_id: str,
                      settlement_b_id: str) -> bool:
    """True when a COMPLETED highway links the two settlements."""
    for project in sim.highway_projects:
        if project.completed and {project.a_id, project.b_id} == {
            settlement_a_id, settlement_b_id
        }:
            return True
    return False


def advance_projects(sim) -> None:
    """One tick of construction for every active project. Deterministic:
    iteration order is fixed by append order; payment comes from the
    sponsor only."""
    for project in sim.highway_projects:
        if project.completed:
            continue
        sponsor = next(
            (s for s in sim.settlements if s.id == project.sponsor_id), None)
        if sponsor is None or not sponsor.is_alive:
            continue  # paused, never cancelled
        segments_this_tick = (
            2 if sponsor.era >= 3 else HIGHWAY_SEGMENTS_PER_TICK
        )
        for _ in range(segments_this_tick):
            if project.done:
                break
            inventory = sponsor.resource_inventory
            if inventory.get("stone", 0.0) < HIGHWAY_STONE_PER_SEGMENT:
                break  # out of stone: project pauses
            ty, tx = project.path[project.segments_done]
            inventory["stone"] -= HIGHWAY_STONE_PER_SEGMENT
            sim.world.improvements[ty, tx] = Improvement.ROAD.value
            project.segments_done += 1
        if project.done and not project.completed:
            project.completed = True
            sim.log_event(
                "infrastructure",
                [project.a_id, project.b_id],
                f"Highway completed between "
                f"{_name(sim, project.a_id)} and {_name(sim, project.b_id)}",
            )
    sim._invalidate_cache()


def _name(sim, settlement_id: str) -> str:
    s = next((s for s in sim.settlements if s.id == settlement_id), None)
    return s.name if s else "unknown"


def highway_trade_multiplier(sim, source_id: str, dest_id: str) -> float:
    return 1.0 + (
        HIGHWAY_TRADE_BONUS
        if highway_connected(sim, source_id, dest_id)
        else 0.0
    )


def maybe_start_highways(sim, settlement: Settlement) -> None:
    """Rule-agent hook: sponsor a highway when wealthy enough. Called from
    the auto-road rule so all agent types benefit without new actions."""
    if settlement.era < HIGHWAY_ERA_REQUIREMENT:
        return
    if settlement.resource_inventory.get("stone", 0.0) < (
            HIGHWAY_MIN_STONE_RESERVE * 10):
        return
    idx = sim.settlements.index(settlement)
    for other in sim.settlements:
        if other.id == settlement.id or not other.is_alive:
            continue
        ok, _reason = can_start_highway(sim, settlement, other)
        if ok and sim._territories_adjacent(
                idx, sim.settlements.index(other)):
            start_highway(sim, settlement, other, sim.tick)
            return
