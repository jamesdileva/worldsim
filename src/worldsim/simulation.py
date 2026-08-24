"""Deterministic tick engine: settlements, food, buildings, roads (Sprint 3).

All state transitions are pure functions of (state, tick) â€” no external RNG
after spawn (architecture_detailed.md A1).
"""

from __future__ import annotations

import random
import uuid
import zlib
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .actions import Action, WIRED_ACTIONS, action_category
from .agents import (
    RAID_AGGRESSION_GATE,
    RAID_CADENCE_TICKS,
    Agent,
    RuleBasedAgent,
    placeholder_reward,
)
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
    ContaminationZone,
    DisasterEvent,
    DisasterType,
    DROUGHT_FARM_MULTIPLIER,
    DISASTER_RADIUS,
    EVENT_CHECK_INTERVAL_TICKS,
    PLAGUE_MORTALITY,
    roll_event,
)
from .diplomacy import (
    PEACE_OFFER_VALIDITY_TICKS,
    PEACE_TRIBUTE_FRACTION,
    REPUTATION_RAID_COST,
    REPUTATION_TREATY_BONUS,
    REPUTATION_TRADE_FLOOR,
    DiplomacyState,
)
from .relations import (
    RAID_ATTEMPTED_PENALTY,
    RAID_SUCCESS_PENALTY,
    RelationMatrix,
    TRADE_ESTABLISHED_BONUS,
    TRADE_TRANSFER_BONUS,
    WAR_THRESHOLD,
)
from .settlement import (
    LOW_HAPPINESS_COLLAPSE_TICKS,
    STRATEGY_LABEL_INTERVAL_TICKS,
    assign_personality,
    Settlement,
)
from .treaties import TREATY_PROPOSAL_CADENCE_TICKS
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
# owned tiles each tick â€” prevents an unrecoverable wood deadlock before
# sawmills/mines exist.
GATHER_RATE = 0.25

# Multi-spawn (minimal Sprint 9 pull-forward) and trade (Sprint 4).
DEFAULT_SETTLEMENT_COUNT = 5
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

# Long-horizon stability (Sprint 37): bounded memory at 100k+ ticks.
EVENT_LOG_MAX = 20_000
EXPERIENCE_BUFFER_MAX = 50_000
HISTORY_INTERVAL_TICKS = 500

# Competition (Sprint 9): neighbors, raids, contested zones, events.
# Sprint 11: raised from 48 to 96 â€” sparse worlds left most settlements
# unreachable, which starved trade/diplomacy (and thus strategy emergence).
NEIGHBOR_SPAWN_DISTANCE = 96
RAID_BUILDING_DEBUFF_TICKS = 200
RAID_BUILDING_DEBUFF_MULTIPLIER = 0.5
RAID_THEFT_UNITS = 10
CONTESTED_EXPIRY_TICKS = 400

# Sprint 10: diplomacy constants.
ALLIANCE_MUTUAL_TRADES = 3


@dataclass
class BuildingDebuff:
    """A timed output reduction on one improved tile (raids, disasters)."""

    x: int
    y: int
    multiplier: float
    expires_tick: int
    cause: str

    def active(self, tick: int) -> bool:
        return tick < self.expires_tick


