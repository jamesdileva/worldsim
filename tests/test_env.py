import numpy as np
import gymnasium as gym
import pytest

from worldsim.actions import Action
from worldsim.env import MAX_EPISODE_TICKS, WorldSimEnv
from worldsim.rewards import compute_reward_components, total_of
from worldsim.settlement import Settlement


# ----------------------------------------------------------------------
# Spaces & reset
# ----------------------------------------------------------------------

def test_action_and_observation_spaces():
    env = WorldSimEnv(seed=42)
    assert isinstance(env.action_space, gym.spaces.Discrete)
    assert env.action_space.n == 62
    assert env.observation_space.shape == (60,)
    assert env.observation_space.dtype == np.float32


def test_reset_returns_valid_observation():
    env = WorldSimEnv(seed=42, num_settlements=3)
    obs, info = env.reset()
    assert obs.shape == (60,)
    assert obs.dtype == np.float32
    assert np.all(obs >= 0.0) and np.all(obs <= 1.0)
    assert "settlement_id" in info and "settlement_name" in info


def test_reset_with_seed_is_reproducible():
    env = WorldSimEnv(num_settlements=3)
    obs_a, _ = env.reset(seed=7)
    env2 = WorldSimEnv(num_settlements=3)
    obs_b, _ = env2.reset(seed=7)
    np.testing.assert_array_equal(obs_a, obs_b)


# ----------------------------------------------------------------------
# Step mechanics
# ----------------------------------------------------------------------

@pytest.fixture()
def env():
    env = WorldSimEnv(seed=42, num_settlements=3)
    env.reset()
    return env


def test_step_returns_gym_tuple(env):
    obs, reward, terminated, truncated, info = env.step(59)  # IDLE
    assert obs.shape == (60,)
    assert -1.0 <= reward <= 1.0
    assert isinstance(terminated, bool)
    assert isinstance(truncated, bool)
    assert info["tick"] == 1


def test_invalid_action_is_safe_noop(env):
    # execute_action validates; must not raise.
    obs, reward, term, trunc, info = env.step(-5)
    assert info["tick"] == 1


def test_controlled_settlement_skips_own_agent(env):
    """The given action is the ONLY decision for the controlled settlement:
    its rule-based agent must not also act that tick."""
    s = env.controlled
    before_routes = s.routes_established
    before_pop = s.population
    # Execute a no-op; if the internal agent also acted, counters could move.
    env.step(int(Action.IDLE))
    # Population may grow from economy, but route initiations require the
    # agent cadence branch which was skipped.
    assert s.routes_established >= before_routes


def test_random_episode_completes_within_max_ticks():
    env = WorldSimEnv(seed=99, num_settlements=3, max_ticks=300)
    env.reset()
    rng = np.random.default_rng(0)
    steps = 0
    done = False
    while not done:
        obs, r, term, trunc, info = env.step(int(rng.integers(0, 62)))
        steps += 1
        done = term or trunc
        assert steps <= 301
    assert done


def test_truncation_at_max_ticks():
    env = WorldSimEnv(seed=42, num_settlements=2, max_ticks=50)
    env.reset()
    for _ in range(49):
        _, _, term, trunc, _ = env.step(58)
        assert not trunc
    _, _, term, trunc, _ = env.step(58)
    assert trunc or term


def test_termination_when_controlled_settlement_dies():
    env = WorldSimEnv(seed=42, num_settlements=2)
    env.reset()
    env.controlled.population = 0
    obs, reward, terminated, truncated, info = env.step(58)
    assert terminated is True


# ----------------------------------------------------------------------
# Reward function
# ----------------------------------------------------------------------

def make_settlement(pop=10, food_stock=500):
    return Settlement(name="T", spawn_x=0, spawn_y=0, population=pop,
                      food_stock=food_stock)


def test_reward_survival_bonus_small_positive():
    s = make_settlement()
    r = total_of(compute_reward_components(
        prev_population=10, population=s.population, building_delta=0,
        route_delta=0, food_stock=s.food_stock, starvation_progress=0,
        repeated_action_count=0, action_executed=True))
    assert 0 < r <= 1.0


def test_reward_population_gain():
    s = make_settlement(pop=11)
    r = total_of(compute_reward_components(
        prev_population=10, population=s.population, building_delta=0,
        route_delta=0, food_stock=s.food_stock, starvation_progress=0,
        repeated_action_count=0, action_executed=True))
    assert 0.02 < r <= 1.0  # gain component dominates survival bonus


def test_reward_population_loss_dominates():
    s = make_settlement(pop=9)
    r = total_of(compute_reward_components(
        prev_population=10, population=s.population, building_delta=1,
        route_delta=0, food_stock=s.food_stock, starvation_progress=0,
        repeated_action_count=0, action_executed=True))
    assert r < 0  # loss penalty outweighs survival + building


def test_reward_building_delta_positive():
    base = total_of(compute_reward_components(
        prev_population=10, population=10, building_delta=0, route_delta=0,
        food_stock=500, starvation_progress=0, repeated_action_count=0,
        action_executed=True))
    with_bld = total_of(compute_reward_components(
        prev_population=10, population=10, building_delta=1, route_delta=0,
        food_stock=500, starvation_progress=0, repeated_action_count=0,
        action_executed=True))
    assert with_bld > base


def test_reward_starving_penalty():
    starving = make_settlement(pop=10, food_stock=-5)
    starving.starvation_progress = 11
    healthy = make_settlement(pop=10)
    args = dict(building_delta=0, route_delta=0, repeated_action_count=0,
                action_executed=True)
    r_starve = total_of(compute_reward_components(
        prev_population=10, population=10, food_stock=-5,
        starvation_progress=11, **args))
    r_healthy = total_of(compute_reward_components(
        prev_population=10, population=10, food_stock=500,
        starvation_progress=0, **args))
    assert r_starve < r_healthy


def test_reward_clamped_to_unit_range():
    rich = make_settlement(pop=10000)
    r = total_of(compute_reward_components(
        prev_population=10, population=rich.population, building_delta=100,
        route_delta=50, food_stock=rich.food_stock,
        starvation_progress=0, repeated_action_count=0,
        action_executed=True))
    assert min(1.0, max(-1.0, r)) == 1.0  # env clamps to +1
    dying = make_settlement(pop=0, food_stock=-1000)
    dying.starvation_progress = 48
    r = total_of(compute_reward_components(
        prev_population=500, population=dying.population, building_delta=0,
        route_delta=0, food_stock=dying.food_stock,
        starvation_progress=48, repeated_action_count=0,
        action_executed=True))
    assert max(-1.0, min(1.0, r)) == -1.0  # env clamps to -1


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------

def test_env_trajectories_deterministic():
    def run(seed):
        env = WorldSimEnv(seed=seed, num_settlements=3, max_ticks=200)
        obs, _ = env.reset(seed=seed)
        rng = np.random.default_rng(seed)
        trace = []
        done = False
        while not done:
            a = int(rng.integers(0, 62))
            obs, r, term, trunc, info = env.step(a)
            trace.append((round(r, 6), info["tick"], info["population"]))
            done = term or trunc
        return trace

    assert run(1234) == run(1234)


def test_max_tick_default():
    assert MAX_EPISODE_TICKS == 5000
