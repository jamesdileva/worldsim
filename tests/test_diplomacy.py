import pytest

from worldsim.actions import Action
from worldsim.agents import RuleBasedAgent
from worldsim.buildings import BuildingType
from worldsim.db import WorldStore
from worldsim.diplomacy import (
    REPUTATION_DECAY_PER_TICK,
    WAR_RAID_THRESHOLD,
    WAR_WINDOW_TICKS,
    DiplomacyState,
)
from worldsim.relations import RelationMatrix
from worldsim.simulation import Simulation
from worldsim.tiles import TerrainType
from worldsim.world import World


def make_sim(seed: int = 42, count: int = 2) -> tuple[Simulation, list]:
    sim = Simulation(World(seed=seed))
    settlements = sim.spawn_settlements(count=count)
    return sim, settlements


def force_adjacent(sim: Simulation, a, b) -> None:
    idx_a = sim.settlements.index(a)
    idx_b = sim.settlements.index(b)
    for (y, x), idx in (
        ((a.spawn_y, a.spawn_x + 2), idx_a),
        ((a.spawn_y, a.spawn_x + 3), idx_b),
    ):
        if sim.world.ownership[y, x] == -1:
            sim.world.ownership[y, x] = idx


def _prepare_war(sim: Simulation, a, b) -> None:
    force_adjacent(sim, a, b)
    sim.relations.adjust(a.id, b.id, -60)
    sim._neighbors_cache.clear()
    sim._refresh_contested_zones()
    for s in (a, b):
        s.food_stock = 400
        s.resource_inventory["wood"] = 200
        s.resource_inventory["stone"] = 150
    # Build on a contested tile owned by the defender so raids have targets.
    idx_b = sim.settlements.index(b)
    contested_b = [
        (x, y)
        for (x, y) in sim.contested
        if sim.world.ownership[y, x] == idx_b
        and TerrainType(sim.world.terrain[y, x]) != TerrainType.WATER
    ]
    if contested_b:
        x, y = sorted(contested_b)[0]
        terrain = TerrainType(sim.world.terrain[y, x])
        if terrain in (TerrainType.PLAINS, TerrainType.FERTILE):
            building = BuildingType.FARM
        elif terrain is TerrainType.FOREST:
            building = BuildingType.SAWMILL
        elif terrain is TerrainType.MOUNTAIN:
            building = BuildingType.MINE
        else:
            building = BuildingType.GRANARY
        assert sim.build_at(b, building, x=x, y=y)


# ----------------------------------------------------------------------
# War declaration via raid escalation
# ----------------------------------------------------------------------

def test_war_declared_after_three_raids_in_window():
    d = DiplomacyState()
    assert d.record_raid("a", "b", tick=10) is False
    assert d.record_raid("a", "b", tick=200) is False
    assert d.record_raid("a", "b", tick=400) is True
    assert d.at_war("a", "b")


def test_no_war_before_threshold():
    d = DiplomacyState()
    d.record_raid("a", "b", tick=10)
    d.record_raid("a", "b", tick=20)
    assert not d.at_war("a", "b")


def test_old_raids_fall_out_of_window():
    d = DiplomacyState()
    d.record_raid("a", "b", tick=10)
    d.record_raid("a", "b", tick=20)
    # Third raid arrives after the first two aged out of the 500-tick window.
    assert d.record_raid("a", "b", tick=10 + WAR_WINDOW_TICKS + 5) is False
    assert not d.at_war("a", "b")


def test_war_declared_by_simulation_and_logged():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    a.personality["aggression"] = 1.0

    war_declared = False
    # Jump 250 ticks per attempt: respects the 200-tick raid cadence while
    # keeping all three raids inside the 500-tick escalation window.
    for _ in range(10):
        sim.world.tick += 250
        sim._neighbors_cache.clear()
        sim._refresh_contested_zones()
        sim.initiate_raid(a)
        if sim.diplomacy.at_war(a.id, b.id):
            war_declared = True
            break
    assert war_declared
    war_events = [e for e in sim.event_log if e.type == "war"]
    assert any("War declared" in e.description for e in war_events)


def test_war_kills_trade_route():
    sim, (a, b) = make_sim(seed=42, count=2)
    force_adjacent(sim, a, b)
    route = sim.establish_route(a, b)
    assert route is not None and route.active
    sim.diplomacy.declare_war(a.id, b.id, sim.tick)
    sim.relations.set_score(a.id, b.id, -100.0)
    sim._trade_tick(route)
    assert not route.active


