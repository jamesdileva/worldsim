"""Sprint 34: treaties with clauses + derived federations."""

import pytest

from worldsim.relations import FRIENDLY_THRESHOLD
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.treaties import (
    CLAUSE_NON_AGGRESSION,
    CLAUSE_TRADE_PACT,
    CLAUSE_TRIBUTE,
    FEDERATION_SHIPMENT_BONUS,
    TRADE_PACT_SHIPMENT_BONUS,
    Treaty,
    apply_tribute,
    expire_treaties,
    federation_of,
    federation_shipment_multiplier,
    federations,
    maybe_propose_treaties,
    pact_shipment_multiplier,
    propose_treaty,
    treaty_between,
    would_accept,
)
from worldsim.world import World


def _sim(n=2, seed=42, size=64) -> Simulation:
    sim = Simulation(World(seed=seed, size=size))
    sim.spawn_settlements(count=n)
    return sim


def _friendly(sim, a=None, b=None):
    living = [s for s in sim.settlements if s.is_alive]
    a = a or living[0]
    b = b or living[1]
    sim.relations.adjust(a.id, b.id, FRIENDLY_THRESHOLD + 5)
    return a, b


# ----------------------------------------------------------------------
# Proposal / acceptance (deterministic predicate)
# ----------------------------------------------------------------------

def test_proposal_requires_friendliness():
    sim = _sim()
    a, b = sim.settlements[:2]
    ok, reason = would_accept(
        sim, a, b, [CLAUSE_TRADE_PACT, CLAUSE_NON_AGGRESSION])
    assert not ok and reason == "relations_too_low"


def test_proposal_blocked_at_war():
    sim = _sim()
    a, b = sim.settlements[:2]
    _friendly(sim, a, b)
    sim.diplomacy.declare_war(a.id, b.id, tick=sim.tick)
    ok, reason = would_accept(sim, a, b, [CLAUSE_TRADE_PACT])
    assert not ok and reason == "at_war"


def test_successful_treaty_signed_and_logged():
    sim = _sim()
    a, b = _friendly(sim)
    treaty = propose_treaty(
        sim, a, b, [CLAUSE_TRADE_PACT, CLAUSE_NON_AGGRESSION], sim.tick)
    assert treaty is not None
    assert treaty_between(sim, a.id, b.id) is treaty
    types = [e.type for e in sim.event_log]
    assert "diplomacy" in types
    # reputation reward on both sides
    assert sim.diplomacy.rep(a.id) > 0


def test_duplicate_treaty_rejected():
    sim = _sim()
    a, b = _friendly(sim)
    assert propose_treaty(
        sim, a, b, [CLAUSE_TRADE_PACT], sim.tick) is not None
    assert propose_treaty(
        sim, b, a, [CLAUSE_NON_AGGRESSION], sim.tick) is None


def test_tribute_only_treaties_never_proposed_between_friends():
    sim = _sim()
    a, b = _friendly(sim)
    assert propose_treaty(sim, a, b, [CLAUSE_TRIBUTE], sim.tick) is None


# ----------------------------------------------------------------------
# Clause effects
# ----------------------------------------------------------------------

def test_non_aggression_blocks_raids_sim_side():
    sim = _sim()
    a, b = _friendly(sim)
    propose_treaty(
        sim, a, b, [CLAUSE_NON_AGGRESSION], sim.tick)
    # Force military-warlike conditions that would otherwise allow raids.
    b.resource_inventory["stone"] = 50.0
    sim.relations.adjust(a.id, b.id, -80)  # hostile anyway; treaty must hold
    targets = sim._raidable_targets(a)
    assert b.id not in targets


def test_trade_pact_boosts_shipments():
    sim = _sim()
    a, b = _friendly(sim)
    route = sim.establish_route(a, b)
    assert route is not None
    a.resource_inventory.update({"wood": 400.0})
    b.resource_inventory.update({"wood": 0.0})
    sim._trade_tick(route)
    normal = b.resource_inventory["wood"]

    propose_treaty(sim, a, b, [CLAUSE_TRADE_PACT], sim.tick)
    assert pact_shipment_multiplier(sim, a.id, b.id) == pytest.approx(
        1.0 + TRADE_PACT_SHIPMENT_BONUS)

    b.resource_inventory.update({"wood": 0.0})
    a.resource_inventory.update({"wood": 400.0})
    sim._trade_tick(route)
    boosted = b.resource_inventory["wood"]
    assert boosted == pytest.approx(normal * (1.0 + TRADE_PACT_SHIPMENT_BONUS))


def test_tribute_transfers_from_richer_to_poorer():
    sim = _sim()
    a, b = _friendly(sim)
    # Mixed-clause treaties may include tribute (tribute-ONLY is reserved
    # for victor-imposed terms).
    treaty = propose_treaty(
        sim, a, b, [CLAUSE_TRADE_PACT, CLAUSE_TRIBUTE], sim.tick)
    assert treaty is not None
    a.resource_inventory.update({"stone": 1000.0, "wood": 1000.0})
    b.resource_inventory.update({"stone": 10.0, "wood": 10.0})
    sim.world.tick = sim.tick + (100 - sim.tick % 100)  # period boundary
    apply_tribute(sim)
    assert a.resource_inventory["stone"] < 1000.0
    assert b.resource_inventory["stone"] > 10.0


