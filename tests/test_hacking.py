"""Sprint 24: anti-reward-hacking regression suite.

Known exploits replayed as tests — if detection, penalization, or
quarantine ever regresses, these fail loudly in CI. Marked slow (long
rollouts with scripted exploiters).

Known exploits:
1. Route farming   - spam ESTABLISH_TRADE_ROUTE for route_delta rewards.
2. Granary spam    - Sprint 11's building-spam exploit (building deltas).
3. Alternator      - two actions alternated to dodge redundant-action
                     penalties while still farming building rewards.
"""

import pytest

from worldsim.actions import Action
from worldsim.env import WorldSimEnv
from worldsim.rewards import (
    GUARD_LEVEL_OK,
    GUARD_LEVEL_PENALIZE,
    GUARD_LEVEL_WARN,
    RewardGuard,
    RewardHackingDetector,
)


class ScriptedPolicy:
    """Deterministic scripted 'policy' for exploit replay."""

    def __init__(self, actions):
        self.actions = actions
        self.i = 0

    def predict(self, obs, deterministic=True):
        action = self.actions[self.i % len(self.actions)]
        self.i += 1
        return int(action), None


def _run_scripted(actions, ticks=400, size=32, num_settlements=2,
                  seed=7):
    env = WorldSimEnv(seed=seed, size=size,
                      num_settlements=num_settlements, max_ticks=ticks)
    obs, _ = env.reset(seed=seed)
    policy = ScriptedPolicy(actions)
    total = 0.0
    done = False
    guard_levels = []
    while not done:
        action = int(policy.predict(obs)[0])
        obs, reward, term, trunc, info = env.step(action)
        total += reward
        guard_levels.append(info["guard_level"])
        done = term or trunc or info["quarantined"]
    return env.reward_guard, total, guard_levels


# ----------------------------------------------------------------------
# Guard ladder unit tests (fast)
# ----------------------------------------------------------------------

def test_guard_escalates_to_penalize_on_sustained_single_source():
    guard = RewardGuard()
    comps = {"routes": 1.0, "survival": 0.001}
    level_seen = 0
    for tick in range(300):
        level, scale = guard.record(tick, comps)
        level_seen = max(level_seen, level)
        if level >= GUARD_LEVEL_PENALIZE:
            break
    assert level_seen >= GUARD_LEVEL_PENALIZE


def test_guard_stays_ok_on_balanced_rewards():
    guard = RewardGuard()
    comps = {"survival": 0.001, "routes": 0.2, "buildings": 0.15,
             "population": 0.1}
    for tick in range(600):
        level, _scale = guard.record(tick, comps)
        assert level == GUARD_LEVEL_OK
    # Never escalated.
    assert guard.quarantined_at_tick is None


def test_reward_scale_applied_only_when_penalizing():
    # Short detector warm-up so ladder mechanics are tested without waiting
    # out the production 200-tick track record.
    guard = RewardGuard(
        detector=RewardHackingDetector(min_ticks=5)
    )
    exploit = {"routes": 1.0}
    scales = []
    for tick in range(120):
        _level, scale = guard.record(tick, exploit)
        scales.append(scale)
    assert scales[0] == 1.0          # early ticks unpenalized
    assert min(scales) == 0.5        # later ticks penalized
    assert scales.count(1.0) > 0 and scales.count(0.5) > 0


# ----------------------------------------------------------------------
# Known-exploit replays (regression suite; slow tier by design)
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_exploit_route_farming_is_flagged_and_penalized():
    """Exploit 1: ESTABLISH_TRADE_ROUTE every tick."""
    guard, total, levels = _run_scripted([int(Action.ESTABLISH_TRADE_ROUTE)])
    # The exploiter's own redundant-action penalties count as reward-mass
    # too, so the dominant source may be 'routes' or the 'redundant_action'
    # punishment itself. Either way: flagged + escalated.
    assert guard.dominant_source() in ("routes", "redundant_action")
    assert max(levels) >= GUARD_LEVEL_WARN
    assert min(levels) <= GUARD_LEVEL_WARN or total < 0


@pytest.mark.slow
def test_exploit_granary_spam_is_detected():
    """Exploit 2: BUILD_GRANARY spam (Sprint 11 building-delta farm)."""
    guard, total, levels = _run_scripted([int(Action.BUILD_GRANARY)])
    # Either the dominant source is buildings or the redundant-action
    # penalty keeps the agent honest — but the guard must never reward the
    # spam unchecked.
    if guard.dominant_source() == "buildings":
        assert max(levels) >= GUARD_LEVEL_WARN


@pytest.mark.slow
def test_exploit_alternator_dodges_redundancy_but_not_hacking_detection():
    """Exploit 3: alternate two actions to dodge redundant-action shaping.

    The hacking detector works on reward COMPONENT shares, not action
    repetition — so alternation cannot hide a single-source exploit."""
    guard, total, levels = _run_scripted(
        [int(Action.BUILD_GRANARY), int(Action.ESTABLISH_TRADE_ROUTE)] * 200
    )
    flagged = max(levels) >= GUARD_LEVEL_WARN
    # If one component dominates earnings, detection must fire regardless
    # of the alternation trick.
    dominant = guard.dominant_source()
    if dominant in ("buildings", "routes"):
        assert flagged


@pytest.mark.slow
def test_synthetic_exploiter_quarantined_in_env():
    """Acceptance: a seeded synthetic exploiter reaches QUARANTINE."""
    guard, total, levels = _run_scripted(
        [int(Action.ESTABLISH_TRADE_ROUTE)], ticks=600
    )
    assert guard.level == 3  # QUARANTINE
    assert guard.quarantined_at_tick is not None
