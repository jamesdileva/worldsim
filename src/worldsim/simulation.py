"""Deterministic tick engine: settlements, food, buildings, roads (Sprint 3).

All state transitions are pure functions of (state, tick) — no external RNG
after spawn (architecture_detailed.md A1).
"""

from __future__ import annotations

import random
import uuid
import zlib
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
from .disasters import (
    BASE_EVENT_CHANCE,
    DisasterEvent,
    DisasterType,
    DROUGHT_FARM_MULTIPLIER,
    EVENT_CHECK_INTERVAL_TICKS,
    PLAGUE_MORTALITY,
    roll_event,
)
from .settlement import (
    LOW_HAPPINESS_COLLAPSE_TICKS,
    Settlement,
)
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

# Multi-spawn (minimal Sprint 9 pull-forward) and trade (Sprint 4).
DEFAULT_SETTLEMENT_COUNT = 3
SPAWN_MIN_DISTANCE = 32
TRADE_INTERVAL_TICKS = 24
TRADE_AMOUNT_PER_TICK = 1.0
# Resources eligible for trade, in deterministic evaluation order.
TRADE_RESOURCES = ("food", "wood", "stone", "metal")
# Economic collapse: any inventory < 0 sustained this long costs population.
COLLAPSE_INTERVAL_TICKS = 48

# Ruins & spontaneous re-settlement (Sprint 5).
RUIN_SEED_OFFSET = 4_000_000
RUIN_RESETTLE_MIN_AGE = 500
RUIN_RESETTLE_CHANCE = 0.10
RUIN_GROWTH_MULTIPLIER = 2

# Spawn search: best-food 3x3 neighborhood within the central region.
SPAWN_SEARCH_RADIUS = 64

_NAME_SYLLABLES = [
    "ka", "tor", "ven", "mi", "ra", "sol", "un", "dar", "el", "ith",
    "bra", "nor", "ash", "quel", "mar", "ten", "ova", "ryn", "fal", "ze",
]

NAME_SEED_OFFSET = 2_000_000


def generate_name(seed: int, index: int = 0) -> str:
    rng = random.Random((seed + NAME_SEED_OFFSET + index * 7919) & 0x7FFFFFFF)
    return "".join(rng.choice(_NAME_SYLLABLES) for _ in range(3)).capitalize()


@dataclass
class RuinSite:
    """A collapsed settlement's remains (Sprint 5).

    Remembered so the site can spontaneously re-settle after 500 ticks and
    grant 2x growth to settlements founded adjacent to the former capital."""

    settlement_id: str
    name: str
    spawn_x: int
    spawn_y: int
    collapse_tick: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TradeRoute:
    """A trade link between two settlements (Sprint 4).

    Direction-agnostic: each tick the donor is whichever side holds more of
    the resource the other side needs most. Transfers 1 unit/tick."""

    source_id: str
    dest_id: str
    established_tick: int
    transfers: int = 0
    active: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def partner_of(self, settlement_id: str) -> str:
        return self.dest_id if settlement_id == self.source_id else self.source_id


