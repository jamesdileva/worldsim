"""LLM advice -> action intent mapping + validation (Sprint 28).

Contract:
- Advice phrases map onto the FROZEN action space by keyword matching;
  nothing here may renumber or extend action IDs.
- Every candidate action is validated against existing sim mechanics
  BEFORE execution; invalid intents are dropped with telemetry reasons.
- No LLM output can execute an illegal action: validation reuses exactly
  the mechanic methods the sim itself enforces (defense in depth).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .actions import Action
from .advice import StrategicAdvice
from .agents import RAID_CADENCE_TICKS
from .buildings import BuildingType
from .settlement import Settlement
from .world import UNOWNED


# Phrase keywords -> frozen action ID. First matching rule per phrase
# wins; keyword order within each tuple = priority. Matching is on word
# STARTS (\b + prefix), so stems like "agricultur(al)" work while "ore"
# cannot falsely fire inside "more". IDs are Action members only.
PHRASE_RULES: tuple[tuple[tuple[str, ...], Action], ...] = (
    (("raid", "attack", "plunder", "war"), Action.INITIATE_RAID),
    (("peace", "truce", "ceasefire"), Action.OFFER_PEACE),
    (("granary", "storage", "food cap", "food reserve"),
     Action.BUILD_GRANARY),
    (("sawmill", "lumber", "timber"), Action.BUILD_SAWMILL),
    (("mine", "ore", "metal income"), Action.BUILD_MINE),
    (("farm", "agricultur", "crop", "grow food"), Action.BUILD_FARM),
    (("road",), Action.BUILD_ROAD),
    (("trade", "commerce", "route"), Action.ESTABLISH_TRADE_ROUTE),
    (("claim", "expand territor", "expansion", "territor"),
     Action.CLAIM_TERRITORY),
    (("wait", "hold", "consolidate", "nothing"), Action.WAIT),
)

_PHRASE_MATCHERS: tuple[tuple[tuple[re.Pattern, ...], Action], ...] = tuple(
    ((tuple(re.compile(r"\b" + re.escape(k)) for k in keywords), action)
     for keywords, action in PHRASE_RULES)
)

BUILD_ACTIONS: dict[Action, BuildingType] = {
    Action.BUILD_FARM: BuildingType.FARM,
    Action.BUILD_SAWMILL: BuildingType.SAWMILL,
    Action.BUILD_MINE: BuildingType.MINE,
    Action.BUILD_GRANARY: BuildingType.GRANARY,
}


@dataclass
class IntentTelemetry:
    """Per-agent counters for mapped/dropped intents (never sim state)."""

    phrases_seen: int = 0
    phrases_mapped: int = 0
    phrases_unmapped: int = 0
    actions_validated: int = 0
    actions_dropped: int = 0
    drop_reasons: dict[str, int] = field(default_factory=dict)
    fallback_decisions: int = 0
    advice_failures: int = 0

    def record_drop(self, reason: str) -> None:
        self.actions_dropped += 1
        self.drop_reasons[reason] = self.drop_reasons.get(reason, 0) + 1


def map_advice_to_actions(advice: StrategicAdvice,
                          telemetry: IntentTelemetry | None = None
                          ) -> list[Action]:
    """Map priority phrases to candidate action IDs (deduped, ordered).

    Unmapped phrases are counted, never fatal."""
    actions: list[Action] = []
    seen: set[Action] = set()
    lowered = [p.lower() for p in advice.priorities]
    if telemetry is not None:
        telemetry.phrases_seen += len(lowered)
    for phrase in lowered:
        matched = None
        for patterns, action in _PHRASE_MATCHERS:
            if any(pattern.search(phrase) for pattern in patterns):
                matched = action
                break
        if matched is None:
            if telemetry is not None:
                telemetry.phrases_unmapped += 1
            continue
        if telemetry is not None:
            telemetry.phrases_mapped += 1
        if matched not in seen:
            seen.add(matched)
            actions.append(matched)
    return actions


def validate_action(sim, settlement: Settlement,
                    action_id: Action) -> tuple[bool, str]:
    """Read-only legality check against current world state.

    Returns (ok, drop_reason). Reuses the exact predicates the sim's own
    handlers enforce so an accepted intent cannot fail 'illegally'."""
    if action_id in BUILD_ACTIONS:
        building_type = BUILD_ACTIONS[action_id]
        spec_costs = _building_cost(building_type)
        if spec_costs is None:
            return False, "unknown_building"
        wood_cost, stone_cost = spec_costs
        if not sim.can_afford(settlement, wood_cost, stone_cost):
            return False, f"unaffordable_{building_type.name.lower()}"
        if sim.find_building_site(settlement, building_type) is None:
            return False, f"no_site_{building_type.name.lower()}"
        return True, ""

    if action_id == Action.BUILD_ROAD:
        if not _has_road_candidate(sim, settlement):
            return False, "no_road_site"
        return True, ""

    if action_id == Action.CLAIM_TERRITORY:
        if not _has_claimable_neighbor(sim, settlement):
            return False, "no_unowned_adjacent"
        return True, ""

    if action_id == Action.ESTABLISH_TRADE_ROUTE:
        partners = [
            other for other in sim.neighbors_of(settlement)
            if other.is_alive and sim.can_establish_route(settlement, other)
        ]
        if not partners:
            return False, "no_valid_trade_partner"
        return True, ""

    if action_id == Action.INITIATE_RAID:
        last = sim.last_raid_tick.get(settlement.id)
        tick = sim.tick
        if last is not None and tick - last < RAID_CADENCE_TICKS:
            return False, "raid_cadence"
        if not sim._raidable_targets(settlement):
            return False, "no_raidable_targets"
        return True, ""

    if action_id == Action.OFFER_PEACE:
        if not sim.diplomacy.wars_of(settlement.id):
            return False, "not_at_war"
        return True, ""

    if action_id in (Action.WAIT, Action.IDLE, Action.BOOST_MORALE):
        return True, ""

    # Wired-but-context-free actions (EXPAND_ROAD_NETWORK etc.) and any
    # unwired/no-op ID: safe defaults, executed as no-ops by the sim.
    return True, ""


def _building_cost(building_type: BuildingType) -> tuple[float, float] | None:
    from .buildings import BUILDING_SPECS

    spec = BUILDING_SPECS.get(building_type)
    if spec is None:
        return None
    return spec.cost_wood, spec.cost_stone


def _has_claimable_neighbor(sim, settlement: Settlement) -> bool:
    ownership = sim.world.ownership
    size = sim.world.size
    for x, y in sim.territory_of(settlement):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if (nx, ny) == (x, y):
                    continue
                if 0 <= nx < size and 0 <= ny < size:
                    if ownership[ny, nx] == UNOWNED:
                        return True
    return False


def _has_road_candidate(sim, settlement: Settlement) -> bool:
    from .buildings import Improvement, ROAD_COST_STONE
    from .tiles import TerrainType

    if settlement.resource_inventory.get("stone", 0) < ROAD_COST_STONE:
        return False
    improvements = sim.world.improvements
    for x, y in sim.territory_of(settlement):
        if improvements[y, x] == Improvement.NONE.value:
            if TerrainType(sim.world.terrain[y, x]) != TerrainType.WATER:
                return True
    return False