@dataclass
class WorldEvent:
    """An inter-settlement interaction record (Sprint 9)."""

    tick: int
    type: str  # raid | trade_route | relation | disaster | collapse ...
    actor_ids: list[str]
    description: str

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
    grant 2x growth to settlements founded adjacent to the former capital.
    Sprint 36: ruins also remember the dead civilization's era and
    technologies plus salvageable stockpiles for recovery."""

    settlement_id: str
    name: str
    spawn_x: int
    spawn_y: int
    collapse_tick: int
    era: int = 1
    technologies: list[str] = field(default_factory=list)
    salvage: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TradeRoute:
    """A trade link between two settlements (Sprint 4).

    Direction-agnostic: each tick the donor is whichever side holds more of
    the resource the other needs most. Transfers 1 unit/tick."""

    source_id: str
    dest_id: str
    established_tick: int
    transfers: int = 0
    active: bool = True
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # Sprint 10: consecutive alternating-donor transfers (alliance trigger).
    mutual_streak: int = 0
    last_donor_id: str | None = None

    def partner_of(self, settlement_id: str) -> str:
        return self.dest_id if settlement_id == self.source_id else self.source_id


@dataclass
class Simulation:
    world: World
    settlements: list[Settlement] = field(default_factory=list)
    trade_routes: list[TradeRoute] = field(default_factory=list)
    disaster_events: list[DisasterEvent] = field(default_factory=list)
    ruins: list[RuinSite] = field(default_factory=list)
    # One agent per settlement, aligned by index (Sprint 7).
    agents: list[Agent | None] = field(default_factory=list)
    # Buffered (settlement_id, tick, obs, action, reward, next_obs, done).
    experience_buffer: list[tuple] = field(default_factory=list)
    action_counts: dict[int, int] = field(default_factory=dict)
    _pending_transitions: dict[str, tuple] = field(default_factory=dict)
    # Sprint 9: relations, contested zones, debuffs, event log, raids.
    relations: RelationMatrix = field(default_factory=RelationMatrix)
    contested: dict[tuple[int, int], int] = field(default_factory=dict)  # tile -> expiry
    building_debuffs: list[BuildingDebuff] = field(default_factory=list)
    event_log: list[WorldEvent] = field(default_factory=list)
    last_raid_tick: dict[str, int] = field(default_factory=dict)
    _neighbors_cache: dict[str, list[str]] = field(default_factory=dict)
    # Sprint 10: wars, alliances, peace offers, reputation.
    diplomacy: DiplomacyState = field(default_factory=DiplomacyState)
    _interacted_this_tick: set[str] = field(default_factory=set)
    # Sprint 11: strategy memory â€” EMA reward per (archetype, action_id).
    strategy_memory: dict[tuple[str, int], float] = field(default_factory=dict)
    # Perf: per-tick memo for full-grid scans (buildings/territory/roads).
    _tick_cache: dict = field(default_factory=dict)
    _cache_tick: int = -1
    # Sprint 17: difficulty knobs for benchmark/evaluation worlds.
    disaster_chance_mult: float = 1.0
    gather_mult: float = 1.0
    # Sprint 33: inter-settlement highway construction projects.
    highway_projects: list = field(default_factory=list)
    # Sprint 34: formal treaties with clauses (non-aggression, trade
    # pact, tribute). Federations are derived, not stored.
    treaties: list = field(default_factory=list)
    # Sprint 37: compact epoch history for long-run analysis.
    history: list[dict] = field(default_factory=list)
    # Sprint 42: nuclear fallout zones (long-lived yield + happiness
    # debuffs that decay on a fixed schedule).
    contamination_zones: list = field(default_factory=list)

    @property
    def tick(self) -> int:
        return self.world.tick

    def _cached(self, key: tuple, fn):
        """Per-tick memo for expensive full-grid scans. Cleared at tick
        start and on any world mutation, so values are never stale."""
        if self._cache_tick != self.world.tick:
            self._tick_cache.clear()
            self._cache_tick = self.world.tick
        if key not in self._tick_cache:
            self._tick_cache[key] = fn()
        return self._tick_cache[key]

    def _invalidate_cache(self) -> None:
        self._tick_cache.clear()

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

    def _register_settlement(
        self,
        settlement: Settlement,
        ruin_origin: str | None = None,
    ) -> Settlement:
        """Append a settlement, align its agent slot, and claim 3x3."""
        settlement.ruin_origin = ruin_origin
        # Deterministic id: identical runs must produce identical worlds
        # (raid RNG and event logs hash these ids).
        index = len(self.settlements)
        settlement.id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"worldsim/{self.world.seed}/{index}")
        )
        if not settlement.personality:
            settlement.personality = assign_personality(self.world.seed, index)
        # Seed the reputation ledger so non-interaction decay applies.
        self.diplomacy.adjust_rep(settlement.id, 0.0)
        self.settlements.append(settlement)
        idx = len(self.settlements) - 1
        while len(self.agents) < idx:
            self.agents.append(None)
        if idx == len(self.agents):
            self.agents.append(
                RuleBasedAgent(self.world.seed, idx)
            )
        else:
            self.agents[idx] = RuleBasedAgent(self.world.seed, idx)
        self._claim_tiles(settlement, idx, initial=True)
        return settlement

    def spawn_settlement(self) -> Settlement:
        row, col = self.find_spawn_location()
        settlement = Settlement(
            name=generate_name(self.world.seed, len(self.settlements)),
            spawn_x=col,
            spawn_y=row,
            created_at_tick=self.tick,
        )
        return self._register_settlement(settlement)

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
            self._register_settlement(settlement)
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
        self._invalidate_cache()
        return candidates

    def claim_territory(self, settlement: Settlement) -> list[tuple[int, int]]:
        """Expand territory by one ring; returns newly claimed tiles."""
        idx = self.settlements.index(settlement)
        return self._claim_tiles(settlement, idx, initial=False)

    def territory_of(self, settlement: Settlement) -> list[tuple[int, int]]:
        idx = self.settlements.index(settlement)
        return self._cached(("terr", idx), lambda: [
            tuple(t) for t in np.argwhere(self.world.ownership == idx)
        ])

    def release_territory(self, settlement: Settlement) -> None:
        idx = self.settlements.index(settlement)
        mask = self.world.ownership == idx
        # Buildings/roads on lost tiles are destroyed (Sprint 3 acceptance).
        self.world.improvements[mask] = Improvement.NONE.value
        self.world.ownership[mask] = UNOWNED
        self._invalidate_cache()

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
        """Pick the highest-yield owned, unimproved, valid-terrain tile for
        the building type (Sprint 8: agents seek high-yield tiles).

        Scoring: farms maximize terrain food; sawmills prefer dense forest
        neighborhoods; mines prefer mountain neighborhoods. Ties break
        row-major (deterministic)."""
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
        if building_type == BuildingType.FARM:
            score_map = self.world.food_yield_grid().astype(np.float64)
        else:
            # Prefer tiles whose 3x3 neighborhood is richest in the
            # required terrain (forest for sawmills, mountain for mines).
            target = spec.valid_terrain[0].value
            target_mask = (self.world.terrain == target).astype(np.int32)
            kernel = np.ones((3, 3), dtype=np.int32)
            density = self._convolve3x3(target_mask, kernel)
            padded = np.zeros_like(self.world.terrain, dtype=np.float64)
            padded[1:-1, 1:-1] = density
            score_map = padded
        scores = score_map[sites[:, 0], sites[:, 1]]
        best = int(np.argmax(scores))  # first max wins -> row-major tie-break
        y, x = int(sites[best][0]), int(sites[best][1])
        return y, x

    def build_at(
        self,
        settlement: Settlement,
        building_type: BuildingType,
        x: int,
        y: int,
    ) -> bool:
        """Construct a building on an owned, unimproved, valid-terrain tile.
        Returns True if construction succeeded. Era-gated buildings
        (Sprint 31) require the settlement's era to be high enough."""
        spec = BUILDING_SPECS[building_type]
        size = self.world.size
        from .tech import BUILDING_ERA_REQUIREMENTS

        required_era = BUILDING_ERA_REQUIREMENTS.get(building_type, 1)
        if settlement.era < required_era:
            return False
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
        self._invalidate_cache()
        return True

    def destroy_building(self, x: int, y: int) -> bool:
        """Remove any improvement (building or road) from a tile."""
        if self.world.improvements[y, x] == Improvement.NONE.value:
            return False
        self.world.improvements[y, x] = Improvement.NONE.value
        self._invalidate_cache()
        return True

    def buildings_of(self, settlement: Settlement) -> dict[BuildingType, int]:
        """Counts per building type for a settlement (cached per tick)."""
        idx = self.settlements.index(settlement)
        return self._cached(("bld", idx), lambda: {
            btype: int(
                np.logical_and(
                    self._owned_mask(settlement),
                    self.world.improvements
                    == Improvement(btype.value + 1).value,
                ).sum()
            )
            for btype in BuildingType
        })

    def enqueue_build(self, settlement: Settlement, btype: BuildingType) -> None:
        settlement.build_queue.append(btype.name)

    def _process_build_queue(self, settlement: Settlement) -> None:
        if not settlement.build_queue:
            return
        head = settlement.build_queue[0]
        btype = BuildingType[head]
        site = self.find_building_site(settlement, btype)
        if site is None:
            # No valid tile â€” drop the order rather than blocking the queue.
            settlement.build_queue.pop(0)
            return
        if self.build_at(settlement, btype, x=site[1], y=site[0]):
            settlement.build_queue.pop(0)

    def _auto_build_rule(self, settlement: Settlement) -> None:
        """Queue the least-built building type that has an affordable valid
        site â€” keeps the building mix balanced until agents take over."""
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
        self._invalidate_cache()
        return True

    def roads_of(self, settlement: Settlement) -> set[tuple[int, int]]:
        idx = self.settlements.index(settlement)
        return self._cached(("road", idx), lambda: {
            (int(y), int(x))
            for y, x in np.argwhere(
                np.logical_and(
                    self.world.ownership == idx,
                    self.world.improvements == Improvement.ROAD.value,
                )
            )
        })

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
        """Extend the road network by one tile toward unimproved territory;
        when own territory is fully roaded, sponsor highways instead
        (Sprint 33)."""
        from .infrastructure import maybe_start_highways

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
        # Own network saturated: look outward.
        maybe_start_highways(self, settlement)

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
        if self.relations.label(a.id, b.id) == "hostile":
            return False  # hostile pairs don't open trade routes
        if self.diplomacy.at_war(a.id, b.id):
            return False
        # Low-reputation settlements are refused credit (Sprint 10).
        if (
            self.diplomacy.rep(a.id) < REPUTATION_TRADE_FLOOR
            or self.diplomacy.rep(b.id) < REPUTATION_TRADE_FLOOR
        ):
            return False
        for route in self.trade_routes:
            if not route.active:
                continue
            ids = {route.source_id, route.dest_id}
            if a.id in ids and b.id in ids:
                return False
        # Trade needs known neighbors: territory contact OR proximity
        # (Sprint 11 â€” waiting for physical border contact starved the
        # trading strategy in sparse worlds).
        return any(n.id == b.id for n in self.neighbors_of(a))

    def establish_route(
        self, source: Settlement, dest: Settlement
    ) -> TradeRoute | None:
        if not self.can_establish_route(source, dest):
            return None
        route = TradeRoute(
            source_id=source.id,
            dest_id=dest.id,
            established_tick=self.tick,
            # Deterministic id for snapshot reproducibility.
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"worldsim/route/{source.id}/{dest.id}/{self.tick}",
                )
            ),
        )
        source.routes_established += 1
        self.trade_routes.append(route)
        self.relations.adjust(source.id, dest.id, TRADE_ESTABLISHED_BONUS)
        self.log_event(
            "trade_route",
            [source.id, dest.id],
            f"{source.name} and {dest.name} established trade",
        )
        return route

    def _trade_tick(self, route: TradeRoute) -> None:
        """Move the most-valued resource across the route (Sprint 32:
        valuation-gap direction, gap-scaled shipment size)."""
        from .markets import best_trade, transfer_units

        source = self.settlement_by_id(route.source_id)
        dest = self.settlement_by_id(route.dest_id)
        if (
            source is None
            or dest is None
            or not (source.is_alive and dest.is_alive)
            or self.relations.score(source.id, dest.id) < WAR_THRESHOLD
        ):
            route.active = False
            return
        forward = best_trade(self, source, dest)
        backward = best_trade(self, dest, source)
        if forward is None and backward is None:
            return
        if backward is None or (
            forward is not None and forward[1] >= backward[1]
        ):
            donor, receiver, best_resource, gap = (
                source, dest, forward[0], forward[1])
        else:
            donor, receiver, best_resource, gap = (
                dest, source, backward[0], backward[1])

        units = transfer_units(gap, donor.era >= 3)
        if best_resource == "food":
            available = donor.food_stock
        else:
            available = donor.resource_inventory.get(best_resource, 0.0)
        units = min(units, max(0.0, available))
        if units < 0.5:
            return
        from .infrastructure import highway_trade_multiplier
        from .treaties import (
            federation_shipment_multiplier,
            pact_shipment_multiplier,
        )

        units *= highway_trade_multiplier(self, donor.id, receiver.id)
        units *= pact_shipment_multiplier(self, donor.id, receiver.id)
        units *= federation_shipment_multiplier(self, donor.id, receiver.id)
        if best_resource == "food":
            donor.food_stock -= units
            receiver.food_stock += units
        else:
            donor.resource_inventory[best_resource] -= units
            receiver.resource_inventory[best_resource] = (
                receiver.resource_inventory.get(best_resource, 0.0) + units
            )
        route.transfers += 1
        self._interacted_this_tick.update((source.id, dest.id))
        self.relations.adjust(source.id, dest.id, TRADE_TRANSFER_BONUS)
        # Sprint 10: alliance forms from sustained mutual trade.
        if donor.id != route.last_donor_id and route.last_donor_id is not None:
            route.mutual_streak += 1
        else:
            route.mutual_streak = 0
        route.last_donor_id = donor.id
        if (
            route.mutual_streak >= ALLIANCE_MUTUAL_TRADES
            and not self.diplomacy.is_allied(source.id, dest.id)
            and self.diplomacy.form_alliance(source.id, dest.id)
        ):
            self.log_event(
                "alliance",
                [source.id, dest.id],
                f"Alliance formed between {source.name} and {dest.name} "
                f"after {route.mutual_streak} mutual trades",
            )
            self.diplomacy.adjust_rep(source.id, REPUTATION_TREATY_BONUS)
            self.diplomacy.adjust_rep(dest.id, REPUTATION_TREATY_BONUS)

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
    # God Mode (Sprint 6) â€” interventions return before/after state
    # ------------------------------------------------------------------

    def god_smite(self, settlement: Settlement, amount: int) -> tuple[dict, dict]:
        """Kill `amount` population directly."""
        before = {"population": settlement.population}
        settlement.population = max(0, settlement.population - amount)
        if not settlement.is_alive:
            self._kill(settlement)
        after = {"population": settlement.population}
        self._divine(f"smote {settlement.name} ({amount} population)")
        return before, after

    def god_bless_resources(
        self, settlement: Settlement, resource: str, amount: float
    ) -> tuple[dict, dict]:
        """Grant resources (or food) to a settlement."""
        if resource == "food":
            before = {"food_stock": settlement.food_stock}
            settlement.food_stock += amount
            after = {"food_stock": settlement.food_stock}
        else:
            before = {resource: settlement.resource_inventory.get(resource, 0.0)}
            settlement.resource_inventory[resource] = (
                settlement.resource_inventory.get(resource, 0.0) + amount
            )
            after = {resource: settlement.resource_inventory[resource]}
        self._divine(f"blessed {settlement.name} with {amount} {resource}")
        return before, after

    def god_destroy_improvement(self, x: int, y: int) -> tuple[dict, dict]:
        """Remove any improvement from a tile."""
        before = {"improvement": int(self.world.improvements[y, x])}
        self.destroy_building(x, y)
        after = {"improvement": int(self.world.improvements[y, x])}
        self._divine(f"destroyed the improvement at ({x}, {y})")
        return before, after

    # ------------------------------------------------------------------
    # God Mode polish (Sprint 38) â€” Â§16 surface completion + audit trail
    # ------------------------------------------------------------------

    def _divine(self, description: str) -> None:
        """Every intervention leaves a 'divine' event for the audit trail
        (Â§16.3 event history / impact tracking)."""
        self.log_event("divine", [], f"GOD: {description}")

    def god_spawn_settlement(
        self, x: int, y: int, name: str | None = None
    ) -> tuple[dict, dict]:
        """Found a new settlement near (x, y). Deterministic name when not
        given; registers the standard rule agent."""
        location = self._find_free_tile_near(x, y)
        if location is None:
            raise ValueError(f"no free tile near ({x}, {y})")
        row, col = location
        settlement_name = name or generate_name(
            self.world.seed, len(self.settlements))
        settlement = Settlement(
            name=settlement_name,
            spawn_x=col,
            spawn_y=row,
            created_at_tick=self.tick,
        )
        self._register_settlement(settlement)
        before = {"settlements": len(self.settlements) - 1}
        after = {
            "settlements": len(self.settlements),
            "name": settlement.name,
            "x": col,
            "y": row,
        }
        self._divine(
            f"spawned settlement {settlement.name} at ({col}, {row})")
        return before, after

    def god_toggle_freeze(
        self, settlement: Settlement
    ) -> tuple[dict, dict]:
        """Freeze/unfreeze a settlement: time stops for it entirely."""
        before = {"frozen": settlement.frozen}
        settlement.frozen = not settlement.frozen
        after = {"frozen": settlement.frozen}
        state = "froze" if settlement.frozen else "unfroze"
        self._divine(f"{state} settlement {settlement.name}")
        return before, after

    def god_bless_happiness(self, settlement: Settlement) -> tuple[dict, dict]:
        """Restore full happiness and clear all misery counters."""
        before = {
            "happiness": round(settlement.happiness, 4),
            "negative_food_streak": settlement.negative_food_streak,
            "low_happiness_progress": settlement.low_happiness_progress,
        }
        settlement.happiness = 1.0
        settlement.negative_food_streak = 0
        settlement.low_happiness_progress = 0
        after = {"happiness": 1.0, "misery_counters": 0}
        self._divine(f"blessed {settlement.name} with contentment")
        return before, after

    # ------------------------------------------------------------------
    # God Mode depth (Sprint 40) â€” region targeting + mass operations
    # ------------------------------------------------------------------

    def _apply_resource_change(
        self, settlement: Settlement, resource: str, amount: float
    ) -> tuple[float, float]:
        """Apply one resource delta; returns (before, after)."""
        if resource == "food":
            before = settlement.food_stock
            settlement.food_stock = before + amount
            return before, settlement.food_stock
        inventory = settlement.resource_inventory
        before = inventory.get(resource, 0.0)
        after = max(0.0, before + amount)
        inventory[resource] = after
        return before, after

    def god_bless_region(
        self,
        resource: str,
        x: int,
        y: int,
        radius: int,
        amount: float,
    ) -> tuple[dict, dict]:
        """Grant a resource to every alive settlement spawned in the
        circle (Sprint 40 region targeting)."""
        from .regions import circle_tiles, settlements_with_spawns_in

        tiles = set(circle_tiles(self.world.size, x, y, radius))
        targets = settlements_with_spawns_in(self, tiles)
        changes = {}
        for s in targets:
            before, after_v = self._apply_resource_change(s, resource, amount)
            changes[s.name] = {"before": round(before, 2),
                               "after": round(after_v, 2)}
        result = {"resource": resource, "amount": amount,
                  "affected": len(targets), "changes": changes}
        self._divine(
            f"blessed {len(targets)} settlement(s) in the region with "
            f"{amount} {resource}"
        )
        return {"region": [x, y, radius], **result}, result

    def god_strip_region(
        self, resource: str, x: int, y: int, radius: int
    ) -> tuple[dict, dict]:
        """Remove a resource entirely from every settlement in the circle."""
        from .regions import circle_tiles, settlements_with_spawns_in

        tiles = set(circle_tiles(self.world.size, x, y, radius))
        targets = settlements_with_spawns_in(self, tiles)
        stripped = {}
        for s in targets:
            before, after_v = self._apply_resource_change(s, resource, 0.0)
            if resource == "food":
                s.food_stock = 0.0
                after_v = 0.0
            else:
                s.resource_inventory[resource] = 0.0
                after_v = 0.0
            stripped[s.name] = round(before, 2)
        result = {"resource": resource, "stripped_from": len(targets),
                  "amounts_before": stripped}
        self._divine(
            f"stripped all {resource} from {len(targets)} settlement(s)"
        )
        return {"region": [x, y, radius], **result}, result

    def god_smite_region(
        self, x: int, y: int, radius: int, amount: int
    ) -> tuple[dict, dict]:
        """Smite every settlement in the circle (catastrophic â€” CLI gates
        on --force)."""
        from .regions import circle_tiles, settlements_with_spawns_in

        tiles = set(circle_tiles(self.world.size, x, y, radius))
        targets = settlements_with_spawns_in(self, tiles)
        outcomes = {}
        for s in targets:
            pop_before = s.population
            self.god_smite(s, amount)
            outcomes[s.name] = {
                "population": f"{pop_before}->{s.population}",
                "alive": s.is_alive,
            }
        result = {"smited": len(targets), "outcomes": outcomes}
        self._divine(f"smiting a whole region ({len(targets)} settlements)")
        return {"region": [x, y, radius], **result}, result

    def god_mass_bless(
        self, resource: str, amount: float,
        archetype: str | None = None,
    ) -> tuple[dict, dict]:
        """Bless every alive settlement matching the archetype filter
        (None = everyone)."""
        targets = sorted(
            (
                s for s in self.settlements
                if s.is_alive and (
                    archetype is None
                    or s.personality.get("archetype") == archetype
                )
            ),
            key=lambda s: s.name,
        )
        changes = {}
        for s in targets:
            before, after_v = self._apply_resource_change(s, resource, amount)
            changes[s.name] = {"before": round(before, 2),
                               "after": round(after_v, 2)}
        result = {
            "resource": resource, "amount": amount,
            "filter": archetype or "all", "blessed": len(targets),
            "changes": changes,
        }
        self._divine(
            f"mass blessing {len(targets)} settlement(s) "
            f"({archetype or 'all'}) with {amount} {resource}"
        )
        return {"filter": archetype}, result

    def god_bless_land(
        self, x: int, y: int, radius: int, bonus: float
    ) -> tuple[dict, dict]:
        """Permanently enrich land: every tile in the circle gains `bonus`
        food yield (Sprint 40 yield overrides)."""
        from .regions import circle_tiles

        tiles = circle_tiles(self.world.size, x, y, radius)
        for ty, tx in tiles:
            self.world.tile_food_bonus[(ty, tx)] = (
                self.world.tile_food_bonus.get((ty, tx), 0.0) + bonus
            )
        self._invalidate_cache()
        result = {"tiles_enriched": len(tiles), "bonus_each": bonus}
        self._divine(
            f"enriched {len(tiles)} tiles with +{bonus} food yield"
        )
        return {"region": [x, y, radius]}, result

    # ------------------------------------------------------------------
    # God Mode depth (Sprint 41) â€” terrain manipulation
    # ------------------------------------------------------------------

    @staticmethod
    def _improvement_survives_terrain(
        improvement_value: int, terrain_type
    ) -> bool:
        """Policy: buildings die when the new terrain can no longer host
        them; roads survive everywhere except water."""
        from .buildings import BUILDING_SPECS, BuildingType, Improvement
        from .tiles import TerrainType as TT

        if improvement_value == Improvement.NONE.value:
            return True
        if improvement_value == Improvement.ROAD.value:
            return terrain_type != TT.WATER
        for btype in BuildingType:
            if Improvement(btype.value + 1).value == improvement_value:
                spec = BUILDING_SPECS[btype]
                return terrain_type in spec.valid_terrain
        return False

    def _terraform_tile(self, x: int, y: int, terrain_name: str) -> dict:
        """One tile of terraforming. Returns a change record."""
        from .tiles import TERRAIN_PROFILES, TerrainType

        target = {
            t.name.lower(): t for t in TerrainType
        }.get((terrain_name or "").lower())
        if target is None:
            valid = sorted(
                t.name.lower() for t in TerrainType)
            raise ValueError(
                f"unknown terrain '{terrain_name}' (expected one of {valid})"
            )
        size = self.world.size
        if not (0 <= x < size and 0 <= y < size):
            raise ValueError(f"tile ({x}, {y}) out of bounds")
        old = TerrainType(self.world.terrain[y, x])
        before = {"terrain": old.name.lower()}
        self.world.terrain[y, x] = target.value
        # Invalidate derived terrain products.
        self.world._food_grid = None
        self._invalidate_cache()
        improvement_lost = False
        if not self._improvement_survives_terrain(
            int(self.world.improvements[y, x]), target
        ):
            before["improvement"] = int(self.world.improvements[y, x])
            self.world.improvements[y, x] = Improvement.NONE.value
            improvement_lost = True
        after = {
            "terrain": target.name.lower(),
            "movement_cost": TERRAIN_PROFILES[target].movement_cost,
            "improvement_lost": improvement_lost,
        }
        return {"before": before, "after": after}

    def god_terraform(self, x: int, y: int, terrain_name: str) -> tuple[
        dict, dict
    ]:
        """Transform a single tile's terrain (Sprint 41)."""
        record = self._terraform_tile(x, y, terrain_name)
        self._divine(
            f"turned tile ({x}, {y}) into {record['after']['terrain']}"
            + (
                "; an improvement was lost to the new terrain"
                if record["after"]["improvement_lost"]
                else ""
            )
        )
        return record["before"], record["after"]

    def god_terraform_region(
        self, x: int, y: int, radius: int, terrain_name: str
    ) -> tuple[dict, dict]:
        """Transform every tile in the circle (Sprint 41)."""
        from .regions import circle_tiles

        tiles = circle_tiles(self.world.size, x, y, radius)
        changed = 0
        improvements_lost = 0
        for ty, tx in tiles:
            current = self.world.terrain[ty, tx]
            record = self._terraform_tile(tx, ty, terrain_name)
            if record["before"]["terrain"] != record["after"]["terrain"]:
                changed += 1
            if record["after"]["improvement_lost"]:
                improvements_lost += 1
        result = {
            "terrain": terrain_name,
            "tiles_changed": changed,
            "improvements_lost": improvements_lost,
        }
        self._divine(
            f"reshaped {changed} tiles into {terrain_name} "
            f"({improvements_lost} improvements lost)"
        )
        return {"region": [x, y, radius]}, result

    # ------------------------------------------------------------------
    # God Mode depth (Sprint 42) â€” nuclear events
    # ------------------------------------------------------------------

    def _contaminated_tiles_mask(self) -> np.ndarray | None:
        """Boolean mask of tiles inside any active fallout zone."""
        size = self.world.size
        mask = np.zeros((size, size), dtype=bool)
        any_active = False
        tick = self.tick
        for zone in self.contamination_zones:
            if not zone.is_active(tick):
                continue
            any_active = True
            y0 = max(0, zone.center_y - zone.radius)
            y1 = min(size, zone.center_y + zone.radius + 1)
            x0 = max(0, zone.center_x - zone.radius)
            x1 = min(size, zone.center_x + zone.radius + 1)
            mask[y0:y1, x0:x1] = True
        return mask if any_active else None

    def _settlement_contaminated(self, settlement: Settlement) -> bool:
        """True while an active fallout zone covers the settlement spawn."""
        tick = self.tick
        return any(
            zone.is_active(tick) and zone.covers(
                settlement.spawn_x, settlement.spawn_y)
            for zone in self.contamination_zones
        )

    def god_nuke(self, x: int, y: int) -> tuple[dict, dict]:
        """Nuclear strike (Â§16.2): annihilate everything in the blast
        radius and leave decades of contamination behind."""
        from .disasters import (
            CONTAMINATION_TICKS,
            NUKE_POPULATION_FRACTION,
            NUKE_RADIUS,
        )
        from .regions import circle_tiles

        blast = set(circle_tiles(self.world.size, x, y, NUKE_RADIUS))
        before = {"zones": len(self.contamination_zones)}

        # 1. Improvements annihilated across the whole blast.
        destroyed = 0
        for ty, tx in blast:
            if self.world.improvements[ty, tx] != Improvement.NONE.value:
                self.world.improvements[ty, tx] = Improvement.NONE.value
                destroyed += 1

        # 2. Population: settlements spawned in the fireball lose most of
        # their people; deaths route through the real kill path so ruins,
        # refugee migration, and recovery all behave as usual.
        deaths: dict[str, int] = {}
        for s in [
            s for s in self.settlements
            if s.is_alive and (s.spawn_y, s.spawn_x) in blast
        ]:
            pop_before = s.population
            killed = max(0, round(s.population * NUKE_POPULATION_FRACTION))
            s.population -= killed
            deaths[s.name] = killed
            if not s.is_alive:
                self._kill(s)

        # 3. Contamination over the blast area for decades.
        zone = ContaminationZone(
            center_x=x,
            center_y=y,
            radius=NUKE_RADIUS,
            start_tick=self.tick,
            end_tick=self.tick + CONTAMINATION_TICKS,
        )
        self.contamination_zones.append(zone)
        self._invalidate_cache()

        after = {
            "zones": len(self.contamination_zones),
            "contaminated_until": zone.end_tick,
            "improvements_destroyed": destroyed,
            "deaths": deaths,
            "blast_radius": NUKE_RADIUS,
        }
        self._divine(
            f"detonated a nuclear weapon at ({x}, {y}); "
            f"{destroyed} improvements annihilated, "
            f"contamination until tick {zone.end_tick}"
        )
        return before, after

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
        self._invalidate_cache()
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
        chance = BASE_EVENT_CHANCE * self.disaster_chance_mult
        event = roll_event(self.world.seed, self.tick, self.world.size,
                           chance=min(chance, 1.0))
        if event is None:
            return
        self.disaster_events.append(event)
        self._apply_disaster(event)

    def _apply_disaster(self, event: DisasterEvent) -> dict:
        """Immediate effects for one disaster (shared by random events and
        authored god disasters â€” Sprint 39)."""
        effect: dict = {}
        if event.type == DisasterType.FIRE:
            effect["tiles_burned"] = self._apply_fire(event)
        elif event.type == DisasterType.PLAGUE:
            before_pops = {
                s.id: s.population for s in self.settlements if s.is_alive
            }
            self._apply_plague(event)
            deaths = {
                s.id: before_pops[s.id] - s.population
                for s in self.settlements
                if s.id in before_pops
                and s.population < before_pops[s.id]
            }
            effect["deaths"] = sum(deaths.values())
        return effect

    def god_trigger_disaster(
        self,
        disaster_type: str,
        x: int,
        y: int,
        radius: int = DISASTER_RADIUS,
        duration: int | None = None,
    ) -> tuple[dict, dict]:
        """Author a disaster on demand (Sprint 39). Uses the exact same
        application mechanics as random events; drought duration defaults
        to the standard drought length."""
        type_map = {
            "drought": DisasterType.DROUGHT,
            "fire": DisasterType.FIRE,
            "plague": DisasterType.PLAGUE,
        }
        if disaster_type not in type_map:
            raise ValueError(
                f"unknown disaster type '{disaster_type}' "
                f"(expected one of {sorted(type_map)})"
            )
        dtype = type_map[disaster_type]
        if duration is None:
            from .disasters import DROUGHT_DURATION_TICKS

            duration = (
                DROUGHT_DURATION_TICKS
                if dtype == DisasterType.DROUGHT
                else 1
            )
        affected = sorted(
            s.name for s in self.settlements
            if s.is_alive and self._settlement_affected(
                s, DisasterEvent(
                    type=dtype, center_x=x, center_y=y, radius=radius,
                    start_tick=self.tick, duration=duration,
                    id="preview",
                )
            )
        )
        before = {
            "disasters": len(self.disaster_events),
            "affected_settlements": affected,
        }
        event = DisasterEvent(
            type=dtype,
            center_x=x,
            center_y=y,
            radius=radius,
            start_tick=self.tick,
            duration=duration,
            id=str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"worldsim/god-disaster/{self.world.seed}/{self.tick}/{x}/{y}/{dtype.name}",
            )),
        )
        self.disaster_events.append(event)
        effect = self._apply_disaster(event)
        after = {
            "disasters": len(self.disaster_events),
            "type": dtype.name.lower(),
            **effect,
        }
        self._divine(
            f"unleashed a {dtype.name.lower()} at ({x}, {y}) "
            f"r={radius} for {duration} ticks"
        )
        return before, after

    def active_disasters(self) -> list[DisasterEvent]:
        return [e for e in self.disaster_events if e.is_active(self.tick)]

    # ------------------------------------------------------------------
    # Ruins & re-settlement (Sprint 5)
    # ------------------------------------------------------------------

    def _record_ruin(self, settlement: Settlement) -> RuinSite:
        from .recovery import salvage_from

        ruin = RuinSite(
            settlement_id=settlement.id,
            name=f"Ruins of {settlement.name}",
            spawn_x=settlement.spawn_x,
            spawn_y=settlement.spawn_y,
            collapse_tick=self.tick,
            # Sprint 36: enriched ruins carry forward knowledge + salvage.
            era=settlement.era,
            technologies=list(settlement.technologies),
            salvage=salvage_from(settlement),
            # Deterministic id (resettle RNG hashes it).
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"worldsim/{settlement.id}/ruin/{self.tick}",
                )
            ),
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
        # Sprint 36: knowledge recovery â€” settlers inherit the ruined
        # civilization's technologies and salvage its stockpiles.
        settlement.technologies = list(ruin.technologies)
        for resource, amount in ruin.salvage.items():
            settlement.resource_inventory[resource] = (
                settlement.resource_inventory.get(resource, 0.0) + amount)
        self._register_settlement(settlement, ruin_origin=ruin.id)
        recovered = ", ".join(sorted(settlement.technologies)) or "nothing"
        self.log_event(
            "recovery",
            [settlement.id],
            f"{settlement.name} rose from the ruins of {ruin.name}; "
            f"settlers recovered {recovered}",
        )
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
    # Agent action dispatch (Sprint 7)
    # ------------------------------------------------------------------

    def _agent_build(self, settlement: Settlement, btype: BuildingType) -> bool:
        site = self.find_building_site(settlement, btype)
        if site is None:
            return False
        return self.build_at(settlement, btype, x=site[1], y=site[0])

    def _act_build_farm(self, s: Settlement) -> bool:
        return self._agent_build(s, BuildingType.FARM)

    def _act_build_sawmill(self, s: Settlement) -> bool:
        return self._agent_build(s, BuildingType.SAWMILL)

    def _act_build_mine(self, s: Settlement) -> bool:
        return self._agent_build(s, BuildingType.MINE)

    def _act_build_granary(self, s: Settlement) -> bool:
        return self._agent_build(s, BuildingType.GRANARY)

    def _act_build_road(self, s: Settlement) -> bool:
        return self._auto_road_rule(s)

    def _act_expand_road_network(self, s: Settlement) -> bool:
        return self._auto_road_rule(s)

    def _act_initiate_raid(self, s: Settlement) -> bool:
        return self.initiate_raid(s)

    def _act_train_raider(self, s: Settlement) -> bool:
        """Sprint 35: food -> army (offensive pool)."""
        from .warfare import TRAIN_RAIDER_ARMY_GAIN, TRAIN_RAIDER_FOOD_COST, can_train_raider

        ok, _reason = can_train_raider(s)
        if not ok:
            return False
        s.food_stock -= TRAIN_RAIDER_FOOD_COST
        s.army += TRAIN_RAIDER_ARMY_GAIN
        return True

    def _act_train_defender(self, s: Settlement) -> bool:
        """Sprint 35: food+wood -> a smaller army gain and fortification."""
        from .warfare import (
            TRAIN_DEFENDER_ARMY_GAIN,
            TRAIN_DEFENDER_FOOD_COST,
            TRAIN_DEFENDER_FORT_GAIN,
            TRAIN_DEFENDER_WOOD_COST,
            can_train_defender,
        )

        ok, _reason = can_train_defender(s)
        if not ok:
            return False
        s.food_stock -= TRAIN_DEFENDER_FOOD_COST
        s.resource_inventory["wood"] = (
            s.resource_inventory.get("wood", 0.0) - TRAIN_DEFENDER_WOOD_COST)
        s.army += TRAIN_DEFENDER_ARMY_GAIN
        s.fort_level = min(3, s.fort_level + TRAIN_DEFENDER_FORT_GAIN)
        return True

    def _act_fortify_border(self, s: Settlement) -> bool:
        """Sprint 35: stone -> border forts (+battle defense)."""
        from .warfare import FORTIFY_STONE_COST, can_fortify

        ok, _reason = can_fortify(s)
        if not ok:
            return False
        s.resource_inventory["stone"] = (
            s.resource_inventory.get("stone", 0.0) - FORTIFY_STONE_COST)
        s.fort_level += 1
        return True

    def _act_offer_peace(self, s: Settlement) -> bool:
        """Offer peace to a war opponent. One-sided until they reciprocate."""
        wars = self.diplomacy.wars_of(s.id)
        if not wars:
            return False
        offered = False
        for key in wars:
            enemy_id = next(pid for pid in key if pid != s.id)
            if self.diplomacy.has_live_offer(s.id, enemy_id, self.tick):
                continue  # already offered
            self.diplomacy.offer_peace(s.id, enemy_id, self.tick)
            offered = True
            enemy = self.settlement_by_id(enemy_id)
            enemy_name = enemy.name if enemy else "unknown"
            self.log_event(
                "peace_offer",
                [s.id, enemy_id],
                f"{s.name} sent a peace offer to {enemy_name}",
            )
        return offered

    def _act_accept_peace(self, s: Settlement) -> bool:
        """Respond to a live incoming offer. Accepting constitutes sending
        our own matching offer; peace concludes once BOTH sides have live
        offers on the table (spec: both parties must send peace offers)."""
        for key in list(self.diplomacy.wars_of(s.id)):
            enemy_id = next(pid for pid in key if pid != s.id)
            if not self.diplomacy.has_live_offer(enemy_id, s.id, self.tick):
                continue
            enemy = self.settlement_by_id(enemy_id)
            if enemy is None:
                continue
            # Our acceptance sends our own offer in response.
            self.diplomacy.offer_peace(s.id, enemy_id, self.tick)
            both_live = self.diplomacy.has_live_offer(
                s.id, enemy_id, self.tick
            ) and self.diplomacy.has_live_offer(enemy_id, s.id, self.tick)
            if both_live:
                return self.conclude_peace(s, enemy)
            return False
        return False

    def conclude_peace(self, a: Settlement, b: Settlement) -> bool:
        """End the a-b war; the side that raided more pays 25% tribute."""
        raids_by_a, raids_by_b = self.diplomacy.conclude_peace(a.id, b.id)
        aggressor, victim = (
            (a, b) if raids_by_a >= raids_by_b else (b, a)
        )
        # Tribute: 25% of the aggressor's stockpiles.
        for resource in ("food", "wood", "stone"):
            if resource == "food":
                tribute = max(0.0, aggressor.food_stock * PEACE_TRIBUTE_FRACTION)
                aggressor.food_stock -= tribute
                victim.food_stock += tribute
            else:
                held = max(
                    aggressor.resource_inventory.get(resource, 0.0), 0.0
                )
                tribute = held * PEACE_TRIBUTE_FRACTION
                aggressor.resource_inventory[resource] = held - tribute
                victim.resource_inventory[resource] = (
                    victim.resource_inventory.get(resource, 0.0) + tribute
                )
        # Relations settle just below hostile threshold; both gain reputation.
        self.relations.set_score(a.id, b.id, -20.0)
        self.diplomacy.adjust_rep(a.id, REPUTATION_TREATY_BONUS)
        self.diplomacy.adjust_rep(b.id, REPUTATION_TREATY_BONUS)
        self.log_event(
            "peace",
            [a.id, b.id],
            f"Peace concluded between {a.name} and {b.name}; "
            f"{aggressor.name} paid 25% stockpile tribute",
        )
        return True

    def _act_boost_morale(self, s: Settlement) -> bool:
        """Small happiness bump; costs nothing this sprint."""
        from .settlement import HAPPINESS_MAX

        s.happiness = min(HAPPINESS_MAX, s.happiness + 0.01)
        return True

    def _act_claim_territory(self, s: Settlement) -> bool:
        return len(self.claim_territory(s)) > 0

    def _act_establish_trade_route(self, s: Settlement) -> bool:
        before = len(self.active_routes())
        self._auto_trade_rule()
        return len(self.active_routes()) > before

    def execute_action(self, settlement: Settlement, action_id: int) -> bool:
        """Apply an agent decision. Unwired actions are validated no-ops."""
        try:
            action = Action(action_id)
        except ValueError:
            return False
        method_name = WIRED_ACTIONS.get(action)
        if method_name is None:
            return False
        method = getattr(self, f"_act_{method_name}", None)
        if method is None:
            # Named in WIRED_ACTIONS but no handler yet â€” treat as no-op.
            return False
        return bool(method(settlement))

    # ------------------------------------------------------------------
    # Neighbors, relations, raids (Sprint 9)
    # ------------------------------------------------------------------

    def log_event(
        self, type_: str, actor_ids: list[str], description: str
    ) -> None:
        self.event_log.append(
            WorldEvent(
                tick=self.tick,
                type=type_,
                actor_ids=actor_ids,
                description=description,
            )
        )
        # Sprint 37: bound memory at 100k+ ticks. Oldest events drop;
        # events are advisory/log-only so physics never depended on them.
        if len(self.event_log) > EVENT_LOG_MAX:
            del self.event_log[: len(self.event_log) - EVENT_LOG_MAX]

    def neighbors_of(self, settlement: Settlement) -> list[Settlement]:
        """Settlements within spawn distance or territory contact.
        Cached per tick."""
        cached = self._neighbors_cache.get(settlement.id)
        if cached is not None:
            return [
                s for s in self.settlements if s.id in cached and s.is_alive
            ]
        ids: list[str] = []
        for other in self.settlements:
            if other is settlement or not other.is_alive:
                continue
            dist = max(
                abs(other.spawn_x - settlement.spawn_x),
                abs(other.spawn_y - settlement.spawn_y),
            )
            if dist <= NEIGHBOR_SPAWN_DISTANCE or self._territories_adjacent(
                self.settlements.index(settlement),
                self.settlements.index(other),
            ):
                ids.append(other.id)
        self._neighbors_cache[settlement.id] = ids
        return [s for s in self.settlements if s.id in ids]

    def _refresh_contested_zones(self) -> None:
        """Recompute contested border tiles: hostile-pair borders, plus
        borders of warlike military settlements (military pressure creates
        border friction even before hostility)."""
        self.contested = {}
        friction_pairs: list[tuple[str, str]] = [
            (id_a, id_b)
            for id_a, id_b, score in self.relations.pairs()
            if score < -25.0
        ]
        for settlement in self.settlements:
            if (
                settlement.is_alive
                and self._is_warlike_military(settlement)
            ):
                for neighbor in self.neighbors_of(settlement):
                    if not self.diplomacy.is_allied(
                        settlement.id, neighbor.id
                    ):
                        friction_pairs.append(
                            (settlement.id, neighbor.id)
                        )
        for id_a, id_b in friction_pairs:
            a = self.settlement_by_id(id_a)
            b = self.settlement_by_id(id_b)
            if a is None or b is None or not (a.is_alive and b.is_alive):
                continue
            idx_a, idx_b = (
                self.settlements.index(a),
                self.settlements.index(b),
            )
            owned_a = self.world.ownership == idx_a
            owned_b = self.world.ownership == idx_b
            grown = owned_a.copy()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    shifted = np.zeros_like(owned_a)
                    ys = slice(max(dy, 0), self.world.size + min(dy, 0))
                    xs = slice(max(dx, 0), self.world.size + min(dx, 0))
                    yd = slice(max(-dy, 0), self.world.size + min(-dy, 0))
                    xd = slice(max(-dx, 0), self.world.size + min(-dx, 0))
                    shifted[yd, xd] = owned_a[ys, xs]
                    grown |= shifted
            size = self.world.size
            for y, x in np.argwhere(np.logical_and(grown, owned_b)):
                for dyy in (-1, 0, 1):
                    for dxx in (-1, 0, 1):
                        ny, nx = int(y) + dyy, int(x) + dxx
                        if 0 <= ny < size and 0 <= nx < size:
                            # Keys are (x, y) tile coords, matching
                            # is_contested().
                            self.contested[(nx, ny)] = (
                                self.tick + CONTESTED_EXPIRY_TICKS
                            )

    def is_contested(self, x: int, y: int) -> bool:
        return (x, y) in self.contested

    def _debuff_multiplier(self, x: int, y: int) -> float:
        mult = 1.0
        for debuff in self.building_debuffs:
            if debuff.active(self.tick) and debuff.x == x and debuff.y == y:
                mult *= debuff.multiplier
        return mult

    def _is_warlike_military(self, settlement: Settlement) -> bool:
        """Military archetypes project force; border friction follows."""
        return settlement.personality.get("archetype") == "military"

    def _raidable_targets(
        self, attacker: Settlement
    ) -> dict[str, list[tuple[int, int]]]:
        """Improved tiles of neighbor settlements inside contested zones,
        grouped by defender id. Warlike military attackers may also target
        neutral neighbors (aggression creates hostility)."""
        warlike = self._is_warlike_military(attacker)
        targets: dict[str, list[tuple[int, int]]] = {}
        from .treaties import CLAUSE_NON_AGGRESSION, has_clause

        for neighbor in self.neighbors_of(attacker):
            hostile = self.relations.is_hostile(attacker.id, neighbor.id)
            if not hostile and not (
                warlike and not self.diplomacy.is_allied(attacker.id, neighbor.id)
            ):
                continue
            if has_clause(
                self, attacker.id, neighbor.id, CLAUSE_NON_AGGRESSION
            ):
                continue  # treaty floor beats even military friction
            idx = self.settlements.index(neighbor)
            improved = np.argwhere(
                np.logical_and(
                    self.world.ownership == idx,
                    self.world.improvements != Improvement.NONE.value,
                )
            )
            tiles = [
                (int(y), int(x))
                for y, x in improved
                if self.is_contested(int(x), int(y))
            ]
            if tiles:
                targets[neighbor.id] = tiles
        return targets

    def initiate_raid(self, attacker: Settlement) -> bool:
        """Attempt a raid on a hostile neighbour's resource buildings.

        Success: building output halved for 200 ticks + resource theft.
        Either way relations sour and war escalates. Returns True on
        success."""
        import random as _random

        # Cadence enforcement (defense in depth with the agent policy).
        last = self.last_raid_tick.get(attacker.id)
        if last is not None and self.tick - last < RAID_CADENCE_TICKS:
            return False

        # Allies are never valid targets (Sprint 10 non-aggression floor).
        targets_by_defender = {
            did: tiles
            for did, tiles in self._raidable_targets(attacker).items()
            if not self.diplomacy.is_allied(attacker.id, did)
        }
        if not targets_by_defender:
            return False
        if not targets_by_defender:
            return False
        defender_id = sorted(targets_by_defender.keys())[0]
        defender = self.settlement_by_id(defender_id)
        if defender is None or not defender.is_alive:
            return False

        self.last_raid_tick[attacker.id] = self.tick
        attacker.raids_committed += 1
        self._interacted_this_tick.update((attacker.id, defender.id))
        aggression = attacker.personality.get("aggression", 0.5)
        size_factor = min(defender.population / 100.0, 0.4)
        success_chance = max(
            0.2, min(0.9, 0.4 + aggression * 0.4 - size_factor)
        )
        rng = _random.Random(
            (self.world.seed ^ 0x8BAD)
            + self.tick * 7919
            + zlib.crc32(attacker.id.encode()) % 99991
        )
        success = rng.random() < success_chance

        self.relations.adjust(
            attacker.id, defender.id, -RAID_ATTEMPTED_PENALTY
        )
        # Diplomatic escalation + reputation (Sprint 10).
        war_declared = self.diplomacy.record_raid(
            attacker.id, defender.id, self.tick
        )
        self.diplomacy.adjust_rep(attacker.id, -REPUTATION_RAID_COST)
        if war_declared:
            self.relations.adjust(attacker.id, defender.id, -200.0)
            self.log_event(
                "war",
                [attacker.id, defender.id],
                f"War declared: {attacker.name} raided {defender.name} "
                f"3 times within 500 ticks",
            )
        if not success:
            self.log_event(
                "raid",
                [attacker.id, defender.id],
                f"{attacker.name} raided {defender.name} and failed",
            )
            return False

        tiles = targets_by_defender[defender_id]
        for y, x in tiles:
            self.building_debuffs.append(
                BuildingDebuff(
                    x=x,
                    y=y,
                    multiplier=RAID_BUILDING_DEBUFF_MULTIPLIER,
                    expires_tick=self.tick + RAID_BUILDING_DEBUFF_TICKS,
                    cause="raid",
                )
            )
        stolen: dict[str, float] = {}
        for resource in ("wood", "stone"):
            available = max(defender.resource_inventory.get(resource, 0.0), 0.0)
            take = min(float(RAID_THEFT_UNITS), available)
            if take > 0:
                defender.resource_inventory[resource] = available - take
                attacker.resource_inventory[resource] = (
                    attacker.resource_inventory.get(resource, 0.0) + take
                )
                stolen[resource] = round(take, 1)
        self.relations.adjust(attacker.id, defender.id, -RAID_SUCCESS_PENALTY)
        self.log_event(
            "raid",
            [attacker.id, defender.id],
            f"{attacker.name} raided {defender.name}: output halved for "
            f"{RAID_BUILDING_DEBUFF_TICKS} ticks, stole {stolen}",
        )
        return True

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    def _advance_research(self, settlement: Settlement) -> None:
        """Sprint 31: accumulate research; auto-discover in fixed order.
        Deterministic: rate is a pure function of population + era."""
        if not settlement.is_alive:
            return
        from .tech import (
            ERA_RESEARCH_MULTIPLIER,
            BASE_RESEARCH_RATE,
            TECH_RESEARCH_COSTS,
            next_technology,
        )

        rate = (
            BASE_RESEARCH_RATE
            * settlement.population
            * ERA_RESEARCH_MULTIPLIER[settlement.era]
        )
        settlement.research_points += rate
        nxt = next_technology(settlement.technologies)
        if nxt is not None and settlement.research_points >= (
                TECH_RESEARCH_COSTS[nxt]):
            era_before = settlement.era
            settlement.research_points -= TECH_RESEARCH_COSTS[nxt]
            settlement.technologies.append(nxt)
            self.log_event(
                "technology",
                [settlement.id],
                f"{settlement.name} discovered {nxt}",
            )
            new_era = settlement.era
            if new_era > era_before:
                self.log_event(
                    "era",
                    [settlement.id],
                    f"{settlement.name} advanced to Era {new_era}",
                )

    def food_income(self, settlement: Settlement) -> float:
        """Terrain food yields + farm output (with raid debuffs) on owned
        tiles. Era III grants +15% farm output (Sprint 31). Memoized per
        tick (Sprint 37)."""
        idx = self.settlements.index(settlement)
        return self._cached(
            ("foodinc", idx), lambda: self._compute_food_income(settlement)
        )

    def _compute_food_income(self, settlement: Settlement) -> float:
        idx = self.settlements.index(settlement)
        owned = self.world.ownership == idx
        yields = self.world.food_yield_grid()
        # Sprint 42: nuclear fallout suppresses yields in its zone.
        contaminated = self._contaminated_tiles_mask()
        if contaminated is not None:
            from .disasters import CONTAMINATION_YIELD_FACTOR

            factor = np.where(
                contaminated,
                CONTAMINATION_YIELD_FACTOR,
                1.0,
            )
            income = float((yields * factor)[owned].sum())
        else:
            income = float(yields[owned].sum())
        # Sprint 40: god-blessed land adds per-tile food yield bonuses.
        if self.world.tile_food_bonus:
            bonus = sum(
                v for (ty, tx), v in self.world.tile_food_bonus.items()
                if 0 <= ty < self.world.size and 0 <= tx < self.world.size
                and owned[ty, tx]
            )
            income += bonus
        from .tech import ERA3_FARM_OUTPUT_BONUS

        farm_mult = 1.0 + (
            ERA3_FARM_OUTPUT_BONUS if settlement.era >= 3 else 0.0
        )
        farm_tiles = np.argwhere(
            np.logical_and(
                owned, self.world.improvements == Improvement.FARM.value
            )
        )
        for y, x in farm_tiles:
            income += (
                BUILDING_SPECS[BuildingType.FARM].food_output
                * self._debuff_multiplier(int(x), int(y))
                * farm_mult
            )
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
        """Add building output (with raid debuffs) + passive gathering to the
        inventory. Vectorized via bincounts; debuffs stay per-tile."""
        idx = self.settlements.index(settlement)
        owned = self.world.ownership == idx
        produced = {"wood": 0.0, "stone": 0.0, "metal": 0.0}
        # Fast path: no active debuffs -> pure bincount, no per-tile loops.
        if not self.building_debuffs:
            imp_counts = np.bincount(
                self.world.improvements[owned].astype(np.int64) + 1,
                minlength=6,
            )
            for btype in BuildingType:
                count = int(imp_counts[btype.value + 2])
                if count == 0:
                    continue
                spec = BUILDING_SPECS[btype]
                produced["wood"] += spec.wood_output * count
                produced["stone"] += spec.stone_output * count
                produced["metal"] += spec.metal_output * count
        else:
            for btype in BuildingType:
                imp = Improvement(btype.value + 1)
                tiles = np.argwhere(
                    np.logical_and(
                        owned, self.world.improvements == imp.value
                    )
                )
                if len(tiles) == 0:
                    continue
                spec = BUILDING_SPECS[btype]
                for y, x in tiles:
                    mult = self._debuff_multiplier(int(x), int(y))
                    produced["wood"] += spec.wood_output * mult
                    produced["stone"] += spec.stone_output * mult
                    produced["metal"] += spec.metal_output * mult
        # Passive gathering: one bincount over terrain composition.
        terrain_counts = np.bincount(
            self.world.terrain[owned], minlength=len(TerrainType)
        )
        for i, tt in enumerate(TerrainType):
            profile = TERRAIN_PROFILES[tt]
            n = int(terrain_counts[i])
            if n == 0:
                continue
            produced["wood"] += n * profile.wood * GATHER_RATE * self.gather_mult
            produced["stone"] += n * profile.stone * GATHER_RATE * self.gather_mult
            produced["metal"] += n * profile.metal * GATHER_RATE * self.gather_mult
        inv = settlement.resource_inventory
        for res, amount in produced.items():
            inv[res] = inv.get(res, 0.0) + amount

    @staticmethod
    def from_state_json(state_json: str) -> "Simulation":
        """Rebuild a Simulation from a serialized snapshot (Sprint 43:
        undo / branching replays)."""
        from .db import deserialize_world

        (
            world,
            settlements,
            trade_routes,
            ruins,
            disaster_events,
            relations,
            contested,
            building_debuffs,
            event_log,
            diplomacy,
            strategy_memory,
            highway_projects,
            treaties,
            contamination_zones,
        ) = deserialize_world(state_json)
        return simulation_from_state(
            world,
            settlements,
            trade_routes=trade_routes,
            ruins=ruins,
            disaster_events=disaster_events,
            relations=relations,
            contested=contested,
            building_debuffs=building_debuffs,
            event_log=event_log,
            diplomacy=diplomacy,
            strategy_memory=strategy_memory,
            highway_projects=highway_projects,
            treaties=treaties,
            contamination_zones=contamination_zones,
        )

    def _record_history_epoch(self) -> None:
        """Append one compact epoch record (Sprint 37). Deterministic."""
        living = [s for s in self.settlements if s.is_alive]
        from .markets import market_prices

        self.history.append({
            "tick": self.tick,
            "settlements_alive": len(living),
            "total_population": sum(s.population for s in living),
            "wars_active": len(self.diplomacy.wars),
            "routes_active": len(self.active_routes()),
            "mean_happiness": (
                round(sum(s.happiness for s in living) / len(living), 4)
                if living else 0.0
            ),
            "prices": market_prices(self),
        })

    def _kill(self, settlement: Settlement) -> RuinSite:
        # Sprint 36: refugees flee to allies/federation before the end.
        from .recovery import migrate_refugees

        migrate_refugees(self, settlement)
        settlement.population = 0
        settlement.destroyed_at_tick = self.tick
        ruin = self._record_ruin(settlement)
        self.release_territory(settlement)
        for route in self.trade_routes:
            if settlement.id in (route.source_id, route.dest_id):
                route.active = False
        return ruin

    def step(self, skip_agent_ids: set[str] | None = None) -> None:
        """Advance the simulation by exactly one tick.

        Settlements listed in skip_agent_ids still produce/consume/populate
        but skip their agent decision this tick (used by the RL environment,
        which supplies the action externally)."""
        self.world.tick += 1
        self._neighbors_cache.clear()
        self.relations.decay_tick()
        self.diplomacy.decay_tick(self._interacted_this_tick)
        self.diplomacy.expire_stale_offers(self.tick)
        self._interacted_this_tick = set()
        if self.tick % 50 == 0:
            self._refresh_contested_zones()
        # Expire building debuffs.
        self.building_debuffs = [
            d for d in self.building_debuffs if d.active(self.tick)
        ]
        if self.tick % EVENT_CHECK_INTERVAL_TICKS == 0:
            self._check_disasters()
        # Sprint 37: compact epoch history (pure function of state).
        if self.tick % HISTORY_INTERVAL_TICKS == 0:
            self._record_history_epoch()
        skip = skip_agent_ids or set()
        for idx, settlement in enumerate(self.settlements):
            if not settlement.is_alive:
                continue
            # Sprint 38: frozen settlements skip their entire tick â€”
            # no decisions, production, growth, or decay.
            if settlement.frozen:
                continue
            # --- Agent decision cycle (Sprint 7) -----------------------
            agent = self.agents[idx] if idx < len(self.agents) else None
            if agent is not None and settlement.id not in skip:
                obs_now = agent.observe(self, settlement)
                self._finalize_transition(settlement, obs_now)
                action_id = agent.decide(obs_now)
                self.execute_action(settlement, action_id)
                self.action_counts[action_id] = (
                    self.action_counts.get(action_id, 0) + 1
                )
                self._pending_transitions[settlement.id] = (
                    obs_now,
                    action_id,
                    settlement.population,
                    sum(self.buildings_of(settlement).values()),
                )
            # Scarcity (any negative inventory) halves construction rate.
            if not settlement.is_in_scarcity or self.tick % 2 == 0:
                self._process_build_queue(settlement)
            self._advance_research(settlement)
            if self.tick % TREATY_PROPOSAL_CADENCE_TICKS == 0:
                from .treaties import maybe_propose_treaties

                maybe_propose_treaties(self, settlement)
            from .warfare import apply_army_upkeep

            apply_army_upkeep(settlement)
            # Sprint 42: fallout bleeds happiness from contaminated spawns.
            if settlement.frozen is False and self._settlement_contaminated(
                settlement
            ):
                from .disasters import CONTAMINATION_HAPPINESS_DECAY

                settlement.happiness = max(
                    0.0,
                    settlement.happiness - CONTAMINATION_HAPPINESS_DECAY,
                )
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
                    # Sprint 36: prolonged neglect also strips a building.
                    from .recovery import decay_building

                    decay_building(self, settlement)
                    if not settlement.is_alive:
                        self._kill(settlement)
                        continue
            else:
                settlement.negative_inventory_progress = 0
        # Highways advance one tick of construction (Sprint 33).
        from .infrastructure import advance_projects

        advance_projects(self)
        # Treaty upkeep: expiry + periodic tribute (Sprint 34).
        from .treaties import apply_tribute, expire_treaties

        expire_treaties(self)
        apply_tribute(self)
        # Field battles resolve between warring pairs (Sprint 35).
        from .warfare import resolve_battles

        resolve_battles(self)
        # Trade: transfer every tick (route establishment is agent-driven).
        for route in self.trade_routes:
            if route.active:
                self._trade_tick(route)
        # Ruins may spontaneously re-settle.
        for ruin in list(self.ruins):
            self._try_resettle_ruin(ruin)
        # Sprint 11: emergent strategy labels + evolution checkpoints.
        if self.tick % STRATEGY_LABEL_INTERVAL_TICKS == 0:
            self._update_strategy_labels()
        if self.tick > 0 and self.tick % 1000 == 0:
            self._log_strategy_evolution()

    def _update_strategy_labels(self) -> None:
        from .agents import derive_strategy_label

        for settlement in self.settlements:
            if not settlement.is_alive:
                continue
            counts = self.buildings_of(settlement)
            route_transfers = sum(
                r.transfers
                for r in self.active_routes()
                if settlement.id in (r.source_id, r.dest_id)
            )
            new_label = derive_strategy_label(
                farms=counts[BuildingType.FARM],
                granaries=counts[BuildingType.GRANARY],
                sawmills=counts[BuildingType.SAWMILL],
                mines=counts[BuildingType.MINE],
                active_routes=sum(
                    1
                    for r in self.active_routes()
                    if settlement.id in (r.source_id, r.dest_id)
                ),
                routes_established=settlement.routes_established,
                raids_committed=settlement.raids_committed,
                route_transfers=route_transfers,
                fallback_archetype=settlement.personality.get(
                    "archetype", "balanced"
                ),
            )
            if new_label != settlement.strategy_label:
                self.log_event(
                    "strategy",
                    [settlement.id],
                    f"{settlement.name} strategy shifted: "
                    f"{settlement.strategy_label} -> {new_label}",
                )
                settlement.strategy_label = new_label

    def _log_strategy_evolution(self) -> None:
        """Log the dominant strategy distribution at milestone ticks."""
        distribution = self.strategy_distribution()
        dominant = max(distribution, key=distribution.get) if distribution else "none"
        self.log_event(
            "strategy_evolution",
            [s.id for s in self.settlements if s.is_alive],
            f"tick {self.tick}: dominant strategy = {dominant} "
            f"({distribution})",
        )

    def strategy_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for s in self.settlements:
            if s.is_alive:
                dist[s.strategy_label] = dist.get(s.strategy_label, 0) + 1
        return dist

    def _finalize_transition(
        self, settlement: Settlement, next_obs: np.ndarray
    ) -> None:
        """Close out the previous tick's (obs, action) with a reward."""
        pending = self._pending_transitions.pop(settlement.id, None)
        if pending is None:
            return
        prev_obs, prev_action, prev_pop, prev_buildings = pending
        buildings_now = sum(self.buildings_of(settlement).values())
        reward = placeholder_reward(
            prev_pop,
            settlement.population,
            prev_buildings,
            buildings_now,
            starving=settlement.food_stock <= 0,
        )
        self.experience_buffer.append(
            (
                settlement.id,
                self.tick - 1,
                np.asarray(prev_obs, dtype=np.float32).tobytes(),
                int(prev_action),
                float(reward),
                np.asarray(next_obs, dtype=np.float32).tobytes(),
                False,
            )
        )
        # Sprint 37: bound RAM at long horizons; oldest rows drop first.
        if len(self.experience_buffer) > EXPERIENCE_BUFFER_MAX:
            del self.experience_buffer[
                : len(self.experience_buffer) - EXPERIENCE_BUFFER_MAX
            ]
        # Sprint 11: strategy memory â€” EMA of reward per archetype/action.
        archetype = settlement.personality.get("archetype", "balanced")
        key = (archetype, int(prev_action))
        prior = self.strategy_memory.get(key)
        self.strategy_memory[key] = (
            reward if prior is None else prior * 0.9 + reward * 0.1
        )

    def flush_experiences(self, store) -> int:
        """Write buffered experiences to SQLite and clear the buffer."""
        if not self.experience_buffer:
            return 0
        count = store.insert_agent_experiences(self.experience_buffer)
        self.experience_buffer.clear()
        return count

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
            f"bld {buildings:>3} | road {roads:>3} | route {routes} | "
            f"{settlement.strategy_label:<12} | {state}"
        )


