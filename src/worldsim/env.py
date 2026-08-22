"""Gymnasium environment wrapping the simulation engine (Sprint 12-13).

The env exposes ONE settlement's perspective: `step(action)` executes the
given action for the controlled settlement while all other settlements
continue under their rule-based agents. This is the interface PPO will train
against in Sprint 14.

Reward follows docs/architecture_detailed.md §6.4 as named components
(Sprint 13), normalized to [-1, +1] per tick, with breakdown/normalized
values and reward-hacking flags exposed through `info`.
"""

from __future__ import annotations

from dataclasses import dataclass

import gymnasium as gym
import numpy as np

from .actions import NUM_ACTIONS
from .agents import OBSERVATION_DIM, observe_vector
from .rewards import (
    RewardHackingDetector,
    RewardWeights,
    RollingNormalizer,
    compute_reward_components,
    total_of,
)
from .replay import ReplayBuffer
from .settlement import Settlement
from .simulation import Simulation
from .world import World

MAX_EPISODE_TICKS = 5000
REDUNDANT_ACTION_WINDOW = 5


@dataclass
class SettlementSnapshot:
    population: int
    building_count: int
    routes_established: int


def snapshot(settlement: Settlement, building_count: int) -> SettlementSnapshot:
    return SettlementSnapshot(
        population=settlement.population,
        building_count=building_count,
        routes_established=settlement.routes_established,
    )


class WorldSimEnv(gym.Env):
    """Single-settlement RL environment over the world simulation."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        seed: int = 42,
        size: int = 256,
        num_settlements: int = 5,
        max_ticks: int = MAX_EPISODE_TICKS,
        replay_capacity: int = 10_000,
        disaster_chance_mult: float = 1.0,
        gather_mult: float = 1.0,
        reward_weights: dict | None = None,
    ) -> None:
        super().__init__()
        self.seed_value = seed
        self.size = size
        self.num_settlements = num_settlements
        self.max_ticks = max_ticks
        self.disaster_chance_mult = disaster_chance_mult
        self.gather_mult = gather_mult
        # Sprint 18b: configurable §6.4 weights (shaping rebalance without
        # code edits).
        self.reward_weights = RewardWeights(**(reward_weights or {}))

        self.action_space = gym.spaces.Discrete(NUM_ACTIONS)
        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(OBSERVATION_DIM,), dtype=np.float32
        )

        # Sprint 13: reward refinement machinery.
        self.replay_buffer = ReplayBuffer(capacity=replay_capacity, seed=seed)
        self.normalizer = RollingNormalizer()
        self.hacking_detector = RewardHackingDetector()
        self.reward_history: list[float] = []
        self._last_action: int | None = None
        self._repeat_count = 0
        self._obs: np.ndarray | None = None

        self.sim: Simulation | None = None
        self.controlled: Settlement | None = None
        self._prev: SettlementSnapshot | None = None
        self._prev_population: int = 0

    def _reset_sim(self) -> None:
        world = World(seed=self.seed_value, size=self.size)
        sim = Simulation(
            world,
            disaster_chance_mult=self.disaster_chance_mult,
            gather_mult=self.gather_mult,
        )
        sim.spawn_settlements(count=self.num_settlements)
        self.sim = sim
        self.controlled = sim.settlements[0]

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self.seed_value = seed
        self._reset_sim()
        assert self.sim is not None and self.controlled is not None
        obs = observe_vector(self.sim, self.controlled).astype(np.float32)
        self._obs = obs
        self._prev = snapshot(
            self.controlled, sum(self.sim.buildings_of(self.controlled).values())
        )
        self._prev_population = self.controlled.population
        self.normalizer = RollingNormalizer()
        self.hacking_detector = RewardHackingDetector()
        self.reward_history.clear()
        self._last_action = None
        self._repeat_count = 0
        info = {
            "settlement_id": self.controlled.id,
            "settlement_name": self.controlled.name,
        }
        return obs, info

    def step(self, action: int):
        assert self.sim is not None and self.controlled is not None
        assert self._prev is not None
        action = int(action)

        # Redundant-action shaping (Sprint 13).
        if action == self._last_action:
            self._repeat_count += 1
        else:
            self._repeat_count = 0
        self._last_action = action

        buildings_before_action = sum(
            self.sim.buildings_of(self.controlled).values()
        )
        routes_before = self.controlled.routes_established
        executed = self.sim.execute_action(self.controlled, action)

        prev_population = self._prev_population
        self.sim.step(skip_agent_ids={self.controlled.id})

        now_buildings = sum(self.sim.buildings_of(self.controlled).values())
        components = compute_reward_components(
            prev_population=prev_population,
            population=self.controlled.population,
            building_delta=now_buildings - buildings_before_action,
            route_delta=self.controlled.routes_established - routes_before,
            food_stock=self.controlled.food_stock,
            starvation_progress=self.controlled.starvation_progress,
            repeated_action_count=self._repeat_count,
            action_executed=executed,
            weights=self.reward_weights,
        )
        reward = float(max(-1.0, min(1.0, total_of(components))))
        self.normalizer.record(reward)
        self.reward_history.append(reward)
        flagged = self.hacking_detector.record(self.sim.tick, components)
        self._prev = snapshot(self.controlled, now_buildings)
        self._prev_population = self.controlled.population

        terminated = not self.controlled.is_alive or not any(
            s.is_alive for s in self.sim.settlements
        )
        truncated = self.sim.tick >= self.max_ticks and not terminated
        next_obs = observe_vector(self.sim, self.controlled).astype(np.float32)

        # Sprint 13: replay buffer captures every transition.
        prev_obs = self._obs if self._obs is not None else next_obs
        self.replay_buffer.add(prev_obs, action, reward, next_obs, terminated)
        self._obs = next_obs

        info = {
            "tick": self.sim.tick,
            "population": self.controlled.population,
            "reward_breakdown": dict(components),
            "reward_normalized": self.normalizer.normalize(reward),
            "hacking_flag": flagged,
            "hacking_source": self.hacking_detector.dominant_source(),
        }
        return next_obs, reward, bool(terminated), bool(truncated), info