@dataclass
class Simulation:
    world: World
    settlements: list[Settlement] = field(default_factory=list)
    trade_routes: list[TradeRoute] = field(default_factory=list)
    disaster_events: list[DisasterEvent] = field(default_factory=list)
    ruins: list[RuinSite] = field(default_factory=list)

    @property
    def tick(self) -> int:
        return self.world.tick

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------

    def find_spawn_location(
        self,
        exclude: list[tuple[int, int]] | None = None,
        min_distance: int = 0,
    ) -> tuple[int, int] | None:
        """Deterministically pick the tile whose 3x3 neighborhood has the
        highest total food yield within the central search region, at least
        min_distance (Chebyshev) from every excluded spawn. Returns
        (row, col), or None if no candidate exists."""
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
        order = np.argsort(neighborhood_sum, axis=None)[::-1]
        exclude = exclude or []
        for flat_idx in order:
            dy, dx = np.unravel_index(int(flat_idx), neighborhood_sum.shape)
            row, col = int(y0 + dy + 1), int(x0 + dx + 1)
            if all(
                max(abs(row - ey), abs(col - ex)) >= min_distance
                for ey, ex in exclude
            ):
                return row, col
        return None

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
            name=generate_name(self.world.seed, len(self.settlements)),
            spawn_x=col,
            spawn_y=row,
            created_at_tick=self.tick,
        )
        self.settlements.append(settlement)
        idx = len(self.settlements) - 1
        self._claim_tiles(settlement, idx, initial=True)
        return settlement

    def spawn_settlements(
        self,
        count: int = DEFAULT_SETTLEMENT_COUNT,
        min_distance: int = SPAWN_MIN_DISTANCE,
    ) -> list[Settlement]:
        """Spawn up to `count` settlements at mutually distant food-rich
        sites. If space runs out the distance constraint is relaxed so
        spawning never silently fails."""
        spawns: list[tuple[int, int]] = [
            (s.spawn_y, s.spawn_x) for s in self.settlements
        ]
        spawned: list[Settlement] = []
        for _ in range(count):
            location = self.find_spawn_location(spawns, min_distance)
            if location is None and min_distance > 0:
                location = self.find_spawn_location(spawns, 0)
            if location is None:
                break
            spawns.append(location)
            row, col = location
            settlement = Settlement(
                name=generate_name(self.world.seed, len(self.settlements)),
                spawn_x=col,
                spawn_y=row,
                created_at_tick=self.tick,
            )
            self.settlements.append(settlement)
            idx = len(self.settlements) - 1
            self._claim_tiles(settlement, idx, initial=True)
            spawned.append(settlement)
        return spawned

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
    # Trade (Sprint 4)
    # ------------------------------------------------------------------

    def settlement_by_id(self, settlement_id: str) -> Settlement | None:
        for s in self.settlements:
            if s.id == settlement_id:
                return s
        return None

    @staticmethod
    def _amounts(settlement: Settlement) -> dict[str, float]:
        amounts = dict(sorted(settlement.resource_inventory.items()))
        amounts["food"] = settlement.food_stock
        return {r: amounts.get(r, 0.0) for r in TRADE_RESOURCES}

    def _territories_adjacent(self, idx_a: int, idx_b: int) -> bool:
        """True if any tile of A is within 1 tile of any tile of B."""
        owned_a = self.world.ownership == idx_a
        owned_b = self.world.ownership == idx_b
        grown = owned_a.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                shifted = np.zeros_like(owned_a)
                ys = slice(max(dy, 0), self.world.size + min(dy, 0))
                xs = slice(max(dx, 0), self.world.size + min(dx, 0))
                yd = slice(max(-dy, 0), self.world.size + min(-dy, 0))
                xd = slice(max(-dx, 0), self.world.size + min(-dx, 0))
                shifted[yd, xd] = owned_a[ys, xs]
                grown |= shifted
        return bool(np.logical_and(grown, owned_b).any())

    def can_establish_route(self, a: Settlement, b: Settlement) -> bool:
        if a is b or not (a.is_alive and b.is_alive):
            return False
        for route in self.trade_routes:
            if not route.active:
                continue
            ids = {route.source_id, route.dest_id}
            if a.id in ids and b.id in ids:
                return False
        return self._territories_adjacent(
            self.settlements.index(a), self.settlements.index(b)
        )

    def establish_route(
        self, source: Settlement, dest: Settlement
    ) -> TradeRoute | None:
        if not self.can_establish_route(source, dest):
            return None
        route = TradeRoute(
            source_id=source.id,
            dest_id=dest.id,
            established_tick=self.tick,
        )
        self.trade_routes.append(route)
        return route

    def _trade_tick(self, route: TradeRoute) -> None:
        """Move 1 unit of the best-arbitrage resource across the route."""
        source = self.settlement_by_id(route.source_id)
        dest = self.settlement_by_id(route.dest_id)
        if source is None or dest is None or not (
            source.is_alive and dest.is_alive
        ):
            route.active = False
            return
        amounts_src = self._amounts(source)
        amounts_dst = self._amounts(dest)
        donor: Settlement | None = None
        receiver: Settlement | None = None
        best_resource: str | None = None
        best_gain = 0.0
        for resource in TRADE_RESOURCES:
            gain_ab = amounts_src[resource] - amounts_dst[resource]
            gain_ba = amounts_dst[resource] - amounts_src[resource]
            if gain_ab > best_gain:
                best_gain, best_resource = gain_ab, resource
                donor, receiver = source, dest
            if gain_ba > best_gain:
                best_gain, best_resource = gain_ba, resource
                donor, receiver = dest, source
        if donor is None or receiver is None or best_resource is None:
            return
        if best_resource == "food":
            donor.food_stock -= TRADE_AMOUNT_PER_TICK
            receiver.food_stock += TRADE_AMOUNT_PER_TICK
        else:
            donor.resource_inventory[best_resource] -= TRADE_AMOUNT_PER_TICK
            receiver.resource_inventory[best_resource] = (
                receiver.resource_inventory.get(best_resource, 0.0)
                + TRADE_AMOUNT_PER_TICK
            )
        route.transfers += 1

    def _auto_trade_rule(self) -> None:
        """Connect every adjacent, unlinked settlement pair."""
        for i, a in enumerate(self.settlements):
            if not a.is_alive:
                continue
            for b in self.settlements[i + 1 :]:
                if b.is_alive:
                    self.establish_route(a, b)

    def active_routes(self) -> list[TradeRoute]:
        return [r for r in self.trade_routes if r.active]

    # ------------------------------------------------------------------
    # Disasters (Sprint 5)
    # ------------------------------------------------------------------

    def _settlement_affected(
        self, settlement: Settlement, event: DisasterEvent
    ) -> bool:
        """Approximate zone overlap: settlement spawn within radius plus a
        territory-reach margin (avoids per-tick full-grid scans)."""
        reach = event.radius + 16
        return (
            max(
                abs(settlement.spawn_x - event.center_x),
                abs(settlement.spawn_y - event.center_y),
            )
            <= reach
        )

    def _drought_multiplier(self, settlement: Settlement) -> float:
        mult = 1.0
        for event in self.disaster_events:
            if (
                event.type == DisasterType.DROUGHT
                and event.is_active(self.tick)
                and self._settlement_affected(settlement, event)
            ):
                mult *= DROUGHT_FARM_MULTIPLIER
        return mult

    def _apply_fire(self, event: DisasterEvent) -> int:
        """Destroy improvements on forest tiles in the zone. Returns count."""
        forest = self.world.terrain == TerrainType.FOREST.value
        y0 = max(0, event.center_y - event.radius)
        y1 = min(self.world.size, event.center_y + event.radius + 1)
        x0 = max(0, event.center_x - event.radius)
        x1 = min(self.world.size, event.center_x + event.radius + 1)
        zone = np.zeros_like(forest)
        zone[y0:y1, x0:x1] = True
        burned = np.logical_and(forest, zone)
        burned = np.logical_and(
            burned, self.world.improvements != Improvement.NONE.value
        )
        count = int(burned.sum())
        self.world.improvements[burned] = Improvement.NONE.value
        return count

    def _apply_plague(self, event: DisasterEvent) -> None:
        for settlement in self.settlements:
            if settlement.is_alive and self._settlement_affected(
                settlement, event
            ):
                settlement.population = int(
                    settlement.population * (1.0 - PLAGUE_MORTALITY)
                )
                if not settlement.is_alive:
                    self._kill(settlement)

    def _check_disasters(self) -> None:
        event = roll_event(self.world.seed, self.tick, self.world.size)
        if event is None:
            return
        self.disaster_events.append(event)
        if event.type == DisasterType.FIRE:
            self._apply_fire(event)
        elif event.type == DisasterType.PLAGUE:
            self._apply_plague(event)

    def active_disasters(self) -> list[DisasterEvent]:
        return [e for e in self.disaster_events if e.is_active(self.tick)]

    # ------------------------------------------------------------------
    # Ruins & re-settlement (Sprint 5)
    # ------------------------------------------------------------------

    def _record_ruin(self, settlement: Settlement) -> RuinSite:
        ruin = RuinSite(
            settlement_id=settlement.id,
            name=f"Ruins of {settlement.name}",
            spawn_x=settlement.spawn_x,
            spawn_y=settlement.spawn_y,
            collapse_tick=self.tick,
        )
        self.ruins.append(ruin)
        return ruin

    def _find_free_tile_near(self, cx: int, cy: int) -> tuple[int, int] | None:
        """Nearest unowned land tile to (cx, cy) by expanding rings."""
        size = self.world.size
        for r in range(0, size):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dy), abs(dx)) != r:
                        continue
                    y, x = cy + dy, cx + dx
                    if not (0 <= y < size and 0 <= x < size):
                        continue
                    if (
                        self.world.ownership[y, x] == UNOWNED
                        and TerrainType(self.world.terrain[y, x])
                        != TerrainType.WATER
                    ):
                        return y, x
        return None

    def _try_resettle_ruin(self, ruin: RuinSite) -> Settlement | None:
        age = self.tick - ruin.collapse_tick
        if age < RUIN_RESETTLE_MIN_AGE or age % 100 != 0:
            return None
        rng = random.Random(
            (self.world.seed ^ RUIN_SEED_OFFSET)
            + zlib.crc32(ruin.id.encode()) * 31
            + age
        )
        if rng.random() >= RUIN_RESETTLE_CHANCE:
            return None
        location = self._find_free_tile_near(ruin.spawn_x, ruin.spawn_y)
        if location is None:
            return None
        row, col = location
        settlement = Settlement(
            name=generate_name(self.world.seed, len(self.settlements)),
            spawn_x=col,
            spawn_y=row,
            created_at_tick=self.tick,
            ruin_origin=ruin.id,
        )
        self.settlements.append(settlement)
        idx = len(self.settlements) - 1
        self._claim_tiles(settlement, idx, initial=True)
        return settlement

    def _ruin_adjacent(self, settlement: Settlement) -> bool:
        """True if any owned tile is within 2 tiles of the origin ruin."""
        if settlement.ruin_origin is None:
            return False
        ruin = next(
            (r for r in self.ruins if r.id == settlement.ruin_origin), None
        )
        if ruin is None:
            return False
        owned = np.argwhere(self.world.ownership == self.settlements.index(settlement))
        for y, x in owned:
            if (
                max(abs(int(y) - ruin.spawn_y), abs(int(x) - ruin.spawn_x))
                <= 2
            ):
                return True
        return False

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

    def _kill(self, settlement: Settlement) -> RuinSite:
        settlement.population = 0
        settlement.destroyed_at_tick = self.tick
        ruin = self._record_ruin(settlement)
        self.release_territory(settlement)
        for route in self.trade_routes:
            if settlement.id in (route.source_id, route.dest_id):
                route.active = False
        return ruin

    def step(self) -> None:
        """Advance the simulation by exactly one tick."""
        self.world.tick += 1
        if self.tick % EVENT_CHECK_INTERVAL_TICKS == 0:
            self._check_disasters()
        for idx, settlement in enumerate(self.settlements):
            if not settlement.is_alive:
                continue
            # Scarcity (any negative inventory) halves construction rate.
            if not settlement.is_in_scarcity or self.tick % 2 == 0:
                self._process_build_queue(settlement)
            if self.tick % BUILD_INTERVAL_TICKS == 0:
                self._auto_build_rule(settlement)
            if self.tick % ROAD_INTERVAL_TICKS == 0:
                self._auto_road_rule(settlement)
            self._produce_resources(settlement)
            income = self.food_income(settlement) * self._drought_multiplier(
                settlement
            )
            settlement.consume_food(
                income,
                capacity=self.food_capacity(settlement),
            )
            was_alive = settlement.is_alive
            growth_multiplier = (
                RUIN_GROWTH_MULTIPLIER if self._ruin_adjacent(settlement) else 1
            )
            settlement.step_population(growth_multiplier)
            settlement.step_happiness(
                building_count=sum(self.buildings_of(settlement).values())
            )
            if was_alive and not settlement.is_alive:
                self._kill(settlement)
                continue
            # Collapse via sustained misery (happiness < 0.1 for 100 ticks).
            if (
                settlement.is_alive
                and settlement.low_happiness_progress
                >= LOW_HAPPINESS_COLLAPSE_TICKS
            ):
                self._kill(settlement)
                continue
            # Economic collapse: negative inventory sustained too long.
            if settlement.is_in_scarcity:
                settlement.negative_inventory_progress += 1
                if (
                    settlement.negative_inventory_progress
                    >= COLLAPSE_INTERVAL_TICKS
                ):
                    settlement.negative_inventory_progress = 0
                    settlement.population -= 1
                    if not settlement.is_alive:
                        self._kill(settlement)
                        continue
            else:
                settlement.negative_inventory_progress = 0
            if (
                settlement.is_alive
                and settlement.net_food_rate > CLAIM_FOOD_SURPLUS_THRESHOLD
                and self.tick % CLAIM_INTERVAL_TICKS == 0
            ):
                self.claim_territory(settlement)
        # Trade: establish new routes periodically, transfer every tick.
        if self.tick % TRADE_INTERVAL_TICKS == 0:
            self._auto_trade_rule()
        for route in self.trade_routes:
            if route.active:
                self._trade_tick(route)
        # Ruins may spontaneously re-settle.
        for ruin in list(self.ruins):
            self._try_resettle_ruin(ruin)

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
            routes = sum(
                1
                for r in self.active_routes()
                if settlement.id in (r.source_id, r.dest_id)
            )
            state = "alive"
        else:
            territory = 0
            buildings = 0
            roads = 0
            routes = 0
            state = "DEAD"
        return (
            f"tick {self.tick:>6} | pop {settlement.population:>4} | "
            f"food {settlement.food_stock:>9.1f} | territory {territory:>4} | "
            f"bld {buildings:>3} | road {roads:>3} | route {routes} | {state}"
        )
