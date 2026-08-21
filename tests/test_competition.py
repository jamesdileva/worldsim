import numpy as np
import pytest

from worldsim.actions import Action
from worldsim.agents import RuleBasedAgent
from worldsim.buildings import BuildingType, Improvement
from worldsim.db import WorldStore
from worldsim.relations import (
    RAID_SUCCESS_PENALTY,
    RelationMatrix,
    relation_label,
)
from worldsim.simulation import (
    NEIGHBOR_SPAWN_DISTANCE,
    RAID_BUILDING_DEBUFF_TICKS,
    Simulation,
)
from worldsim.tiles import TerrainType
from worldsim.world import World


def make_sim(seed: int = 42, count: int = 2) -> tuple[Simulation, list]:
    sim = Simulation(World(seed=seed))
    settlements = sim.spawn_settlements(count=count)
    return sim, settlements


def force_adjacent(sim: Simulation, a, b) -> None:
    """Test helper: claim tiles so territories touch."""
    idx_a = sim.settlements.index(a)
    idx_b = sim.settlements.index(b)
    tiles = [
        ((a.spawn_y, a.spawn_x + 2), idx_a),
        ((a.spawn_y, a.spawn_x + 3), idx_b),
    ]
    for (y, x), idx in tiles:
        if sim.world.ownership[y, x] == -1:
            sim.world.ownership[y, x] = idx


# ----------------------------------------------------------------------
# Relations
# ----------------------------------------------------------------------

def test_relation_matrix_defaults_and_labels():
    m = RelationMatrix()
    assert m.score("a", "b") == 0.0
    assert m.label("a", "b") == "neutral"
    m.adjust("a", "b", -50)
    assert m.is_hostile("a", "b")
    assert m.label("a", "b") == "hostile"
    assert m.is_hostile("b", "a")  # symmetric


def test_relations_decay_toward_neutral():
    m = RelationMatrix()
    m.adjust("a", "b", -50)
    for _ in range(2500):
        m.decay_tick()
    assert abs(m.score("a", "b")) < 1.0
    assert m.label("a", "b") == "neutral"


def test_relation_dict_round_trip():
    m = RelationMatrix()
    m.adjust("a", "b", -30)
    m.adjust("c", "d", 40)
    restored = RelationMatrix.from_dict(m.to_dict())
    assert restored.score("a", "b") == m.score("a", "b")
    assert restored.score("c", "d") == m.score("c", "d")


# ----------------------------------------------------------------------
# Neighbors
# ----------------------------------------------------------------------

def test_neighbors_detected_within_distance():
    sim, settlements = make_sim(seed=999, count=5)
    # At least one settlement pair must be neighbors on this seed.
    assert any(sim.neighbors_of(s) for s in settlements)


def test_distant_settlements_not_neighbors():
    sim, (a,) = make_sim(seed=42, count=1)
    # A settlement spawned far away is not a neighbor.
    from worldsim.settlement import Settlement

    far = Settlement(name="Faraway", spawn_x=0, spawn_y=0)
    sim.settlements.append(far)
    neighbors = sim.neighbors_of(a)
    assert all(n.id != far.id for n in neighbors)
    dist = max(abs(far.spawn_x - a.spawn_x), abs(far.spawn_y - a.spawn_y))
    assert dist > NEIGHBOR_SPAWN_DISTANCE


def test_five_settlements_spawn_without_overlap():
    sim, settlements = make_sim(seed=999, count=5)
    assert len(settlements) == 5
    for s in settlements:
        assert len(sim.territory_of(s)) == 9
    total_owned = int((sim.world.ownership != -1).sum())
    assert total_owned == 45  # 5 x 9, no overlap


# ----------------------------------------------------------------------
# Raids
# ----------------------------------------------------------------------

def _prepare_war(sim: Simulation, a, b) -> None:
    """Make a and b hostile neighbors with a contested defended building."""
    force_adjacent(sim, a, b)
    sim.relations.adjust(a.id, b.id, -60)
    sim._neighbors_cache.clear()
    sim._refresh_contested_zones()
    # Build on a contested tile owned by b so the raid has a valid target.
    idx_b = sim.settlements.index(b)
    contested_b = [
        (x, y)
        for (x, y) in sim.contested
        if sim.world.ownership[y, x] == idx_b
        and TerrainType(sim.world.terrain[y, x]) != TerrainType.WATER
    ]
    assert contested_b, "no contested tiles owned by defender"
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
    b.resource_inventory["wood"] = 30
    b.resource_inventory["stone"] = 15
    assert sim.build_at(b, building, x=x, y=y)


def test_raid_reduces_building_output_for_200_ticks():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    b.personality["aggression"] = 0.0
    a.personality["aggression"] = 1.0

    income_before = sim.food_income(b)
    assert income_before > 0

    succeeded = False
    for attempt_seed in range(20):
        sim.world.tick += 7919  # shift the raid RNG
        if sim.initiate_raid(a):
            succeeded = True
            break
    assert succeeded, "raid never succeeded in 20 attempts"

    during = sim.food_income(b)
    assert during < income_before  # debuff applied

    # Advance past the debuff window.
    sim.world.tick += RAID_BUILDING_DEBUFF_TICKS + 1
    sim.building_debuffs = [
        d for d in sim.building_debuffs if d.active(sim.tick)
    ]
    after = sim.food_income(b)
    assert after == pytest.approx(income_before)


