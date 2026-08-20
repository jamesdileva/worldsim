"""Deterministic tick engine: settlements, food, buildings, roads (Sprint 3).

All state transitions are pure functions of (state, tick) — no external RNG
after spawn (architecture_detailed.md A1).
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .buildings import (
    BASE_FOOD_CAPACITY,
    BUILDING_SPECS,
    BuildingType,
    IMPROVEMENT_TO_BUILDING,
    Improvement,
    ROAD_COST_STONE,
)
from .settlement import Settlement
from .tiles import TERRAIN_PROFILES, TerrainType
from .world import UNOWNED, World

# Auto-expansion rule (proves claim_territory before agents exist in Sprint 7).
CLAIM_INTERVAL_TICKS = 24
CLAIM_FOOD_SURPLUS_THRESHOLD = 0.0

# Auto-build / auto-road rules (placeholder until agents arrive in Sprint 7).
BUILD_INTERVAL_TICKS = 8
ROAD_INTERVAL_TICKS = 12
# Build priority evaluated top-down; first type with a valid affordable site
# is queued.
BUILD_PRIORITY = [
    BuildingType.FARM,
    BuildingType.SAWMILL,
    BuildingType.MINE,
    BuildingType.GRANARY,
]

# Workers passively gather a fraction of terrain wood/stone/metal yields on
# owned tiles each tick — prevents an unrecoverable wood deadlock before
# sawmills/mines exist.
GATHER_RATE = 0.25

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
        mask = self.world.ownership == idx
        # Buildings/roads on lost tiles are destroyed (Sprint 3 acceptance).
        self.world.improvements[mask] = Improvement.NONE.value
        self.world.ownership[mask] = UNOWNED

    # ------------------------------------------------------------------
    # Buildings & roads
    # ------------------------------------------------------------------

    def _owned_mask(self, settlement: Settlement) -> np.ndarray:
        idx = self.settlements.index(settlement)
        return self.world.ownership == idx

    def can_afford(self, settlement: Settlement, wood: int, stone: int) -> bool:
        inv = settlement.resource_inventory
        return (
            inv.get("wood", 0.0) >= wood and inv.get("stone", 0.0) >= stone
        )

    def _pay(self, settlement: Settlement, wood: int, stone: int) -> None:
        inv = settlement.resource_inventory
        inv["wood"] = inv.get("wood", 0.0) - wood
        inv["stone"] = inv.get("stone", 0.0) - stone

    def find_building_site(
        self, settlement: Settlement, building_type: BuildingType
    ) -> tuple[int, int] | None:
        """Deterministically pick the first owned, unimproved tile with valid
        terrain for the building type (row-major scan)."""
        spec = BUILDING_SPECS[building_type]
        valid_terrain = np.isin(
            self.world.terrain, [t.value for t in spec.valid_terrain]
        )
        candidates = np.logical_and(
            self._owned_mask(settlement),
            np.logical_and(
                valid_terrain,
                self.world.improvements == Improvement.NONE.value,
            ),
        )
        sites = np.argwhere(candidates)
        if len(sites) == 0:
            return None
        y, x = int(sites[0][0]), int(sites[0][1])
        return y, x

    def build_at(
        self,
        settlement: Settlement,
        building_type: BuildingType,
        x: int,
        y: int,
    ) -> bool:
        """Construct a building on an owned, unimproved, valid-terrain tile.
        Returns True if construction succeeded."""
        spec = BUILDING_SPECS[building_type]
        size = self.world.size
        if not (0 <= y < size and 0 <= x < size):
            return False
        if self.world.ownership[y, x] != self.settlements.index(settlement):
            return False
        if self.world.improvements[y, x] != Improvement.NONE.value:
            return False
        if TerrainType(self.world.terrain[y, x]) not in spec.valid_terrain:
            return False
        if not self.can_afford(settlement, spec.cost_wood, spec.cost_stone):
            return False
        self._pay(settlement, spec.cost_wood, spec.cost_stone)
        improvement = Improvement(building_type.value + 1)
        self.world.improvements[y, x] = improvement.value
        return True

    def destroy_building(self, x: int, y: int) -> bool:
        """Remove any improvement (building or road) from a tile."""
        if self.world.improvements[y, x] == Improvement.NONE.value:
            return False
        self.world.improvements[y, x] = Improvement.NONE.value
        return True

    def buildings_of(self, settlement: Settlement) -> dict[BuildingType, int]:
        """Counts per building type for a settlement."""
        owned = self._owned_mask(settlement)
        counts: dict[BuildingType, int] = {}
        for btype in BuildingType:
            imp = Improvement(btype.value + 1)
            counts[btype] = int(
                np.logical_and(
                    owned, self.world.improvements == imp.value
                ).sum()
            )
        return counts

    def enqueue_build(self, settlement: Settlement, btype: BuildingType) -> None:
        settlement.build_queue.append(btype.name)

    def _process_build_queue(self, settlement: Settlement) -> None:
        if not settlement.build_queue:
            return
        head = settlement.build_queue[0]
        btype = BuildingType[head]
        site = self.find_building_site(settlement, btype)
        if site is None:
            # No valid tile — drop the order rather than blocking the queue.
            settlement.build_queue.pop(0)
            return
        if self.build_at(settlement, btype, x=site[1], y=site[0]):
            settlement.build_queue.pop(0)

    def _auto_build_rule(self, settlement: Settlement) -> None:
        """Queue the least-built building type that has an affordable valid
        site — keeps the building mix balanced until agents take over."""
        if settlement.build_queue:
            return
        counts = self.buildings_of(settlement)
        best: BuildingType | None = None
        best_key: tuple[int, int] | None = None
        for priority, btype in enumerate(BUILD_PRIORITY):
            site = self.find_building_site(settlement, btype)
            if site is None:
                continue
            spec = BUILDING_SPECS[btype]
            if not self.can_afford(settlement, spec.cost_wood, spec.cost_stone):
                continue
            key = (counts[btype], priority)
            if best_key is None or key < best_key:
                best_key = key
                best = btype
        if best is not None:
            self.enqueue_build(settlement, best)

    # -- Roads ----------------------------------------------------------

    def build_road(self, settlement: Settlement, x: int, y: int) -> bool:
        """Build a road segment on an owned, unimproved land tile."""
        size = self.world.size
        if not (0 <= y < size and 0 <= x < size):
            return False
        if self.world.ownership[y, x] != self.settlements.index(settlement):
            return False
        if self.world.improvements[y, x] != Improvement.NONE.value:
            return False
        if TerrainType(self.world.terrain[y, x]) == TerrainType.WATER:
            return False
        if not self.can_afford(settlement, 0, ROAD_COST_STONE):
            return False
        self._pay(settlement, 0, ROAD_COST_STONE)
        self.world.improvements[y, x] = Improvement.ROAD.value
        return True

    def roads_of(self, settlement: Settlement) -> set[tuple[int, int]]:
        owned = self._owned_mask(settlement)
        road_tiles = np.argwhere(
            np.logical_and(
                owned, self.world.improvements == Improvement.ROAD.value
            )
        )
        return {(int(y), int(x)) for y, x in road_tiles}

    def road_connectivity(
        self, settlement: Settlement
    ) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
        """BFS from the settlement center (which acts as a network hub: roads
        adjacent to it are connected) across 4-connected road tiles.
        Returns (connected, disconnected) road tile sets."""
        roads = self.roads_of(settlement)
        center = (settlement.spawn_y, settlement.spawn_x)
        connected: set[tuple[int, int]] = set()
        queue: deque[tuple[int, int]] = deque()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            adj = (center[0] + dy, center[1] + dx)
            if adj in roads:
                queue.append(adj)
        while queue:
            y, x = queue.popleft()
            if (y, x) in connected:
                continue
            connected.add((y, x))
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nxt = (y + dy, x + dx)
                if nxt in roads and nxt not in connected:
                    queue.append(nxt)
        return connected, roads - connected

    def _auto_road_rule(self, settlement: Settlement) -> None:
        """Extend the road network by one tile toward unimproved territory."""
        roads = self.roads_of(settlement)
        anchor = (
            roads
            if roads
            else {(settlement.spawn_y, settlement.spawn_x)}
        )
        owned = self._owned_mask(settlement)
        size = self.world.size
        for ay, ax in sorted(anchor):
            for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                ny, nx = ay + dy, ax + dx
                if (
                    0 <= ny < size
                    and 0 <= nx < size
                    and owned[ny, nx]
                    and self.world.improvements[ny, nx]
                    == Improvement.NONE.value
                    and TerrainType(self.world.terrain[ny, nx])
                    != TerrainType.WATER
                ):
                    if self.build_road(settlement, nx, ny):
                        return

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def food_income(self, settlement: Settlement) -> float:
        """Terrain food yields + farm output on owned tiles."""
        idx = self.settlements.index(settlement)
        owned = self.world.ownership == idx
        income = float(self.world.food_yield_grid()[owned].sum())
        farms = np.logical_and(
            owned, self.world.improvements == Improvement.FARM.value
        )
        income += int(farms.sum()) * BUILDING_SPECS[BuildingType.FARM].food_output
        return income

    def food_capacity(self, settlement: Settlement) -> float:
        granaries = int(
            np.logical_and(
                self._owned_mask(settlement),
                self.world.improvements == Improvement.GRANARY.value,
            ).sum()
        )
        return BASE_FOOD_CAPACITY + (
            granaries * BUILDING_SPECS[BuildingType.GRANARY].food_capacity
        )

    def _produce_resources(self, settlement: Settlement) -> None:
        """Add building output + passive gathering to the inventory."""
        idx = self.settlements.index(settlement)
        owned = self.world.ownership == idx
        produced = {"wood": 0.0, "stone": 0.0, "metal": 0.0}
        for btype in BuildingType:
            imp = Improvement(btype.value + 1)
            count = int(
                np.logical_and(owned, self.world.improvements == imp.value).sum()
            )
            if count == 0:
                continue
            spec = BUILDING_SPECS[btype]
            produced["wood"] += spec.wood_output * count
            produced["stone"] += spec.stone_output * count
            produced["metal"] += spec.metal_output * count
        # Passive gathering: fraction of terrain yields on owned tiles.
        terrain_grid = self.world.terrain
        for res_name, attr in (("wood", "wood"), ("stone", "stone"), ("metal", "metal")):
            trickle = 0.0
            for tt in TerrainType:
                per_tile = getattr(TERRAIN_PROFILES[tt], attr)
                if per_tile <= 0:
                    continue
                tiles = int(
                    np.logical_and(
                        owned, terrain_grid == tt.value
                    ).sum()
                )
                trickle += tiles * per_tile * GATHER_RATE
            produced[res_name] += trickle
        inv = settlement.resource_inventory
        for res, amount in produced.items():
            inv[res] = inv.get(res, 0.0) + amount

    def step(self) -> None:
        """Advance the simulation by exactly one tick."""
        self.world.tick += 1
        for idx, settlement in enumerate(self.settlements):
            if not settlement.is_alive:
                continue
            self._process_build_queue(settlement)
            if self.tick % BUILD_INTERVAL_TICKS == 0:
                self._auto_build_rule(settlement)
            if self.tick % ROAD_INTERVAL_TICKS == 0:
                self._auto_road_rule(settlement)
            self._produce_resources(settlement)
            settlement.consume_food(
                self.food_income(settlement),
                capacity=self.food_capacity(settlement),
            )
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
        if settlement.is_alive:
            territory = len(self.territory_of(settlement))
            buildings = sum(self.buildings_of(settlement).values())
            roads = len(self.roads_of(settlement))
            state = "alive"
        else:
            territory = 0
            buildings = 0
            roads = 0
            state = "DEAD"
        return (
            f"tick {self.tick:>6} | pop {settlement.population:>4} | "
            f"food {settlement.food_stock:>9.1f} | territory {territory:>4} | "
            f"bld {buildings:>3} | road {roads:>3} | {state}"
        )