# ----------------------------------------------------------------------
# Alliances from mutual trade
# ----------------------------------------------------------------------

def _force_mutual_trades(sim: Simulation, a, b):
    """Manually alternate donor direction three times on one route."""
    route = sim.establish_route(a, b)
    assert route is not None
    a.resource_inventory["wood"] = 300.0
    b.resource_inventory["stone"] = 300.0
    # Alternate which side has the surplus so donors alternate.
    pattern = [("wood", "a"), ("stone", "b"), ("wood", "a"),
               ("stone", "b")]
    for resource, side in pattern:
        if side == "a":
            a.resource_inventory[resource] = 300.0
            b.resource_inventory[resource] = 0.0
        else:
            b.resource_inventory[resource] = 300.0
            a.resource_inventory[resource] = 0.0
        sim._trade_tick(route)


def test_alliance_forms_after_three_mutual_trades():
    sim, (a, b) = make_sim(seed=42, count=2)
    force_adjacent(sim, a, b)
    _force_mutual_trades(sim, a, b)
    assert sim.diplomacy.is_allied(a.id, b.id)
    alliance_events = [e for e in sim.event_log if e.type == "alliance"]
    assert len(alliance_events) == 1


def test_same_side_trades_do_not_form_alliance():
    sim, (a, b) = make_sim(seed=42, count=2)
    force_adjacent(sim, a, b)
    route = sim.establish_route(a, b)
    a.resource_inventory["wood"] = 500.0
    for _ in range(6):  # one-way flow only
        sim._trade_tick(route)
    assert not sim.diplomacy.is_allied(a.id, b.id)


def test_allies_cannot_raid_each_other():
    sim, (a, b) = make_sim(seed=42, count=2)
    force_adjacent(sim, a, b)
    _force_mutual_trades(sim, a, b)
    assert sim.diplomacy.is_allied(a.id, b.id)
    # Even at hostile relations, allies are protected by the handler.
    sim.relations.set_score(a.id, b.id, -80.0)
    sim._neighbors_cache.clear()
    assert not sim.initiate_raid(a)


# ----------------------------------------------------------------------
# Peace treaties
# ----------------------------------------------------------------------

def test_one_sided_offer_does_not_end_war():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    sim.diplomacy.declare_war(a.id, b.id, sim.tick)
    # Only b offers; a (the enemy) has sent no offer.
    sim.diplomacy.offer_peace(b.id, a.id, sim.tick)
    assert sim.diplomacy.has_live_offer(b.id, a.id, sim.tick)
    assert not sim.diplomacy.has_live_offer(a.id, b.id, sim.tick)
    # One offer alone never concludes peace.
    assert sim.diplomacy.at_war(a.id, b.id)


def test_acceptance_sends_matching_offer_and_concludes():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    sim.diplomacy.declare_war(a.id, b.id, sim.tick)
    sim.diplomacy.offer_peace(b.id, a.id, sim.tick)
    # a accepts: its acceptance constitutes sending its own offer, so both
    # parties' offers exist -> treaty concludes.
    rc = sim._act_accept_peace(a)
    assert rc is True
    assert not sim.diplomacy.at_war(a.id, b.id)


def test_bilateral_offers_conclude_peace_with_tribute():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    sim.diplomacy.declare_war(a.id, b.id, sim.tick)
    sim.relations.set_score(a.id, b.id, -90.0)

    # a escalates naturally: three raids inside the window -> war declared,
    # and the war log records a as the aggressor.
    for t in (10, 200, 400):
        sim.diplomacy.record_raid(a.id, b.id, tick=t)
    sim.relations.set_score(a.id, b.id, -90.0)

    # a is the recorded aggressor, so a pays tribute.
    a.food_stock = 1000.0
    victim_food_before = b.food_stock

    sim.diplomacy.offer_peace(a.id, b.id, sim.tick)
    assert sim.diplomacy.has_live_offer(a.id, b.id, sim.tick)
    rc = sim._act_accept_peace(b)
    assert rc is True
    assert not sim.diplomacy.at_war(a.id, b.id)
    assert b.food_stock > victim_food_before
    assert a.food_stock < 1000.0
    peace_events = [e for e in sim.event_log if e.type == "peace"]
    assert len(peace_events) == 1


