"""Formal treaties with clauses + derived federations (Sprint 34).

Treaties are persisted agreements between two settlements carrying
clauses:
- non_aggression: neither party may raid the other
- trade_pact:     +25% shipment size on routes between the parties
- tribute:        every TRIBUTE_PERIOD_TICKS, the wealthier party pays
                  the poorer a fraction of its stone/wood stockpiles

Federations are DERIVED, never stored: connected components of the
alliance graph with at least FEDERATION_MIN_SIZE members (pure function
of state, same philosophy as eras and market prices). Members ship
+15% to each other.

Determinism: treaty ids are uuid5; acceptance rules are pure predicates;
expiry is a pure function of tick.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .relations import FRIENDLY_THRESHOLD
from .settlement import Settlement

CLAUSE_NON_AGGRESSION = "non_aggression"
CLAUSE_TRADE_PACT = "trade_pact"
CLAUSE_TRIBUTE = "tribute"

TREATY_DURATION_TICKS = 1000
TRADE_PACT_SHIPMENT_BONUS = 0.25
TRIBUTE_PERIOD_TICKS = 100
TRIBUTE_FRACTION_PER_PERIOD = 0.05

FEDERATION_MIN_SIZE = 3
FEDERATION_SHIPMENT_BONUS = 0.15

TREATY_PROPOSAL_CADENCE_TICKS = 250


@dataclass
class Treaty:
    party_a: str
    party_b: str
    clauses: list[str] = field(default_factory=list)
    start_tick: int = 0
    expires_tick: int = 0
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"worldsim/treaty/{self.party_a}/{self.party_b}/"
                f"{self.start_tick}",
            ))

    def partner_of(self, settlement_id: str) -> str | None:
        if settlement_id == self.party_a:
            return self.party_b
        if settlement_id == self.party_b:
            return self.party_a
        return None


def _pair_key(a: str, b: str) -> frozenset:
    return frozenset((a, b))


def treaty_between(sim, a_id: str, b_id: str) -> Treaty | None:
    for treaty in sim.treaties:
        if _pair_key(treaty.party_a, treaty.party_b) == _pair_key(a_id, b_id):
            return treaty
    return None


def has_clause(sim, a_id: str, b_id: str, clause: str) -> bool:
    treaty = treaty_between(sim, a_id, b_id)
    return treaty is not None and clause in treaty.clauses


def active_treaties_of(sim, settlement_id: str) -> list[Treaty]:
    return [
        t for t in sim.treaties
        if settlement_id in (t.party_a, t.party_b)
    ]


def would_accept(sim, proposer: Settlement, target: Settlement,
                 clauses: list[str]) -> tuple[bool, str]:
    """Deterministic acceptance predicate — identical logic on both sides."""
    if not (proposer.is_alive and target.is_alive):
        return False, "settlement_dead"
    if sim.diplomacy.at_war(proposer.id, target.id):
        return False, "at_war"
    score = sim.relations.score(proposer.id, target.id)
    if score < FRIENDLY_THRESHOLD:
        return False, "relations_too_low"
    # Personality friction (S60): aggressive civilizations demand warmer
    # relations before signing away their freedom of action — without
    # this every world treaty-converges to permanent peace instantly.
    aggression = float(target.personality.get("aggression", 0.5))
    required = FRIENDLY_THRESHOLD + max(0.0, aggression - 0.5) * 80.0
    if score < required:
        return False, "target_wary_of_aggression"
    if treaty_between(sim, proposer.id, target.id) is not None:
        return False, "treaty_exists"
    if clauses == [CLAUSE_TRIBUTE]:
        # Tribute-only treaties are imposed by victors (warfare work,
        # Sprint 35) — they are never proposed between friendly pairs.
        return False, "tribute_requires_conflict"
    return True, ""


def propose_treaty(sim, proposer: Settlement, target: Settlement,
                   clauses: list[str], tick: int) -> Treaty | None:
    ok, _reason = would_accept(sim, proposer, target, clauses)
    if not ok:
        return None
    treaty = Treaty(
        party_a=proposer.id,
        party_b=target.id,
        clauses=list(clauses),
        start_tick=tick,
        expires_tick=tick + TREATY_DURATION_TICKS,
    )
    sim.treaties.append(treaty)
    sim.log_event(
        "diplomacy",
        [proposer.id, target.id],
        f"{proposer.name} and {target.name} signed a treaty "
        f"({', '.join(sorted(clauses))})",
    )
    sim.diplomacy.adjust_rep(proposer.id, 5.0)
    sim.diplomacy.adjust_rep(target.id, 5.0)
    return treaty


def expire_treaties(sim) -> None:
    """Remove expired treaties, logging each expiry. Order-stable."""
    survivors = []
    for treaty in sim.treaties:
        if sim.tick >= treaty.expires_tick:
            name_a = _name(sim, treaty.party_a)
            name_b = _name(sim, treaty.party_b)
            sim.log_event(
                "diplomacy",
                [treaty.party_a, treaty.party_b],
                f"Treaty between {name_a} and {name_b} expired",
            )
        else:
            survivors.append(treaty)
    sim.treaties[:] = survivors


def apply_tribute(sim) -> None:
    """Periodic tribute transfers for active tribute treaties."""
    if sim.tick % TRIBUTE_PERIOD_TICKS != 0 or sim.tick == 0:
        return
    by_id = {s.id: s for s in sim.settlements}
    for treaty in sim.treaties:
        if CLAUSE_TRIBUTE not in treaty.clauses:
            continue
        payer = by_id.get(treaty.party_a)
        receiver = by_id.get(treaty.party_b)
        if payer is None or receiver is None or not (
                payer.is_alive and receiver.is_alive):
            continue
        # The wealthier side pays the poorer (deterministic tie-break by id).
        wealth_p = _wealth(payer)
        wealth_r = _wealth(receiver)
        if wealth_p == wealth_r:
            payer, receiver = sorted(
                (payer, receiver), key=lambda s: s.id)
        elif wealth_r > wealth_p:
            payer, receiver = receiver, payer
        for resource in ("stone", "wood"):
            amount = max(0.0, payer.resource_inventory.get(resource, 0.0))
            amount = round(amount * TRIBUTE_FRACTION_PER_PERIOD, 3)
            if amount <= 0:
                continue
            payer.resource_inventory[resource] = (
                payer.resource_inventory.get(resource, 0.0) - amount)
            receiver.resource_inventory[resource] = (
                receiver.resource_inventory.get(resource, 0.0) + amount)


def _wealth(s: Settlement) -> float:
    inventory = s.resource_inventory
    return (
        s.food_stock
        + sum(max(0.0, v) for v in inventory.values())
    )


def _name(sim, settlement_id: str) -> str:
    s = next((s for s in sim.settlements if s.id == settlement_id), None)
    return s.name if s else "unknown"


# ----------------------------------------------------------------------
# Federations (derived from the alliance graph)
# ----------------------------------------------------------------------

def federations(sim) -> list[frozenset]:
    """Connected components of the alliance graph with >= min size.
    Deterministic output: components sorted by their sorted member ids."""
    adjacency: dict[str, set[str]] = {}
    for pair in sim.diplomacy.alliances:
        a, b = tuple(pair)
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    seen: set[str] = set()
    components: list[frozenset] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, component = [start], set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            seen.add(node)
            stack.extend(sorted(adjacency[node] - component))
        if len(component) >= FEDERATION_MIN_SIZE:
            components.append(frozenset(component))
    return sorted(components, key=lambda c: sorted(c))


def federation_of(sim, settlement_id: str) -> frozenset | None:
    for component in federations(sim):
        if settlement_id in component:
            return component
    return None


def federation_shipment_multiplier(sim, source_id: str,
                                   dest_id: str) -> float:
    source_fed = federation_of(sim, source_id)
    if source_fed is not None and dest_id in source_fed:
        return 1.0 + FEDERATION_SHIPMENT_BONUS
    return 1.0


def pact_shipment_multiplier(sim, source_id: str, dest_id: str) -> float:
    return 1.0 + (
        TRADE_PACT_SHIPMENT_BONUS
        if has_clause(sim, source_id, dest_id, CLAUSE_TRADE_PACT)
        else 0.0
    )


def maybe_propose_treaties(sim, settlement: Settlement) -> None:
    """Rule-agent hook (cadence-gated by caller): propose a trade-pact +
    non-aggression treaty to the best-scoring friendly neighbor without
    one. Tribute treaties are imposed via peace terms instead."""
    if settlement.era < 2:
        return
    candidates = []
    for other in sim.neighbors_of(settlement):
        if other.id == settlement.id or not other.is_alive:
            continue
        ok, _reason = would_accept(
            sim, settlement, other,
            [CLAUSE_TRADE_PACT, CLAUSE_NON_AGGRESSION])
        if ok:
            score = sim.relations.score(settlement.id, other.id)
            candidates.append((score, other.name, other))
    if not candidates:
        return
    candidates.sort(key=lambda c: (-c[0], c[1]))
    _score, _name_, best = candidates[0]
    propose_treaty(
        sim, settlement, best,
        [CLAUSE_TRADE_PACT, CLAUSE_NON_AGGRESSION],
        sim.tick,
    )
