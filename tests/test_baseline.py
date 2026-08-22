import numpy as np
import pytest

from worldsim.actions import Action, WIRED_ACTIONS
from worldsim.agents import RuleBasedAgent
from worldsim.buildings import BuildingType, Improvement
from worldsim.db import WorldStore
from worldsim.settlement import ARCHETYPES, PERSONALITY_TRAITS, assign_personality
from worldsim.simulation import Simulation
from worldsim.tiles import TerrainType
from worldsim.world import World


def make_sim(seed: int = 42, count: int = 1) -> tuple[Simulation, list]:
    sim = Simulation(World(seed=seed))
    settlements = sim.spawn_settlements(count=count)
    return sim, settlements


# ----------------------------------------------------------------------
# Personality vectors
# ----------------------------------------------------------------------

def test_assign_personality_seeded_and_bounded():
    a = assign_personality(42, 0)
    b = assign_personality(42, 0)
    c = assign_personality(42, 1)
    assert a == b
    assert a != c
    assert set(a) == set(PERSONALITY_TRAITS) | {"archetype"}
    assert all(0.0 <= v <= 1.0 for k, v in a.items() if k != "archetype")
    assert a["archetype"] in ARCHETYPES


def test_settlements_get_personalities_at_spawn():
    sim, settlements = make_sim(seed=42, count=3)
    for s in settlements:
        assert set(s.personality) == set(PERSONALITY_TRAITS) | {"archetype"}
    # Distinct settlements have distinct vectors.
    vecs = [tuple(sorted(s.personality.items())) for s in settlements]
    assert len(set(vecs)) == len(vecs)


def test_personality_persists_through_snapshot():
    store = WorldStore(":memory:")
    try:
        sim, _ = make_sim(seed=42, count=2)
        wid = store.save_world(sim.world, sim.settlements)
        _, loaded, *_ = store.load_latest_snapshot(wid)
        for orig, rest in zip(sim.settlements, loaded):
            assert rest.personality == orig.personality
    finally:
        store.close()


def test_personalities_produce_different_strategies():
    """Same state, different personality -> different decisions."""
    sim, (s,) = make_sim(seed=42)
    agent = RuleBasedAgent(seed=5, settlement_index=0)
    agent.EPSILON = 0.0
    s.food_stock = 400.0  # comfortable: above famine, below granary trigger

    # High expansionism claims on a tighter cadence than low expansionism.
    decisions_expansive = []
    decisions_passive = []
    s.personality = {"expansionism": 1.0, "industry": 0.5,
                     "commerce": 0.5, "aggression": 0.5}
    for i in range(40):
        sim.world.tick += 1  # advance clock so cadence counters progress
        obs = agent.observe(sim, s)
        decisions_expansive.append(agent.decide(obs))
    agent2 = RuleBasedAgent(seed=5, settlement_index=0)
    agent2.EPSILON = 0.0
    s.personality = {"expansionism": 0.0, "industry": 0.5,
                     "commerce": 0.5, "aggression": 0.5}
    for i in range(40):
        sim.world.tick += 1
        obs = agent2.observe(sim, s)
        decisions_passive.append(agent2.decide(obs))

    claims_e = sum(1 for d in decisions_expansive
                   if d == int(Action.CLAIM_TERRITORY))
    claims_p = sum(1 for d in decisions_passive
                   if d == int(Action.CLAIM_TERRITORY))
    assert claims_e > claims_p


def test_industry_biases_stockpile_threshold():
    """Industrial settlements keep gathering income buildings longer."""
    sim, (s,) = make_sim(seed=42)
    s.food_stock = 400.0  # non-famine, below granary trigger
    s.resource_inventory["wood"] = 100.0  # obs wood = 0.10
    s.resource_inventory["stone"] = 100.0
    assert sim._agent_build(s, BuildingType.FARM)  # satisfy has_farm gate

    results = {}
    for label, industry in (("low", 0.0), ("high", 1.0)):
        s.personality = {"expansionism": 0.5, "industry": industry,
                         "commerce": 0.5, "aggression": 0.5}
        agent = RuleBasedAgent(seed=9, settlement_index=0)
        agent.EPSILON = 0.0
        sawmills = 0
        for i in range(60):
            sim.world.tick += 1  # advance clock so sub-cadence gates fire
            obs = agent.observe(sim, s)
            if agent.decide(obs) == int(Action.BUILD_SAWMILL):
                sawmills += 1
        results[label] = sawmills
    assert results["high"] > results["low"]


