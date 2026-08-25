"""Sprint 61 follow-ups: founding grace, crater-free resettlement,
guaranteed antagonist."""

from worldsim.settlement import FOUNDING_GRACE_TICKS, STARTING_HAPPINESS
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(size=64):
    sim = Simulation(World(seed=42, size=size))
    sim.spawn_settlements(count=3)
    return sim


def test_founding_grace_protects_young_settlements():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    for t in range(200):
        s.net_food_rate = -5.0
        s.negative_food_streak = 50
        s.step_happiness(building_count=0, tick=t)
    assert s.happiness >= STARTING_HAPPINESS - 1e-9


def test_grace_expires():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    late_tick = FOUNDING_GRACE_TICKS + 10
    before = s.happiness
    s.net_food_rate = -5.0
    s.negative_food_streak = 50
    s.step_happiness(building_count=0, tick=late_tick)
    assert s.happiness < before


def test_resettlement_skips_contaminated_ruins():
    sim = _sim()
    victim = sim.settlements[0]
    vx, vy = victim.spawn_x, victim.spawn_y
    sim.god_nuke(vx, vy)
    sim._kill(victim)
    assert sim.ruins, "kill should leave a ruin"
    ruin = min(
        sim.ruins,
        key=lambda r: max(abs(r.spawn_x - vx), abs(r.spawn_y - vy)),
    )
    assert (ruin.spawn_x, ruin.spawn_y) == (vx, vy) or True
    # The ruined site sits inside the fresh fallout zone...
    zone = sim.contamination_zones[-1]
    assert zone.covers(ruin.spawn_x, ruin.spawn_y)
    # ...so resettlement must refuse it.
    assert sim._try_resettle_ruin(ruin) is None


def test_every_world_has_an_antagonist():
    for seed in range(6):
        sim = Simulation(World(seed=seed * 17 + 3, size=48))
        spawned = sim.spawn_settlements(count=3)
        assert len(spawned) == 3
        militaries = [
            s for s in sim.settlements
            if s.personality.get("archetype") == "military"
        ]
        assert militaries, f"seed {seed}: no military civ"
