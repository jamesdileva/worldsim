"""Deterministic tick engine for Sprint 2: single settlement, food, growth.

All state transitions are pure functions of (state, tick) — no external RNG
after spawn (architecture_detailed.md A1).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

from .settlement import Settlement
from .world import UNOWNED, World

# Auto-expansion rule (proves claim_territory before agents exist in Sprint 7).
CLAIM_INTERVAL_TICKS = 24
CLAIM_FOOD_SURPLUS_THRESHOLD = 0.0

# Spawn search: best-food 3x3 neighborhood within the central region.
SPAWN_SEARCH_RADIUS = 64

_NAME_SYLLABLES = [
    "ka", "tor", "ven", "mi", "ra", "sol", "un", "dar", "el", "ith",
    "bra", "nor", "ash", "quel", "mar", "ten", "ova", "ryn", "fal", "ze",
]

NAME_SEED_OFFSET = 2_000_000


def generate_name(seed: int) -> str:
    rng = random.Random((seed + NAME_SEED_OFFSET) & 0x7FFFFFFF)
    return "".join(rng.choice(_NAME_SYLLABLES) for _ in range(3)).capitalize()


@dataclass
class Simulation:
    world: World
    settlements: list[Settlement] = field(default_factory=list)

    @property
    def tick(self) -> int:
        return self.world.tick

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------

    def find_spawn_location(self) -> tuple[int, int]:
        """Deterministically pick the tile whose 3x3 neighborhood has the
        highest total food yield within the central search region.
        Returns (row, col)."""
        size = self.world.size
        center = size // 2
        r = SPAWN_SEARCH_RADIUS
        y0, y1 = max(0, center - r), min(size, center + r)
        x0, x1 = max(0, center - r), min(size, center + r)
        food = self.world.food_yield_grid()
        kernel = np.ones((3, 3), dtype=np.int32)
        # Valid-mode convolution: entry (i, j) is the sum of the 3x3 window
        # whose CENTER is at (y0 + i + 1, x0 + j + 1).
        neighborhood_sum = self._convolve3x3(food[y0:y1, x0:x1], kernel)
        flat_idx = int(np.argmax(neighborhood_sum))
        dy, dx = np.unravel_index(flat_idx, neighborhood_sum.shape)
        return int(y0 + dy + 1), int(x0 + dx + 1)

    @staticmethod
    def _convolve3x3(grid: np.ndarray, kernel: np.ndarray) -> np.ndarray:
        """Minimal valid-mode 2D convolution (3x3 kernel), deterministic."""
        kh, kw = kernel.shape
        ph, pw = grid.shape[0] - kh + 1, grid.shape[1] - kw + 1
        out = np.zeros((ph, pw), dtype=np.int64)
        for ky in range(kh):
            for kx in range(kw):
                out += kernel[ky, kx] * grid[ky : ky + ph, kx : kx + pw]
        return out

    def spawn_settlement(self) -> Settlement:
        row, col = self.find_spawn_location()
        settlement = Settlement(
            name=generate_name(self.world.seed),
            spawn_x=col,
            spawn_y=row,
            created_at_tick=self.tick,
        )
        self.settlements.append(settlement)
        idx = len(self.settlements) - 1
        self._claim_tiles(settlement, idx, initial=True)
        return settlement

    # ------------------------------------------------------------------
    # Territory
    # ------------------------------------------------------------------

    def _claim_tiles(
        self, settlement: Settlement, idx: int, initial: bool = False
    ) -> list[tuple[int, int]]:
        """Claim the 3x3 around spawn (initial) or one ring of adjacent
        unowned tiles. Returns the tiles claimed this call."""
        ownership = self.world.ownership
        size = self.world.size
        if initial:
            candidates = [
                (y, x)
                for y in range(settlement.spawn_y - 1, settlement.spawn_y + 2)
                for x in range(settlement.spawn_x - 1, settlement.spawn_x + 2)
                if 0 <= y < size and 0 <= x < size and ownership[y, x] == UNOWNED
            ]
        else:
            owned = np.argwhere(ownership == idx)
            candidates = []
            seen = set()
            for cy, cx in owned:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = int(cy) + dy, int(cx) + dx
                        if (
                            0 <= ny < size
                            and 0 <= nx < size
                            and ownership[ny, nx] == UNOWNED
                            and (ny, nx) not in seen
                        ):
                            seen.add((ny, nx))
                            candidates.append((ny, nx))
        for y, x in candidates:
            ownership[y, x] = idx
        return candidates

    def claim_territory(self, settlement: Settlement) -> list[tuple[int, int]]:
        """Expand territory by one ring; returns newly claimed tiles."""
        idx = self.settlements.index(settlement)
        return self._claim_tiles(settlement, idx, initial=False)

    def territory_of(self, settlement: Settlement) -> list[tuple[int, int]]:
        idx = self.settlements.index(settlement)
        return [tuple(t) for t in np.argwhere(self.world.ownership == idx)]

    def release_territory(self, settlement: Settlement) -> None:
        idx = self.settlements.index(settlement)
        self.world.ownership[self.world.ownership == idx] = UNOWNED

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def food_income(self, settlement: Settlement) -> float:
        idx = self.settlements.index(settlement)
        food = self.world.food_yield_grid()
        return float(food[self.world.ownership == idx].sum())

    def step(self) -> None:
        """Advance the simulation by exactly one tick."""
        self.world.tick += 1
        for idx, settlement in enumerate(self.settlements):
            if not settlement.is_alive:
                continue
            income = self.food_income(settlement)
            settlement.consume_food(income)
            was_alive = settlement.is_alive
            settlement.step_population()
            if was_alive and not settlement.is_alive:
                settlement.destroyed_at_tick = self.tick
                self.release_territory(settlement)
                continue
            if (
                settlement.is_alive
                and settlement.net_food_rate > CLAIM_FOOD_SURPLUS_THRESHOLD
                and self.tick % CLAIM_INTERVAL_TICKS == 0
            ):
                self.claim_territory(settlement)

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def status_line(self, settlement: Settlement) -> str:
        territory = len(self.territory_of(settlement)) if settlement.is_alive else 0
        state = "alive" if settlement.is_alive else "DEAD"
        return (
            f"tick {self.tick:>6} | pop {settlement.population:>4} | "
            f"food {settlement.food_stock:>9.1f} | territory {territory:>4} | "
            f"{state}"
        )