# ----------------------------------------------------------------------
# High-yield site selection
# ----------------------------------------------------------------------

def test_farm_site_selection_prefers_higher_yield():
    sim, (s,) = make_sim(seed=42)
    # Ensure at least two valid tiles with different yields exist by
    # claiming extra territory.
    for _ in range(4):
        sim.claim_territory(s)
    site = sim.find_building_site(s, BuildingType.FARM)
    assert site is not None
    food = sim.world.food_yield_grid()
    best_owned = max(
        food[y, x]
        for y, x in sim.territory_of(s)
        if sim.world.improvements[y, x] == Improvement.NONE.value
        and sim.world.terrain[y, x]
        in (TerrainType.PLAINS.value, TerrainType.FERTILE.value)
    )
    assert food[site] == best_owned


def test_sawmill_site_prefers_dense_forest():
    sim, (s,) = make_sim(seed=42)
    forest_sites = [
        (y, x) for y, x in sim.territory_of(s)
        if sim.world.terrain[y, x] == TerrainType.FOREST.value
    ]
    if not forest_sites:
        pytest.skip("no forest in territory")
    site = sim.find_building_site(s, BuildingType.SAWMILL)
    assert site is not None
    assert sim.world.terrain[site] == TerrainType.FOREST.value


# ----------------------------------------------------------------------
# Epsilon exploration
# ----------------------------------------------------------------------

def test_epsilon_rate_approximately_10_percent():
    agent = RuleBasedAgent(seed=3, settlement_index=0)
    agent._personality = {"expansionism": 0.5, "industry": 0.5,
                          "commerce": 0.5, "aggression": 0.5}
    obs = np.zeros(60, dtype=np.float32)
    random_count = 0
    n = 2000
    wired_ids = sorted(int(a) for a in WIRED_ACTIONS)
    for i in range(n):
        agent.call_count += 1
        action = agent._epsilon_action(agent.call_count * 7 + i)
        if action != -1:
            random_count += 1
            assert action in wired_ids
    rate = random_count / n
    assert 0.05 < rate < 0.15


# ----------------------------------------------------------------------
# Benchmark infrastructure
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_benchmark_cli_runs_and_logs_metrics(tmp_path):
    from worldsim.cli import main

    db = str(tmp_path / "bench.db")
    rc = main(["benchmark", "--first-seed", "50000", "--num-worlds", "2",
               "--ticks", "150", "--settlements", "2", "--db", db])
    assert rc == 0
    store = WorldStore(db)
    try:
        rows = store._conn.execute(
            "SELECT seed, agent_type, survivors, peak_population, "
            "avg_survival_ticks FROM benchmark_runs ORDER BY seed"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 50000 and rows[1][0] == 50001
        assert all(r[1] == "rulebased" for r in rows)
        assert all(r[2] >= 0 and r[3] >= 0 for r in rows)
    finally:
        store.close()


def test_benchmark_metrics_survival_short_run():
    """Sanity: on short runs most worlds should have survivors."""
    sim_results = []
    for seed in range(50000, 50003):
        sim = Simulation(World(seed=seed))
        settlements = sim.spawn_settlements(2)
        for _ in range(300):
            sim.step()
        sim_results.append(any(s.is_alive for s in settlements))
    assert sum(sim_results) >= 2


# ----------------------------------------------------------------------
# Urgency ordering
# ----------------------------------------------------------------------

def test_famine_beats_all_other_priorities():
    sim, (s,) = make_sim(seed=42)
    agent = RuleBasedAgent(seed=11, settlement_index=0)
    agent.EPSILON = 0.0
    s.food_stock = 1.0  # famine
    obs = agent.observe(sim, s)
    action = agent.decide(obs)
    # At famine the only acceptable answers are farm/mine/claim/wait —
    # never granary/trade/road.
    assert action in (
        int(Action.BUILD_FARM),
        int(Action.BUILD_MINE),
        int(Action.CLAIM_TERRITORY),
        int(Action.WAIT),
    )


def test_agent_determinism_with_personalities():
    def run(seed):
        sim, settlements = make_sim(seed=seed, count=3)
        history = []
        for _ in range(120):
            sim.step()
            history.append(
                tuple(
                    (s.population, tuple(sorted(s.personality.items())))
                    for s in settlements
                )
            )
        return history

    assert run(8080) == run(8080)
