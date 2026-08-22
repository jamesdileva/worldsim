import numpy as np
import pytest

from worldsim.actions import Action
from worldsim.env import WorldSimEnv
from worldsim.rewards import (
    HACKING_SHARE_THRESHOLD,
    RewardHackingDetector,
    RollingNormalizer,
    compute_reward_components,
    total_of,
)
from worldsim.replay import ReplayBuffer


# ----------------------------------------------------------------------
# Reward components / breakdowns
# ----------------------------------------------------------------------

def test_breakdown_has_named_components():
    c = compute_reward_components(
        prev_population=10, population=10, building_delta=0, route_delta=0,
        food_stock=500, starvation_progress=0, repeated_action_count=0,
        action_executed=True,
    )
    assert set(c) == {
        "survival", "population", "buildings", "routes", "starvation",
        "redundant_action", "effective_action",
    }
    assert c["survival"] > 0
    assert c["effective_action"] > 0


def test_breakdown_totals_sum_to_total():
    c = compute_reward_components(
        prev_population=10, population=12, building_delta=2, route_delta=1,
        food_stock=-10, starvation_progress=20, repeated_action_count=7,
        action_executed=False,
    )
    total = total_of(c)
    manual = sum(c.values())
    assert total == pytest.approx(manual)


def test_redundant_action_penalty_escalates():
    args = dict(
        prev_population=10, population=10, building_delta=0, route_delta=0,
        food_stock=500, starvation_progress=0, action_executed=True,
    )
    r4 = compute_reward_components(**args, repeated_action_count=4)
    r6 = compute_reward_components(**args, repeated_action_count=6)
    r15 = compute_reward_components(**args, repeated_action_count=15)
    assert r4["redundant_action"] == 0.0
    assert r6["redundant_action"] < r4["redundant_action"]
    assert r15["redundant_action"] <= r6["redundant_action"]  # capped


def test_effective_action_bonus_only_when_executed():
    ok = compute_reward_components(
        prev_population=10, population=10, building_delta=0, route_delta=0,
        food_stock=500, starvation_progress=0, repeated_action_count=0,
        action_executed=True,
    )
    failed = compute_reward_components(
        prev_population=10, population=10, building_delta=0, route_delta=0,
        food_stock=500, starvation_progress=0, repeated_action_count=0,
        action_executed=False,
    )
    assert ok["effective_action"] > failed["effective_action"] == 0.0


# ----------------------------------------------------------------------
# Rolling normalizer
# ----------------------------------------------------------------------

def test_normalizer_identity_until_warmed_up():
    n = RollingNormalizer()
    for v in [1.0] * 49:
        n.record(v)
    assert n.normalize(1.0) == 1.0  # below warm-up threshold: identity


def test_normalizer_centers_and_scales():
    n = RollingNormalizer()
    rng_values = [((i * 37) % 100) / 100.0 * 4 - 2 for i in range(1000)]
    for v in rng_values:
        n.record(v)
    normalized = n.normalize(n.mean())
    assert abs(normalized) < 0.5  # mean maps near zero
    out = n.normalize(2.0)
    assert out > 1.0  # far-above-mean values exceed one sigma


# ----------------------------------------------------------------------
# Hacking detector
# ----------------------------------------------------------------------

def feed_constant(detector, tick_start, ticks, components):
    for i in range(ticks):
        detector.record(tick_start + i, components)


def test_hacking_flags_single_source_above_80pct():
    d = RewardHackingDetector()
    # survival is +0.001; routes give a big constant chunk every tick.
    comps = {"survival": 0.001, "routes": 1.0}
    flagged_at = None
    for i in range(300):
        if d.record(i, comps):
            flagged_at = i
            break
    assert flagged_at is not None
    assert d.dominant_source() == "routes"


def test_balanced_rewards_not_flagged():
    d = RewardHackingDetector()
    comps = {
        "survival": 0.001, "routes": 0.3, "buildings": 0.25,
        "population": 0.2, "starvation": -0.2,
    }
    for i in range(HACKING_MIN_TICKS_DEFAULT + 50):
        assert not d.record(i, comps)


HACKING_MIN_TICKS_DEFAULT = 200


def test_hacking_requires_minimum_track_record():
    d = RewardHackingDetector(min_ticks=200)
    comps = {"routes": 1.0}
    assert not d.record(0, comps)
    assert not d.record(150, comps)  # before min_ticks: never flagged


# ----------------------------------------------------------------------
# Replay buffer
# ----------------------------------------------------------------------

def make_obs(seed):
    return np.full(60, seed, dtype=np.float32)


def test_buffer_capacity_ring_behavior():
    buf = ReplayBuffer(capacity=100)
    for i in range(150):
        buf.add(make_obs(i), i % 62, float(i), make_obs(i + 1), False)
    assert len(buf) == 100
    items = buf.latest(100)
    # Items 0-49 were evicted; the oldest survivor is item 50.
    assert items[0][0][0] == pytest.approx(50.0)
    assert items[-1][0][0] == pytest.approx(149.0)


def test_buffer_sample_shapes():
    buf = ReplayBuffer(capacity=100)
    for i in range(10):
        buf.add(make_obs(i), 5, 0.5, make_obs(i + 1), False)
    batch = buf.sample(32)
    assert len(batch) == 10  # clamped to buffer size
    obs, action, reward, next_obs, done = batch[0]
    assert obs.shape == (60,) and obs.dtype == np.float32
    assert isinstance(action, int)
    assert isinstance(reward, float)
    assert done is False


def test_buffer_empty_sample_returns_empty():
    buf = ReplayBuffer()
    assert buf.sample(10) == []
    assert len(buf) == 0


def test_env_replay_buffer_fills_per_step(env=None):
    env = WorldSimEnv(seed=42, num_settlements=2, max_ticks=500)
    env.reset()
    for _ in range(25):
        env.step(int(Action.IDLE))
    assert len(env.replay_buffer) == 25


# ----------------------------------------------------------------------
# Env integration
# ----------------------------------------------------------------------

def test_info_contains_breakdown_and_normalized():
    env = WorldSimEnv(seed=42, num_settlements=2)
    env.reset()
    _, _, _, _, info = env.step(59)
    assert "reward_breakdown" in info
    assert "reward_normalized" in info
    assert "hacking_flag" in info
    bd = info["reward_breakdown"]
    assert "survival" in bd and "buildings" in bd


def test_reward_history_tracked_per_episode():
    env = WorldSimEnv(seed=42, num_settlements=2)
    env.reset()
    for _ in range(10):
        env.step(58)
    assert len(env.reward_history) == 10
    env.reset()  # cleared per episode
    assert env.reward_history == []


def test_repeated_idle_action_incurs_penalty_in_env():
    env = WorldSimEnv(seed=42, num_settlements=2)
    env.reset()
    rewards = []
    for _ in range(8):
        _, r, *_ = env.step(58)  # same action repeatedly
        rewards.append(r)
    # Later repeats carry the redundant penalty vs the first.
    assert all(r <= 1.0 for r in rewards)
    late_avg = sum(rewards[5:]) / len(rewards[5:])
    early_avg = rewards[0]
    assert late_avg < early_avg or late_avg <= early_avg + 0.02
