"""RAM replay buffer for RL transitions (Sprint 13).

Fixed-capacity ring buffer storing (obs, action, reward, next_obs, done).
Capacity 10,000 per the spec; `sample()` supports future PPO/SAC-style
training without touching SQLite (which remains the durable archive via
Simulation.flush_experiences).
"""

from __future__ import annotations

import random
from collections import deque

import numpy as np


class ReplayBuffer:
    def __init__(self, capacity: int = 10_000, seed: int = 0) -> None:
        self.capacity = capacity
        self._storage: deque = deque(maxlen=capacity)
        self._rng = random.Random(seed)

    def add(
        self,
        obs: np.ndarray,
        action: int,
        reward: float,
        next_obs: np.ndarray,
        done: bool,
    ) -> None:
        self._storage.append(
            (
                np.asarray(obs, dtype=np.float32),
                int(action),
                float(reward),
                np.asarray(next_obs, dtype=np.float32),
                bool(done),
            )
        )

    def __len__(self) -> int:
        return len(self._storage)

    def __bool__(self) -> bool:
        return len(self._storage) > 0

    def sample(self, batch_size: int) -> list[tuple]:
        """Uniform random sample with replacement (returns empty list when
        the buffer is empty)."""
        if not self._storage:
            return []
        return [
            self._storage[self._rng.randrange(len(self._storage))]
            for _ in range(min(batch_size, len(self._storage)))
        ]

    def latest(self, n: int) -> list[tuple]:
        """Most recent n transitions, oldest-first."""
        items = list(self._storage)
        return items[-n:] if n < len(items) else items

    def clear(self) -> None:
        self._storage.clear()