def test_expired_offer_not_acceptable():
    d = DiplomacyState()
    d.offer_peace("a", "b", tick=100)
    assert d.has_live_offer("a", "b", tick=250)
    assert not d.has_live_offer("a", "b", tick=301)
    d.expire_stale_offers(301)
    assert d.peace_offers.get("a", {}).get("b") is None


# ----------------------------------------------------------------------
# Reputation
# ----------------------------------------------------------------------

def test_reputation_decays_during_non_interaction():
    sim, (a,) = make_sim(seed=42, count=1)
    start_rep = sim.diplomacy.rep(a.id)
    for _ in range(100):
        sim.step()
    expected_decay = 100 * REPUTATION_DECAY_PER_TICK  # 0.1 per 100 ticks
    actual = start_rep - sim.diplomacy.rep(a.id)
    assert actual >= expected_decay * 0.9  # tolerance for interaction ticks


def test_interacting_settlement_does_not_decay():
    sim, (a, b) = make_sim(seed=42, count=2)
    force_adjacent(sim, a, b)
    route = sim.establish_route(a, b)
    a.resource_inventory["wood"] = 500.0
    rep_a_before = sim.diplomacy.rep(a.id)
    rep_b_before = sim.diplomacy.rep(b.id)
    sim._trade_tick(route)
    # Both interacted this tick: no decay applied to either.
    assert sim.diplomacy.rep(a.id) >= rep_a_before
    assert sim.diplomacy.rep(b.id) >= rep_b_before


def test_raid_costs_reputation():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    rep_before = sim.diplomacy.rep(a.id)
    sim.world.tick += 7919
    sim.initiate_raid(a)
    assert sim.diplomacy.rep(a.id) <= rep_before - 5.0 + 1e-9


def test_low_reputation_blocks_new_trade():
    sim, (a, b) = make_sim(seed=42, count=2)
    force_adjacent(sim, a, b)
    sim.diplomacy.adjust_rep(a.id, -80.0)
    assert not sim.can_establish_route(a, b)


# ----------------------------------------------------------------------
# Agent diplomacy behavior
# ----------------------------------------------------------------------

def test_peaceful_agent_at_war_offers_peace():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    sim.diplomacy.declare_war(a.id, b.id, sim.tick)
    a.personality["aggression"] = 0.2
    agent = RuleBasedAgent(seed=21, settlement_index=0)
    agent.EPSILON = 0.0
    offered = False
    for _ in range(120):
        sim.world.tick += 1
        obs = agent.observe(sim, a)
        if agent.decide(obs) == int(Action.OFFER_PEACE):
            offered = True
            break
    assert offered


def test_highly_aggressive_never_accepts_peace():
    sim, (a, b) = make_sim(seed=42, count=2)
    _prepare_war(sim, a, b)
    sim.diplomacy.declare_war(a.id, b.id, sim.tick)
    sim.diplomacy.offer_peace(b.id, a.id, sim.tick)
    a.personality["aggression"] = 1.0
    agent = RuleBasedAgent(seed=21, settlement_index=0)
    agent.EPSILON = 0.0
    accepted = False
    for _ in range(120):
        sim.world.tick += 1
        obs = agent.observe(sim, a)
        if agent.decide(obs) == int(Action.ACCEPT_PEACE):
            accepted = True
            break
    assert not accepted


# ----------------------------------------------------------------------
# Persistence & determinism
# ----------------------------------------------------------------------

def test_diplomacy_state_persists():
    store = WorldStore(":memory:")
    try:
        sim, settlements = make_sim(seed=42, count=2)
        a, b = settlements
        _prepare_war(sim, a, b)
        sim.diplomacy.declare_war(a.id, b.id, sim.tick)
        sim.diplomacy.adjust_rep(a.id, -25.0)
        wid = store.save_world(
            sim.world,
            sim.settlements,
            relations=sim.relations,
            event_log=sim.event_log,
            diplomacy=sim.diplomacy,
        )
        snap = store.load_latest_snapshot(wid)
        diplo = snap[-5]  # ...diplomacy, memory, highways, treaties, zones
        assert diplo.at_war(a.id, b.id)
        assert diplo.rep(a.id) == pytest.approx(-25.0)
    finally:
        store.close()


def test_competition_run_stays_deterministic():
    def run(seed):
        sim, settlements = make_sim(seed=seed, count=4)
        history = []
        for _ in range(120):
            sim.step()
            history.append(
                tuple(s.population for s in settlements)
                + (len(sim.event_log),)
            )
        return history

    assert run(31337) == run(31337)
