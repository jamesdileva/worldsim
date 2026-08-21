"""Agent abstraction and the rule-based baseline agent (Sprint 7).

The observation vector is the RL contract: 60 normalized floats whose layout
is frozen from day one (see docs/agent_spec.md). Features that don't exist
yet (military, research, diplomacy) occupy reserved dimensions filled with
0.0 so later sprints can wire them without changing the shape.
"""

from __future__ import annotations

import abc
import random

import numpy as np

from .actions import Action, WIRED_ACTIONS
from .buildings import BASE_FOOD_CAPACITY, BUILDING_SPECS, BuildingType
from .disasters import DisasterType
from .settlement import (
    GROWTH_INTERVAL_TICKS,
    STARVATION_INTERVAL_TICKS,
    Settlement,
)

OBSERVATION_DIM = 60

# Claim/road/trade cadence mirrors the old auto-rules (Sprints 2-4).
CLAIM_INTERVAL_TICKS = 24
ROAD_INTERVAL_TICKS = 12
TRADE_INTERVAL_TICKS = 24

# Raids (Sprint 9): aggressive personalities only, hostile relations only.
RAID_CADENCE_TICKS = 200
RAID_AGGRESSION_GATE = 0.7


def observe_vector(sim, settlement: Settlement) -> np.ndarray:
    """Build the 60-dim normalized observation for one settlement."""
    w = sim.world
    idx = sim.settlements.index(settlement)
    owned = w.ownership == idx
    obs = np.zeros(OBSERVATION_DIM, dtype=np.float32)

    def clamp01(v: float) -> float:
        return float(max(0.0, min(1.0, v)))

    # --- Vital statistics -------------------------------------------
    obs[0] = clamp01(settlement.population / 100.0)
    capacity = max(sim.food_capacity(settlement), 1.0)
    obs[1] = clamp01(settlement.food_stock / capacity)
    obs[2] = (max(-1.0, min(1.0, settlement.net_food_rate)) + 1.0) / 2.0
    inv = settlement.resource_inventory
    obs[3] = clamp01(inv.get("wood", 0.0) / 1000.0)
    obs[4] = clamp01(inv.get("stone", 0.0) / 1000.0)
    obs[5] = clamp01(inv.get("metal", 0.0) / 1000.0)

    # --- Territory & buildings ---------------------------------------
    obs[6] = clamp01(len(sim.territory_of(settlement)) / 1000.0)
    counts = sim.buildings_of(settlement)
    obs[7] = clamp01(counts[BuildingType.FARM] / 50.0)
    obs[8] = clamp01(counts[BuildingType.SAWMILL] / 50.0)
    obs[9] = clamp01(counts[BuildingType.MINE] / 50.0)
    obs[10] = clamp01(counts[BuildingType.GRANARY] / 50.0)
    obs[11] = clamp01(len(sim.roads_of(settlement)) / 200.0)

    # --- Stability ----------------------------------------------------
    obs[12] = clamp01(settlement.happiness)
    obs[13] = 1.0 if settlement.is_in_scarcity else 0.0
    obs[14] = clamp01(settlement.growth_progress / GROWTH_INTERVAL_TICKS)
    obs[15] = clamp01(settlement.starvation_progress / STARVATION_INTERVAL_TICKS)
    obs[16] = clamp01((w.tick - settlement.created_at_tick) / 2000.0)
    obs[17] = 1.0 if settlement.ruin_origin is not None else 0.0
    obs[18] = clamp01(
        sum(
            1
            for r in sim.active_routes()
            if settlement.id in (r.source_id, r.dest_id)
        )
        / 10.0
    )

    # --- World context --------------------------------------------------
    from .clock import TICKS_PER_SEASON

    obs[19] = (w.tick // TICKS_PER_SEASON) % 4 / 3.0

    # Terrain composition of owned tiles.
    total_owned = max(int(owned.sum()), 1)
    from .tiles import TerrainType

    for i, tt in enumerate(TerrainType):
        share = int((w.terrain[owned] == tt.value).sum()) / total_owned
        obs[20 + i] = clamp01(share)

    obs[26] = clamp01(len(settlement.build_queue) / 10.0)
    obs[27] = clamp01(
        settlement.negative_inventory_progress / 48.0
    )
    obs[28] = clamp01(settlement.low_happiness_progress / 100.0)
    obs[29] = clamp01(settlement.negative_food_streak / 50.0)

    # Active disasters affecting this settlement.
    droughts = sum(
        1
        for e in sim.disaster_events
        if e.type == DisasterType.DROUGHT
        and e.is_active(w.tick)
        and sim._settlement_affected(settlement, e)
    )
    obs[30] = clamp01(droughts / 3.0)
    obs[31] = 1.0 if sim._ruin_adjacent(settlement) else 0.0

    # 32-37: reserved military.
    # Sprint 9 diplomacy dims:
    hostile_neighbors = sum(
        1
        for n in sim.neighbors_of(settlement)
        if sim.relations.is_hostile(settlement.id, n.id)
    )
    friendly_neighbors = sum(
        1
        for n in sim.neighbors_of(settlement)
        if sim.relations.is_friendly(settlement.id, n.id)
    )
    obs[42] = clamp01(hostile_neighbors / 5.0)
    obs[43] = clamp01(friendly_neighbors / 5.0)
    obs[44] = clamp01(len(sim.contested) / 500.0)
    # 45-47: reserved research/diplomacy detail.

    # --- World-level aggregates ---------------------------------------
    obs[48] = clamp01(sum(1 for s in sim.settlements if s.is_alive) / 20.0)
    obs[49] = clamp01(len(sim.ruins) / 20.0)
    obs[50] = clamp01(len(sim.active_disasters()) / 5.0)
    from .clock import year_of, TICKS_PER_YEAR

    obs[51] = clamp01(year_of(w.tick) / 100.0)
    obs[52] = clamp01((w.tick % TICKS_PER_YEAR) / TICKS_PER_YEAR)

    # 53-59: reserved meta dimensions.
    return obs


class Agent(abc.ABC):
    """Swappable decision-maker for one settlement.

    Rule-based today; an RL policy wraps this same interface from Sprint 12."""

    @abc.abstractmethod
    def observe(self, sim, settlement: Settlement) -> np.ndarray:
        """Return the normalized observation vector."""

    @abc.abstractmethod
    def decide(self, obs: np.ndarray) -> int:
        """Return an action ID given an observation."""


class RuleBasedAgent(Agent):
    """Urgency-ordered rule-based baseline (Sprint 8).

    Decision priority: famine > food security > expansion/infrastructure/
    trade cadences > resource income > farm growth > idle.

    Personality vectors (per settlement, Sprint 8) bias thresholds:
    expansionism speeds claiming, industry favors sawmills/mines and keeps
    deeper stockpiles, commerce speeds trade route establishment.

    Near-stateless: rolls are keyed by (seed, tick) and the cadence counter
    syncs from the world clock on every observe(), so saved-and-resumed
    simulations continue identically without serializing agent internals."""

    EPSILON = 0.10  # spec: 10% chance of a random action

    def __init__(self, seed: int, settlement_index: int) -> None:
        self.seed = seed
        self.index = settlement_index
        self.call_count = 0
        self._tick: int | None = None
        self._personality: dict[str, float] = {}
        self.last_action: int = int(Action.IDLE)

    def observe(self, sim, settlement: Settlement) -> np.ndarray:
        # Sync cadence counter from the world clock (resume-safe).
        self.call_count = sim.tick - settlement.created_at_tick
        self._tick = sim.tick
        self._personality = dict(settlement.personality) or {
            "expansionism": 0.5,
            "industry": 0.5,
            "commerce": 0.5,
            "aggression": 0.5,
        }
        return observe_vector(sim, settlement)

    def decide(self, obs: np.ndarray) -> int:
        self.call_count += 1
        action = self._policy(obs)
        self.last_action = action
        return action

    def _epsilon_action(self, tick: int) -> int:
        """10% uniform over wired actions (random no-ops teach nothing)."""
        rng = random.Random(
            (self.seed ^ 0xC0DE) + tick * 7919 + self.index * 131
        )
        if rng.random() >= self.EPSILON:
            return -1
        wired = sorted(int(a) for a in WIRED_ACTIONS)
        return wired[rng.randrange(len(wired))]

    def _policy(self, obs: np.ndarray) -> int:
        tick = self._tick if self._tick is not None else self.call_count
        eps = self._epsilon_action(tick)
        if eps != -1:
            return eps

        p = self._personality
        expansionism = p.get("expansionism", 0.5)
        industry = p.get("industry", 0.5)
        commerce = p.get("commerce", 0.5)

        # Personality-biased cadences.
        claim_interval = max(8, int(CLAIM_INTERVAL_TICKS / (0.5 + expansionism)))
        road_interval = max(6, int(ROAD_INTERVAL_TICKS / (0.5 + industry)))
        trade_interval = max(8, int(TRADE_INTERVAL_TICKS / (0.5 + commerce)))
        # Industrial settlements keep deeper stockpiles before switching to
        # income buildings; commercial ones push trade earlier.
        stockpile_floor = 0.05 + industry * 0.10

        food_level = float(obs[1])
        net_food = float(obs[2]) * 2.0 - 1.0  # back to [-1, 1]
        wood = float(obs[3])
        stone = float(obs[4])
        farms = float(obs[7])
        granaries = float(obs[10])

        # Affordability gates (mirrors the old auto-rule can_afford checks).
        # Normalization: inventory / 1000, so 0.005 == 5 units; building
        # counts / 50, so 0.4 == 20 buildings.
        can_farm = wood >= 0.005 and stone >= 0.003
        can_sawmill = wood >= 0.004 and stone >= 0.002
        can_mine = wood >= 0.006 and stone >= 0.004
        can_granary = wood >= 0.005 and stone >= 0.005
        # float32 obs values need tolerant comparisons (0.02 stored as
        # 0.01999... would fail an exact >= check).
        has_farm = farms >= 0.02 - 1e-6  # at least one farm

        # --- Urgency 1: famine response ---------------------------------
        if food_level < 0.2 or net_food < -0.05:
            if farms < 0.8 and can_farm:
                return int(Action.BUILD_FARM)
            if can_mine:
                return int(Action.BUILD_MINE)
            if self.call_count % claim_interval == 0:
                return int(Action.CLAIM_TERRITORY)
            return int(Action.WAIT)

        # --- Urgency 2: food security ------------------------------------
        if food_level > 0.9 and granaries < 0.4 and can_granary:
            return int(Action.BUILD_GRANARY)

        # --- Urgency 3: expansion / infrastructure / trade cadences ------
        if self.call_count % claim_interval == 0:
            return int(Action.CLAIM_TERRITORY)
        if self.call_count % road_interval == 4:
            return int(Action.EXPAND_ROAD_NETWORK)
        if self.call_count % trade_interval == 10:
            return int(Action.ESTABLISH_TRADE_ROUTE)

        # --- Urgency 3.5: raids (aggressive, at war, on cadence) ----------
        aggression = p.get("aggression", 0.5)
        hostile_neighbors = float(obs[42])
        if (
            aggression > RAID_AGGRESSION_GATE
            and hostile_neighbors > 0.0
            and self.call_count % RAID_CADENCE_TICKS == 15
        ):
            return int(Action.INITIATE_RAID)

        # --- Urgency 4: resource income (sub-cadence gated) --------------
        if wood < stockpile_floor and has_farm and can_sawmill and self.call_count % 8 == 2:
            return int(Action.BUILD_SAWMILL)
        if stone < stockpile_floor and has_farm and can_mine and self.call_count % 8 == 6:
            return int(Action.BUILD_MINE)

        # --- Urgency 5: steady-state farm growth -------------------------
        if farms < 0.6 and can_farm:
            farm_rng = random.Random(
                (self.seed ^ 0xB7E2) + tick * 104729 + self.index * 17
            )
            if farm_rng.random() < 0.3 * (1.25 - expansionism):
                return int(Action.BUILD_FARM)
        return int(Action.WAIT)



def placeholder_reward(
    before_pop: int,
    after_pop: int,
    before_buildings: int,
    after_buildings: int,
    starving: bool,
) -> float:
    """Simple shaped reward until the formal system lands (Sprint 13)."""
    reward = 0.0
    reward += 0.1 * (after_pop - before_pop)
    reward += 0.05 * (after_buildings - before_buildings)
    if starving:
        reward -= 0.1
    reward += 0.001  # survival bonus
    return max(-1.0, min(1.0, reward))