def test_non_aggression_reason_surfaces_in_intent_validation():
    from worldsim.actions import Action
    from worldsim.intents import validate_action

    sim = _sim()
    a, b = _friendly(sim)
    propose_treaty(sim, a, b, [CLAUSE_NON_AGGRESSION], sim.tick)
    ok, reason = validate_action(sim, a, Action.INITIATE_RAID)
    assert not ok
    assert reason == "non_aggression_treaty"


# ----------------------------------------------------------------------
# Expiry
# ----------------------------------------------------------------------

def test_treaties_expire_with_event():
    sim = _sim()
    a, b = _friendly(sim)
    treaty = propose_treaty(sim, a, b, [CLAUSE_TRADE_PACT], sim.tick)
    sim.world.tick = treaty.expires_tick + 1
    expire_treaties(sim)
    assert sim.treaties == []
    descriptions = [e.description for e in sim.event_log if e.type == "diplomacy"]
    assert any("expired" in d for d in descriptions)


def test_unexpired_treaties_survive_expiry_pass():
    sim = _sim()
    a, b = _friendly(sim)
    propose_treaty(sim, a, b, [CLAUSE_TRADE_PACT], sim.tick)
    expire_treaties(sim)
    assert len(sim.treaties) == 1


# ----------------------------------------------------------------------
# Federations (derived)
# ----------------------------------------------------------------------

def _tri_alliance(sim):
    """Make settlements 0/1/2 mutually allied."""
    s = [x for x in sim.settlements if x.is_alive][:3]
    sim.diplomacy.form_alliance(s[0].id, s[1].id)
    sim.diplomacy.form_alliance(s[1].id, s[2].id)
    sim.diplomacy.form_alliance(s[0].id, s[2].id)
    return s


def test_federation_derived_from_triangle_of_alliances():
    sim = _sim(n=3)
    members = _tri_alliance(sim)
    feds = federations(sim)
    assert len(feds) == 1
    assert set(members[0].id for m in members) <= set(feds[0])
    ids = {m.id for m in members}
    assert federation_of(sim, members[0].id) == feds[0]
    assert federation_of(sim, "nonexistent") is None


def test_pairwise_alliance_is_not_a_federation():
    sim = _sim(n=3)
    s = [x for x in sim.settlements if x.is_alive]
    sim.diplomacy.form_alliance(s[0].id, s[1].id)
    assert federations(sim) == []


def test_chain_alliances_form_single_federation():
    sim = _sim(n=3)
    s = [x for x in sim.settlements if x.is_alive]
    sim.diplomacy.form_alliance(s[0].id, s[1].id)
    sim.diplomacy.form_alliance(s[1].id, s[2].id)
    feds = federations(sim)
    assert len(feds) == 1 and len(feds[0]) == 3


def test_federation_shipment_bonus_within_members_only():
    sim = _sim(n=4)
    s = [x for x in sim.settlements if x.is_alive]
    for i in range(3):
        for j in range(i + 1, 3):
            sim.diplomacy.form_alliance(s[i].id, s[j].id)
    inside = federation_shipment_multiplier(sim, s[0].id, s[1].id)
    outside = federation_shipment_multiplier(sim, s[0].id, s[3].id)
    assert inside == pytest.approx(1.0 + FEDERATION_SHIPMENT_BONUS)
    assert outside == 1.0


# ----------------------------------------------------------------------
# Rule hook + determinism + persistence
# ----------------------------------------------------------------------

def test_rule_hook_proposes_to_best_neighbor_at_cadence():
    sim = _sim(n=2)
    a, b = sim.settlements[:2]
    a.technologies.extend(["agriculture", "masonry"])
    sim.relations.adjust(a.id, b.id, FRIENDLY_THRESHOLD + 20)
    before = len(sim.treaties)
    maybe_propose_treaties(sim, a)
    assert len(sim.treaties) == before + 1


def test_rule_hook_requires_era_two():
    sim = _sim(n=2)
    a, b = sim.settlements[:2]
    sim.relations.adjust(a.id, b.id, FRIENDLY_THRESHOLD + 20)
    maybe_propose_treaties(sim, a)
    assert len(sim.treaties) == 0


def test_deterministic_ids():
    sim1, sim2 = _sim(), _sim()
    a1, b1 = _friendly(sim1)
    a2, b2 = _friendly(sim2)
    t1 = propose_treaty(sim1, a1, b1, [CLAUSE_TRADE_PACT], tick=77)
    t2 = propose_treaty(sim2, a2, b2, [CLAUSE_TRADE_PACT], tick=77)
    assert t1.id == t2.id


def test_treaty_round_trips_serialization():
    from worldsim.db import _decode_treaty, _encode_treaty

    sim = _sim()
    a, b = _friendly(sim)
    treaty = propose_treaty(sim, a, b, [CLAUSE_TRADE_PACT,
                                        CLAUSE_NON_AGGRESSION], tick=9)
    restored = _decode_treaty(_encode_treaty(treaty))
    assert restored.id == treaty.id
    assert restored.clauses == treaty.clauses
    assert restored.party_a == a.id and restored.party_b == b.id


def test_frozen_contract_unchanged():
    from worldsim.actions import NUM_ACTIONS
    from worldsim.agents import OBSERVATION_DIM

    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
