import numpy as np

from worldsim.settlement import (
    FOOD_PER_WORKER_PER_TICK,
    GROWTH_INTERVAL_TICKS,
    STARVATION_INTERVAL_TICKS,
    STARTING_FOOD,
    STARTING_POPULATION,
    Settlement,
)
from worldsim.simulation import (
    CLAIM_INTERVAL_TICKS,
    Simulation,
    generate_name,
)
from worldsim.world import World


def make_sim(seed: int = 42) -> tuple[Simulation, Settlement]:
    sim = Simulation(World(seed=seed))
    settlement = sim.spawn_settlement()
    return sim, settlement


def test_settlement_defaults():
    s = Settlement(name="Testa", spawn_x=10, spawn_y=10)
    assert s.population == STARTING_POPULATION
    assert s.food_stock == STARTING_FOOD
    assert s.is_alive


def test_growth_one_per_24_ticks():
    s = Settlement(name="Testa", spawn_x=0, spawn_y=0, population=10, food_stock=10_000)
    for _ in range(GROWTH_INTERVAL_TICKS):
        s.consume_food(income=100.0)  # large surplus
        s.step_population()
    assert s.population == 11
    for _ in range(GROWTH_INTERVAL_TICKS):
        s.consume_food(income=100.0)
        s.step_population()
    assert s.population == 12


def test_growth_pauses_when_food_runs_out():
    # Spec: growth requires positive food stock. Start with a small stock and
    # no income — it depletes, then the growth counter must reset.
    s = Settlement(name="Testa", spawn_x=0, spawn_y=0, population=1, food_stock=15)
    for _ in range(10):
        s.consume_food(income=0.0)
        s.step_population()
    assert s.food_stock > 0
    assert s.growth_progress == 10
    for _ in range(20):
        s.consume_food(income=0.0)
        s.step_population()
    assert s.food_stock <= 0
    assert s.growth_progress == 0
    assert s.population < 1 + 10  # starving, never grew


def test_starvation_minus_one_per_48_ticks():
    s = Settlement(name="Testa", spawn_x=0, spawn_y=0, population=10, food_stock=0)
    for _ in range(STARVATION_INTERVAL_TICKS):
        s.consume_food(income=0.0)
        s.step_population()
    assert s.population == 9


def test_starvation_does_not_stack_with_growth():
    s = Settlement(name="Testa", spawn_x=0, spawn_y=0, population=10, food_stock=1)
    # Tick 1: food positive (stock 1 > 0 after consumption? consumption makes it negative)
    s.consume_food(income=0.0)  # stock goes negative -> starving path
    s.step_population()
    assert s.growth_progress == 0


def test_settlement_dies_at_zero_population():
    s = Settlement(name="Testa", spawn_x=0, spawn_y=0, population=2, food_stock=0)
    ticks_needed = STARVATION_INTERVAL_TICKS * 2
    for _ in range(ticks_needed):
        if not s.is_alive:
            break
        s.consume_food(income=0.0)
        s.step_population()
    assert s.population == 0
    assert not s.is_alive


def test_spawn_location_is_food_rich_and_deterministic():
    sim_a, a = make_sim(seed=12345)
    sim_b, b = make_sim(seed=12345)
    assert (a.spawn_x, a.spawn_y) == (b.spawn_x, b.spawn_y)
    food = sim_a.world.food_yield_grid()
    neighborhood = sum(
        food[y, x]
        for y in range(a.spawn_y - 1, a.spawn_y + 2)
        for x in range(a.spawn_x - 1, a.spawn_x + 2)
    )
    # Spawn must be viable: neighborhood yield covers initial consumption
    # (10 workers * 1 food) with headroom for growth.
    assert neighborhood > 10


def test_initial_territory_3x3():
    sim, s = make_sim(seed=7)
    territory = sim.territory_of(s)
    assert len(territory) == 9
    ys = {y for y, _ in territory}
    xs = {x for _, x in territory}
    assert ys == {s.spawn_y - 1, s.spawn_y, s.spawn_y + 1}
    assert xs == {s.spawn_x - 1, s.spawn_x, s.spawn_x + 1}


def test_claim_territory_expands_one_ring():
    sim, s = make_sim(seed=7)
    claimed = sim.claim_territory(s)
    territory = sim.territory_of(s)
    assert len(territory) == 25  # 3x3 -> full 5x5 ring
    assert len(claimed) == 16
    # All claimed tiles are adjacent to previous territory.
    old = set(sim.territory_of(s)) - set(claimed)
    for y, x in claimed:
        neighbors = [
            (y + dy, x + dx) in old
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
            if (dy, dx) != (0, 0)
        ]
        assert any(neighbors)


def test_claim_respects_boundaries():
    sim = Simulation(World(seed=3, size=8))
    s = sim.spawn_settlement()
    # Force spawn near corner by claiming repeatedly; must never go out of bounds.
    for _ in range(20):
        sim.claim_territory(s)
    territory = sim.territory_of(s)
    size = sim.world.size
    assert all(0 <= y < size and 0 <= x < size for y, x in territory)


def test_food_income_from_owned_tiles():
    sim, s = make_sim(seed=99)
    income = sim.food_income(s)
    food = sim.world.food_yield_grid()
    expected = sum(food[y, x] for y, x in sim.territory_of(s))
    assert income == expected


def test_auto_claim_on_surplus():
    sim, s = make_sim(seed=11)
    start_size = len(sim.territory_of(s))
    # Run enough ticks with surplus to trigger the periodic auto-claim.
    for _ in range(CLAIM_INTERVAL_TICKS * 2):
        sim.step()
        if not s.is_alive:
            break
    if s.is_alive and s.net_food_rate > 0:
        assert len(sim.territory_of(s)) > start_size


def test_death_releases_territory():
    sim = Simulation(World(seed=5))
    s = sim.spawn_settlement()
    # Cut off all food income so starvation actually proceeds.
    sim.release_territory(s)
    s.population = 1
    s.food_stock = 0
    for _ in range(STARVATION_INTERVAL_TICKS):
        sim.step()
    assert not s.is_alive
    assert s.destroyed_at_tick is not None
    assert (sim.world.ownership == -1).all()


def test_simulation_deterministic():
    def run(seed):
        sim, s = make_sim(seed=seed)
        history = []
        for _ in range(200):
            sim.step()
            history.append((s.population, round(s.food_stock, 6), len(sim.territory_of(s))))
        return history

    assert run(424242) == run(424242)


def test_name_generator_seeded():
    assert generate_name(1) == generate_name(1)
    assert generate_name(1) != generate_name(2)
    assert generate_name(1).isalpha()


def test_full_lifecycle_growth_then_stability():
    sim, s = make_sim(seed=2024)
    for _ in range(240):  # 10 growth intervals' worth of ticks
        sim.step()
        if not s.is_alive:
            break
    assert s.is_alive
    assert s.population > STARTING_POPULATION
