"""Warfare proper: armies, field battles, sieges (Sprint 35).

Extends Sprint 9 raids into real warfare:
- Settlements train armies via the freshly wired TRAIN_RAIDER /
  TRAIN_DEFENDER / FORTIFY_BORDER actions (reserved slots, frozen IDs).
- Warring pairs clash in deterministic field battles every
  BATTLE_INTERVAL_TICKS. Home ground and forts favor defenders.
- Three consecutive attacker victories = SIEGE: the defender sues for
  peace and the victor IMPOSES a tribute treaty (fulfilling the Sprint
  34 promise that tribute-only treaties await a victor).
- Stale wars end in white peace after WAR_EXHAUSTION_TICKS.

Determinism: battle outcomes are pure functions of (seed, tick, state);
rng streams are seeded per battle. Armies starve without food.
"""

from __future__ import annotations

import random
import zlib

from .settlement import Settlement
from .treaties import CLAUSE_TRIBUTE, TREATY_DURATION_TICKS, Treaty

BATTLE_INTERVAL_TICKS = 100
SIEGE_THRESHOLD = 3                 # attacker wins needed for capitulation
HOME_DEFENSE_BONUS = 1.25           # fighting on own soil
FORT_DEFENSE_PER_LEVEL = 0.25       # +25% defense per fort level
MAX_FORT_LEVEL = 3
WINNER_ARMY_LOSS_FRAC = 0.15
LOSER_ARMY_LOSS_FRAC = 0.30
WAR_EXHAUSTION_TICKS = 1500

TRAIN_RAIDER_FOOD_COST = 10.0
TRAIN_RAIDER_ARMY_GAIN = 2.0
TRAIN_DEFENDER_FOOD_COST = 10.0
TRAIN_DEFENDER_WOOD_COST = 2.0
TRAIN_DEFENDER_ARMY_GAIN = 1.0
TRAIN_DEFENDER_FORT_GAIN = 1
FORTIFY_STONE_COST = 8.0
FORTIFY_FORT_GAIN = 1

ARMY_UPKEEP_FOOD_PER_POINT = 0.01   # per tick


def can_train_raider(settlement: Settlement) -> tuple[bool, str]:
    if not settlement.is_alive:
        return False, "settlement_dead"
    if settlement.food_stock < TRAIN_RAIDER_FOOD_COST:
        return False, "unaffordable_training"
    return True, ""


def can_train_defender(settlement: Settlement) -> tuple[bool, str]:
    if not settlement.is_alive:
        return False, "settlement_dead"
    inventory = settlement.resource_inventory
    if (settlement.food_stock < TRAIN_DEFENDER_FOOD_COST
            or inventory.get("wood", 0.0) < TRAIN_DEFENDER_WOOD_COST):
        return False, "unaffordable_training"
    return True, ""


def can_fortify(settlement: Settlement) -> tuple[bool, str]:
    if not settlement.is_alive:
        return False, "settlement_dead"
    if settlement.fort_level >= MAX_FORT_LEVEL:
        return False, "fort_maxed"
    if settlement.resource_inventory.get("stone", 0.0) < FORTIFY_STONE_COST:
        return False, "unaffordable_fortification"
    return True, ""


def _strength(settlement: Settlement, attacking: bool) -> float:
    strength = max(0.0, settlement.army)
    if not attacking:
        strength *= HOME_DEFENSE_BONUS * (
            1.0 + FORT_DEFENSE_PER_LEVEL * settlement.fort_level)
    return strength


def resolve_battles(sim) -> None:
    """One resolution pass over all active wars. Deterministic."""
    by_id = {s.id: s for s in sim.settlements}
    for war_key, war in sorted(
        sim.diplomacy.wars.items(), key=lambda kv: sorted(kv[0])
    ):
        a_id, b_id = tuple(war_key)
        attacker = by_id.get(a_id)
        defender = by_id.get(b_id)
        if attacker is None or defender is None or not (
            attacker.is_alive and defender.is_alive
        ):
            continue
        start = war.get("start_tick", sim.tick)

        # War exhaustion: stale wars end in white peace.
        if sim.tick - start >= WAR_EXHAUSTION_TICKS:
            _white_peace(sim, attacker, defender)
            continue

        # Periodic battle.
        next_battle = war.get("next_battle_tick", start + BATTLE_INTERVAL_TICKS)
        if sim.tick < next_battle:
            continue
        war["next_battle_tick"] = sim.tick + BATTLE_INTERVAL_TICKS
        if attacker.army <= 0 and defender.army <= 0:
            continue  # nobody took the field this round

        s_att = _strength(attacker, attacking=True)
        s_def = _strength(defender, attacking=False)
        rng = random.Random(
            (sim.world.seed ^ 0xBA771E) + sim.tick * 7919
            + zlib.crc32(f"{a_id}|{b_id}".encode("utf-8"))
        )
        attacker_won = rng.random() < s_att / max(s_att + s_def, 1e-9)

        winner, loser = (
            (attacker, defender) if attacker_won else (defender, attacker))
        winner.army = max(0.0, winner.army * (1.0 - WINNER_ARMY_LOSS_FRAC))
        loser.army = max(0.0, loser.army * (1.0 - LOSER_ARMY_LOSS_FRAC))

        if attacker_won:
            defender.siege_progress += 1
        else:
            attacker.siege_progress = 0

        outcome = (
            f"{attacker.name} won the field battle against {defender.name}"
            if attacker_won
            else f"{defender.name} repelled {attacker.name}'s assault"
        )
        sim.log_event("battle", [a_id, b_id], outcome)

        # Siege: the defender capitulates and pays tribute.
        if (
            attacker_won
            and defender.siege_progress >= SIEGE_THRESHOLD
        ):
            _impose_victors_peace(sim, attacker, defender)


def _white_peace(sim, a: Settlement, b: Settlement) -> None:
    sim.diplomacy.conclude_peace(a.id, b.id)
    a.siege_progress = 0
    b.siege_progress = 0
    sim.log_event(
        "peace",
        [a.id, b.id],
        f"{a.name} and {b.name} ended their exhausted war "
        f"(white peace)",
    )


def _impose_victors_peace(sim, victor: Settlement,
                          defeated: Settlement) -> None:
    """End the war with a victor-imposed TRIBUTE treaty on the loser.

    This is the promised counterpart of treaties.py's rule that
    tribute-only treaties require an actual conflict."""
    sim.diplomacy.conclude_peace(victor.id, defeated.id)
    defeated.siege_progress = 0
    defeated.army = max(0.0, defeated.army * 0.5)
    treaty = Treaty(
        party_a=victor.id,
        party_b=defeated.id,
        clauses=[CLAUSE_TRIBUTE],
        start_tick=sim.tick,
        expires_tick=sim.tick + TREATY_DURATION_TICKS,
    )
    sim.treaties.append(treaty)
    sim.relations.adjust(victor.id, defeated.id, -20)
    sim.log_event(
        "diplomacy",
        [victor.id, defeated.id],
        f"{defeated.name} capitulated after {SIEGE_THRESHOLD} defeats; "
        f"{victor.name} imposed a tribute treaty",
    )


def apply_army_upkeep(settlement: Settlement) -> None:
    """Armies eat. Starving settlements see their armies melt."""
    if not settlement.is_alive or settlement.army <= 0:
        return
    upkeep = settlement.army * ARMY_UPKEEP_FOOD_PER_POINT
    if settlement.food_stock >= upkeep:
        settlement.food_stock -= upkeep
    else:
        settlement.food_stock = 0.0
        settlement.army = max(0.0, settlement.army * 0.95)
