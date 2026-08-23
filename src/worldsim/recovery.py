"""Collapse and recovery depth (Sprint 36).

Extends Sprint 5 ruins/happiness with era-aware mechanics:
- Refugee migration: when a settlement dies, up to half its final
  population flees to allies / federation members instead of vanishing.
- Enriched ruins: the ruin site records the dead settlement's era,
  technologies, and half its surviving stockpiles as salvage.
- Knowledge recovery: re-settlers inherit the ruin's technologies and
  salvage — civilizations rebound faster than they rose.
- Building decay: sustained economic scarcity strips buildings one by
  one before the settlement dies (visible decline).

Determinism everywhere: recipient order, decay targets, and salvage
amounts are pure functions of state.
"""

from __future__ import annotations

import numpy as np

from .buildings import BUILDING_SPECS, BuildingType, Improvement
from .settlement import Settlement

REFUGEE_SHARE_NUM = 1                 # migrants = final_pop * 1/2
REFUGEE_SHARE_DEN = 2
MAX_POP_PER_RECIPIENT = 3             # each ally absorbs at most this

SALVAGE_FRACTION = 0.5

# Building decay fires alongside each population-loss collapse event
# (the sim's COLLAPSE_INTERVAL_TICKS cadence) — decline hits people and
# infrastructure together.
BUILDING_DECAY_THRESHOLD_TICKS = 48


def refugee_recipients(sim, dying: Settlement) -> list[Settlement]:
    """Living allies and federation members, deterministic order."""
    from .treaties import federation_of

    recipients: list[Settlement] = []
    seen_ids = {dying.id}
    fed = federation_of(sim, dying.id)
    if fed is not None:
        for sid in sorted(fed):
            s = next(
                (x for x in sim.settlements if x.id == sid), None)
            if s is not None and s.is_alive and sid not in seen_ids:
                recipients.append(s)
                seen_ids.add(sid)
    for pair in sorted(sim.diplomacy.alliances, key=lambda p: sorted(p)):
        if dying.id not in pair:
            continue
        other_id = next(x for x in pair if x != dying.id)
        if other_id in seen_ids:
            continue
        s = next((x for x in sim.settlements if x.id == other_id), None)
        if s is not None and s.is_alive:
            recipients.append(s)
            seen_ids.add(other_id)
    return recipients


def migrate_refugees(sim, dying: Settlement) -> int:
    """Distribute refugees to allies/federation; returns migrants moved.

    Called by the sim right before the settlement is marked destroyed."""
    if not dying.is_alive or dying.population <= 0:
        return 0
    recipients = refugee_recipients(sim, dying)
    if not recipients:
        return 0
    remaining = dying.population * REFUGEE_SHARE_NUM // REFUGEE_SHARE_DEN
    moved = 0
    for recipient in recipients:
        take = min(MAX_POP_PER_RECIPIENT, remaining)
        if take <= 0:
            break
        recipient.population += take
        remaining -= take
        moved += take
    if moved > 0:
        names = ", ".join(r.name for r in recipients[:3])
        sim.log_event(
            "migration",
            [dying.id] + [r.id for r in recipients],
            f"{moved} refugees from {dying.name} resettled among {names}",
        )
    return moved


def salvage_from(settlement: Settlement) -> dict[str, float]:
    """Half of the dying settlement's non-negative stockpiles."""
    inventory = settlement.resource_inventory
    salvage: dict[str, float] = {}
    for resource in sorted(inventory):
        amount = max(0.0, inventory[resource])
        if amount > 0:
            salvage[resource] = round(amount * SALVAGE_FRACTION, 3)
    return salvage


def decay_building(sim, settlement: Settlement) -> bool:
    """Strip one building after prolonged scarcity (lowest-yield first).

    Returns True when a building was lost."""
    owned_idx = sim.settlements.index(settlement)
    improved = np.argwhere(
        np.logical_and(
            sim.world.ownership == owned_idx,
            sim.world.improvements != Improvement.NONE.value,
        )
    )
    if len(improved) == 0:
        return False
    # Deterministic target: lowest food yield, then row-major order —
    # the least valuable building goes first.
    def yield_key(tile):
        y, x = int(tile[0]), int(tile[1])
        imp = Improvement(sim.world.improvements[y, x])
        try:
            btype = next(
                bt for bt in BuildingType
                if Improvement(bt.value + 1) == imp
            )
        except StopIteration:
            return (0, y, x)  # roads go first
        spec = BUILDING_SPECS[btype]
        score = spec.food_output + spec.wood_output + spec.stone_output \
            + spec.metal_output
        return (score, y, x)

    improved = sorted(improved, key=yield_key)
    y, x = int(improved[0][0]), int(improved[0][1])
    sim.world.improvements[y, x] = Improvement.NONE.value
    sim._invalidate_cache()
    sim.log_event(
        "decay",
        [settlement.id],
        f"{settlement.name} lost a building to neglect",
    )
    return True
