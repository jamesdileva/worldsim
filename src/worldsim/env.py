"""Gymnasium environment wrapping the simulation engine (Sprint 12, Phase 3).

The env exposes ONE settlement's perspective: `step(action)` executes the
given action for the controlled settlement while all other settlements
continue under their rule-based agents. This is the interface PPO will train
against in Sprint 14.

Reward follows docs/architecture_detailed.md §6.4, normalized to [-1, +1]
per tick.
"""

from __future__ import annotations

from dataclasses import dataclass

import gymnasium as gym
import numpy as np

from .actions import NUM_ACTIONS
from .agents import OBSERVATION_DIM, observe_vector
from .settlement import Settlement
from .simulation import Simulation
from .world import World

MAX_EPISODE_TICKS = 5000

# §6.4 reward weights, scaled so a typical tick lands well inside [-1, 1].
REWARD_POPULATION_GAIN = 0.02
REWARD_POPULATION_LOSS = 0.2
REWARD_SURVIVAL_PER_TICK = 0.001
REWARD_BUILDING_DELTA = 0.05
REWARD_ROUTE_DELTA = 0.1
PENALTY_STARVING_TICK = 0.02


def compute_reward(
    prev_population: int,
    now: Settlement,
    building_delta: int,
    route_delta: int,
) -> float:
    """§6.4-shaped reward for one tick, clamped to [-1, +1]."""
    reward = REWARD_SURVIVAL_PER_TICK
    pop_delta = now.population - prev_population
    if pop_delta > 0:
        reward += REWARD_POPULATION_GAIN * pop_delta
    elif pop_delta < 0:
        reward -= REWARD_POPULATION_LOSS * abs(pop_delta)
    reward += REWARD_BUILDING_DELTA * max(0, building_delta)
    reward += REWARD_ROUTE_DELTA * max(0, route_delta)
    if now.food_stock <= 0 and now.starvation_progress > 10:
        reward -= PENALTY_STARVING_TICK
    return float(max(-1.0, min(1.0, reward)))


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
    ) -> None:
        super().__init__()
        self.seed_value = seed
        self.size = size
        self.num_settlements = num_settlements
        self.max_ticks = max_ticks

        self.action_space = gym.spaces.Discrete(NUM_ACTIONS)
        self.observation_space = gym.spaces.Box(
            low=0.0, high=1.0, shape=(OBSERVATION_DIM,), dtype=np.float32
        )

        self.sim: Simulation | None = None
        self.controlled: Settlement | None = None
        self._prev: SettlementSnapshot | None = None

    def _reset_sim(self) -> None:
        world = World(seed=self.seed_value, size=self.size)
        sim = Simulation(world)
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
        self._prev = snapshot(
            self.controlled, sum(self.sim.buildings_of(self.controlled).values())
        )
        info = {
            "settlement_id": self.controlled.id,
            "settlement_name": self.controlled.name,
        }
        return obs, info

    def step(self, action: int):
        assert self.sim is not None and self.controlled is not None
        assert self._prev is not None

        # The controlled settlement executes the GIVEN action this tick; its
        # own rule-based agent decision is skipped for it.
        buildings_before_action = sum(
            self.sim.buildings_of(self.controlled).values()
        )
        routes_before = self.controlled.routes_established
        self.sim.execute_action(self.controlled, int(action))

        self.sim.step(skip_agent_ids={self.controlled.id})

        now_buildings = sum(self.sim.buildings_of(self.controlled).values())
        reward = compute_reward(
            self._prev.population,
            self.controlled,
            building_delta=now_buildings - buildings_before_action,
            route_delta=self.controlled.routes_established - routes_before,
        )
        self._prev = snapshot(self.controlled, now_buildings)

        terminated = not self.controlled.is_alive or not any(
            s.is_alive for s in self.sim.settlements
        )
        truncated = self.sim.tick >= self.max_ticks and not terminated
        obs = observe_vector(self.sim, self.controlled).astype(np.float32)
        info = {
            "tick": self.sim.tick,
            "population": self.controlled.population,
        }
        return obs, float(reward), bool(terminated), bool(truncated), info