def simulation_from_state(
    world: World,
    settlements: list[Settlement],
    trade_routes: list[TradeRoute],
    ruins: list[RuinSite],
    disaster_events: list[DisasterEvent],
    relations: RelationMatrix | None = None,
    contested: dict | None = None,
    building_debuffs: list[BuildingDebuff] | None = None,
    event_log: list[WorldEvent] | None = None,
    diplomacy: DiplomacyState | None = None,
    strategy_memory: dict | None = None,
    highway_projects: list | None = None,
    treaties: list | None = None,
    contamination_zones: list | None = None,
) -> Simulation:
    """Rebuild a Simulation from deserialized snapshot state (Sprint 6).

    Agents are stateless functions of (seed, tick), so fresh instances
    resume seamlessly without serializing internals."""
    sim = Simulation(
        world=world,
        settlements=settlements,
        trade_routes=trade_routes,
        disaster_events=disaster_events,
        ruins=ruins,
        relations=relations if relations is not None else RelationMatrix(),
        contested=contested or {},
        building_debuffs=building_debuffs or [],
        event_log=event_log or [],
        diplomacy=diplomacy if diplomacy is not None else DiplomacyState(),
        strategy_memory=strategy_memory or {},
        highway_projects=highway_projects or [],
        treaties=treaties or [],
        contamination_zones=contamination_zones or [],
    )
    sim.agents = [
        RuleBasedAgent(world.seed, idx) for idx in range(len(settlements))
    ]
    return sim