def test_successful_raid_steals_resources():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    a.personality["aggression"] = 1.0
    b.resource_inventory["wood"] = 50.0
    b.resource_inventory["stone"] = 50.0

    stole_something = False
    for attempt in range(30):
        sim.world.tick += 104729
        before_wood = a.resource_inventory.get("wood", 0.0)
        if sim.initiate_raid(a):
            gained = a.resource_inventory.get("wood", 0.0) - before_wood
            stole_something = True
            assert gained > 0 or b.resource_inventory["wood"] < 50.0
            break
    assert stole_something


def test_raid_worsens_relations_and_logs_event():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    score_before = sim.relations.score(a.id, b.id)
    events_before = len(sim.event_log)

    raided = False
    for attempt in range(30):
        sim.world.tick += 65537
        if sim.initiate_raid(a):
            raided = True
            break
    assert raided
    score_after = sim.relations.score(a.id, b.id)
    assert score_after <= score_before - 20  # at least the attempted penalty
    assert len(sim.event_log) > events_before
    raid_events = [e for e in sim.event_log if e.type == "raid"]
    assert raid_events
    assert f"{a.name} raided {b.name}" in raid_events[-1].description


def test_peaceful_settlements_do_not_raid():
    """Low-aggression agent never chooses INITIATE_RAID even at war."""
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    a.personality["aggression"] = 0.1
    agent = RuleBasedAgent(seed=17, settlement_index=0)
    agent.EPSILON = 0.0
    for i in range(400):
        sim.world.tick += 1
        obs = agent.observe(sim, a)
        action = agent.decide(obs)
        assert action != int(Action.INITIATE_RAID)


def test_aggressive_agent_raids_at_war():
    """High-aggression agent eventually chooses INITIATE_RAID at war."""
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    a.personality["aggression"] = 1.0
    a.food_stock = 400.0  # non-famine so the raid branch is reachable
    agent = RuleBasedAgent(seed=17, settlement_index=0)
    agent.EPSILON = 0.0
    chose_raid = False
    for _ in range(600):
        sim.world.tick += 1
        obs = agent.observe(sim, a)
        if agent.decide(obs) == int(Action.INITIATE_RAID):
            chose_raid = True
            break
    assert chose_raid


def test_trade_blocked_between_hostile_pairs():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    force_adjacent(sim, a, b)
    sim.relations.adjust(a.id, b.id, -60)
    assert not sim.can_establish_route(a, b)
    assert sim.establish_route(a, b) is None


def test_route_deactivates_below_war_threshold():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    force_adjacent(sim, a, b)
    route = sim.establish_route(a, b)
    assert route is not None and route.active
    sim.relations.adjust(a.id, b.id, -100)  # deep into war territory
    sim._trade_tick(route)
    assert not route.active


def test_trade_improves_relations():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    force_adjacent(sim, a, b)
    a.resource_inventory["wood"] = 500.0
    b.resource_inventory["wood"] = 0.0
    score_before = sim.relations.score(a.id, b.id)
    route = sim.establish_route(a, b)
    sim._trade_tick(route)
    assert sim.relations.score(a.id, b.id) > score_before


# ----------------------------------------------------------------------
# Contested zones & events
# ----------------------------------------------------------------------

def test_contested_zones_appear_at_hostile_borders():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    assert len(sim.contested) > 0


def test_contested_zones_expire_when_cooling():
    sim, settlements = make_sim(seed=42, count=2)
    a, b = settlements
    _prepare_war(sim, a, b)
    sim.contested.clear()  # recompute from relations
    sim._refresh_contested_zones()
    had = len(sim.contested) > 0
    sim.relations.adjust(a.id, b.id, +200)  # make peace
    sim._refresh_contested_zones()
    if had:
        assert len(sim.contested) == 0


def test_event_log_serialized_in_snapshot():
    store = WorldStore(":memory:")
    try:
        sim, settlements = make_sim(seed=42, count=2)
        a, b = settlements
        force_adjacent(sim, a, b)
        route = sim.establish_route(a, b)
        wid = store.save_world(
            sim.world,
            sim.settlements,
            trade_routes=sim.trade_routes,
            relations=sim.relations,
            event_log=sim.event_log,
        )
        (
            _,
            _,
            routes,
            _,
            _,
            rels,
            _,
            _,
            events,
            diplo,
        ) = store.load_latest_snapshot(wid)
        assert len(events) >= 1
        assert any(e.type == "trade_route" for e in events)
        assert routes[0].id == route.id
    finally:
        store.close()


def test_multi_settlement_competition_run_deterministic():
    def run(seed):
        sim, settlements = make_sim(seed=seed, count=5)
        history = []
        for _ in range(150):
            sim.step()
            history.append(
                tuple((s.population, round(s.happiness, 5)) for s in settlements)
                + (len(sim.event_log), len(sim.contested))
            )
        return history

    assert run(424242) == run(424242)
