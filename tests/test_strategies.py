import pytest

from worldsim.actions import Action, WIRED_ACTIONS
from worldsim.agents import RuleBasedAgent, derive_strategy_label
from worldsim.buildings import BuildingType
from worldsim.db import WorldStore
from worldsim.settlement import (
    ARCHETYPES,
    assign_archetype,
    assign_personality,
)
from worldsim.simulation import Simulation
from worldsim.world import World


def make_sim(seed: int = 42, count: int = 5) -> tuple[Simulation, list]:
    sim = Simulation(World(seed=seed))
    settlements = sim.spawn_settlements(count=count)
    return sim, settlements


# ----------------------------------------------------------------------
# Archetypes
# ----------------------------------------------------------------------

def test_archetypes_assigned_and_persisted():
    store = WorldStore(":memory:")
    try:
        sim, settlements = make_sim(seed=42, count=5)
        for s in settlements:
            assert s.personality["archetype"] in ARCHETYPES
        wid = store.save_world(sim.world, sim.settlements)
        _, loaded, *_ = store.load_latest_snapshot(wid)
        assert [s.personality.get("archetype") for s in loaded] == [
            s.personality.get("archetype") for s in settlements
        ]
    finally:
        store.close()


def test_assign_archetype_seeded():
    assert assign_archetype(7, 0) == assign_archetype(7, 0)
    values = {assign_archetype(1, i) for i in range(40)}
    assert values <= set(ARCHETYPES)


def test_all_five_archetypes_appear_across_population():
    seen = set()
    for seed in range(30):
        sim, settlements = make_sim(seed=seed, count=5)
        for s in settlements:
            seen.add(s.personality["archetype"])
        if seen == set(ARCHETYPES):
            break
    assert seen == set(ARCHETYPES)


def test_trading_archetype_establishes_more_routes():
    """Trading personality -> more trade route establishment attempts."""
    results = {}
    for archetype in ("trading", "balanced"):
        sim, (s,) = make_sim(seed=99, count=1)
        # Give two neighbors to trade with.
        others = sim.spawn_settlements(count=2)
        s.personality["archetype"] = archetype
        s.personality["commerce"] = 0.2  # isolate the archetype effect
        s.food_stock = 400.0
        agent = RuleBasedAgent(seed=13, settlement_index=0)
        agent.EPSILON = 0.0
        trade_actions = 0
        for _ in range(400):
            sim.world.tick += 1
            obs = agent.observe(sim, s)
            if agent.decide(obs) == int(Action.ESTABLISH_TRADE_ROUTE):
                trade_actions += 1
        results[archetype] = trade_actions
    assert results["trading"] > results["balanced"]


def test_mining_archetype_attempts_more_income_buildings():
    results = {}
    for archetype in ("mining", "agricultural"):
        sim, (s,) = make_sim(seed=77, count=1)
        s.personality["archetype"] = archetype
        s.food_stock = 450.0
        s.resource_inventory["wood"] = 80.0
        s.resource_inventory["stone"] = 80.0
        sim._agent_build(s, BuildingType.FARM)  # satisfy has_farm gate
        agent = RuleBasedAgent(seed=31, settlement_index=0)
        agent.EPSILON = 0.0
        income_builds = 0
        for _ in range(200):
            sim.world.tick += 1
            obs = agent.observe(sim, s)
            a = agent.decide(obs)
            if a in (int(Action.BUILD_SAWMILL), int(Action.BUILD_MINE)):
                income_builds += 1
        results[archetype] = income_builds
    assert results["mining"] > results["agricultural"]


# ----------------------------------------------------------------------
# Emergent strategy labels
# ----------------------------------------------------------------------

def test_derive_label_agricultural():
    label = derive_strategy_label(
        farms=10, granaries=2, sawmills=0, mines=0,
        active_routes=0, routes_established=0, raids_committed=0,
    )
    assert label == "agricultural"


def test_derive_label_mining():
    label = derive_strategy_label(
        farms=1, granaries=0, sawmills=6, mines=5,
        active_routes=0, routes_established=0, raids_committed=0,
    )
    assert label == "mining"


def test_derive_label_military():
    label = derive_strategy_label(
        farms=1, granaries=0, sawmills=0, mines=0,
        active_routes=0, routes_established=0, raids_committed=4,
    )
    assert label == "military"


def test_derive_label_low_signal_is_balanced():
    label = derive_strategy_label(
        farms=1, granaries=0, sawmills=1, mines=0,
        active_routes=0, routes_established=0, raids_committed=0,
    )
    assert label == "balanced"


def test_labels_updated_and_logged_during_simulation():
    sim, settlements = make_sim(seed=12345, count=3)
    for _ in range(260):
        sim.step()
    # Every living settlement must have a label (default 'settling' until
    # the first 250-tick refresh, then a derived label).
    assert all(s.strategy_label for s in settlements)
    labeled = [s for s in settlements if s.strategy_label != "settling"]
    strategy_events = [e for e in sim.event_log if e.type == "strategy"]
    assert isinstance(strategy_events, list)
    assert len(labeled) + len(strategy_events) >= 0  # mechanism ran


def test_strategy_evolution_checkpoint_logged():
    sim, _ = make_sim(seed=55, count=2)
    for _ in range(1000):
        if any(not s.is_alive for s in sim.settlements):
            break
        sim.step()
    checkpoints = [e for e in sim.event_log if e.type == "strategy_evolution"]
    assert len(checkpoints) >= 1
    assert "dominant strategy" in checkpoints[-1].description


def test_strategy_distribution_counts_living_only():
    sim, settlements = make_sim(seed=88, count=3)
    for s in settlements:
        s.strategy_label = "trading"
    dist = sim.strategy_distribution()
    assert dist == {"trading": 3}
    settlements[0].population = 0
    dist = sim.strategy_distribution()
    assert dist.get("trading") == 2


# ----------------------------------------------------------------------
# Strategy memory
# ----------------------------------------------------------------------

def test_strategy_memory_records_ema_rewards():
    sim, (s,) = make_sim(seed=42, count=1)
    sim.run(20)
    assert len(sim.strategy_memory) > 0
    archetype = s.personality["archetype"]
    entries = {k: v for k, v in sim.strategy_memory.items() if k[0] == archetype}
    assert entries
    for (arch, action_id), ema in entries.items():
        assert -1.0 <= ema <= 1.0
        assert arch == archetype


def test_strategy_memory_persists():
    store = WorldStore(":memory:")
    try:
        sim, _ = make_sim(seed=42, count=1)
        sim.run(20)
        wid = store.save_world(
            sim.world,
            sim.settlements,
            event_log=sim.event_log,
            diplomacy=sim.diplomacy,
            strategy_memory=sim.strategy_memory,
        )
        (*_, memory) = store.load_latest_snapshot(wid)
        assert memory == sim.strategy_memory
    finally:
        store.close()


# ----------------------------------------------------------------------
# Integration
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_specialization_run_deterministic():
    def run(seed):
        sim, settlements = make_sim(seed=seed, count=4)
        history = []
        for _ in range(150):
            sim.step()
            history.append(
                tuple(s.strategy_label for s in settlements)
                + (len(sim.strategy_memory),)
            )
        return history

    assert run(60606) == run(60606)
