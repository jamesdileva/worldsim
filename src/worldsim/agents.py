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
# Peace offers (Sprint 10): long wars wear down even aggressive settlements.
WAR_WEARINESS_TICKS = 1000


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

    # 32: neighbor count (military dimension — wired Sprint 11 so military
    # archetypes can detect raid targets among neutral neighbors).
    obs[32] = clamp01(len(sim.neighbors_of(settlement)) / 5.0)
    # 33-37: reserved military detail.
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
    # Sprint 10 diplomacy detail:
    at_war = sum(1 for key in sim.diplomacy.wars_of(settlement.id))
    incoming_offer = any(
        sim.diplomacy.has_live_offer(enemy_id, settlement.id, w.tick)
        for enemy_id in {
            next(pid for pid in key if pid != settlement.id)
            for key in sim.diplomacy.wars_of(settlement.id)
        }
    )
    obs[45] = 1.0 if at_war else 0.0
    obs[46] = 1.0 if incoming_offer else 0.0
    obs[47] = clamp01(
        (sim.diplomacy.rep(settlement.id) + 100.0) / 200.0
    )

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

    # Exploration pool excludes BUILD_* actions: random construction ignores
    # affordability/caps and drowns archetype specialization in noise.
    _EXPLORATION_ACTIONS = sorted(
        int(a)
        for a in WIRED_ACTIONS
        if a
        not in (
            Action.BUILD_FARM,
            Action.BUILD_SAWMILL,
            Action.BUILD_MINE,
            Action.BUILD_GRANARY,
        )
    )

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
            "archetype": "balanced",
        }
        return observe_vector(sim, settlement)

    def decide(self, obs: np.ndarray) -> int:
        self.call_count += 1
        action = self._policy(obs)
        self.last_action = action
        return action

    def _epsilon_action(self, tick: int) -> int:
        """10% uniform over wired non-construction actions."""
        rng = random.Random(
            (self.seed ^ 0xC0DE) + tick * 7919 + self.index * 131
        )
        if rng.random() >= self.EPSILON:
            return -1
        pool = self._EXPLORATION_ACTIONS
        return pool[rng.randrange(len(pool))]

    def _policy(self, obs: np.ndarray) -> int:
        tick = self._tick if self._tick is not None else self.call_count
        eps = self._epsilon_action(tick)
        if eps != -1:
            return eps

        p = self._personality
        expansionism = p.get("expansionism", 0.5)
        industry = p.get("industry", 0.5)
        commerce = p.get("commerce", 0.5)

        # Sprint 11: archetype biases — thresholds shift, nothing is forced.
        archetype = p.get("archetype", "balanced")
        # Farm ceilings (normalized /50): specialization means non-farm
        # archetypes stop spamming farms once basic food security is met.
        farm_caps = {
            "agricultural": 0.8,
            "balanced": 0.5,
            "mining": 0.15,
            "trading": 0.24,
            "military": 0.2,
        }
        farms_cap = farm_caps.get(archetype, 0.5)
        # Granary ceilings (normalized /50): oversized storage keeps
        # food_level permanently low, which deadlocks the policy in famine
        # mode. Agricultural settlements store more.
        granary_caps = {
            "agricultural": 0.12,
            "balanced": 0.08,
            "mining": 0.04,
            "trading": 0.04,
            "military": 0.04,
        }
        granaries_cap = granary_caps.get(archetype, 0.08)
        if archetype == "trading":
            trade_interval = max(4, int(TRADE_INTERVAL_TICKS / 2))
        else:
            trade_interval = max(8, int(TRADE_INTERVAL_TICKS / (0.5 + commerce)))
        claim_interval = max(8, int(CLAIM_INTERVAL_TICKS / (0.5 + expansionism)))
        road_interval = max(6, int(ROAD_INTERVAL_TICKS / (0.5 + industry)))
        # Industrial (mining) archetypes build income buildings at higher
        # stockpile levels; agricultural ones keep a deeper food buffer.
        stockpile_floor = 0.05 + industry * 0.10
        farm_growth_chance = 0.3 * (1.25 - expansionism)
        famine_food_level = 0.2
        granary_food_level = 0.9
        raid_gate = RAID_AGGRESSION_GATE
        if archetype == "mining":
            stockpile_floor += 0.10
            road_interval = max(6, int(road_interval * 0.7))
        elif archetype == "agricultural":
            famine_food_level = 0.3
            farm_growth_chance *= 2.0
            granary_food_level = 0.85
        elif archetype == "military":
            raid_gate = min(raid_gate, 0.5)
        elif archetype == "balanced":
            pass

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
        if food_level < famine_food_level or net_food < -0.05:
            if farms < farms_cap and can_farm:
                return int(Action.BUILD_FARM)
            if can_mine:
                return int(Action.BUILD_MINE)
            if self.call_count % claim_interval == 0:
                return int(Action.CLAIM_TERRITORY)
            return int(Action.WAIT)

        # --- Urgency 2: food security ------------------------------------
        if (
            food_level > granary_food_level
            and granaries < granaries_cap
            and can_granary
        ):
            return int(Action.BUILD_GRANARY)
            return int(Action.BUILD_GRANARY)

        # --- Urgency 3: expansion / infrastructure / trade cadences ------
        if self.call_count % claim_interval == 0:
            return int(Action.CLAIM_TERRITORY)
        if self.call_count % road_interval == 4:
            return int(Action.EXPAND_ROAD_NETWORK)
        if self.call_count % trade_interval == 10:
            return int(Action.ESTABLISH_TRADE_ROUTE)

        # --- Urgency 3.5: raids ------------------------------------------
        # Hostile settlements raid hostile neighbors; military archetypes
        # also START conflicts against neutral neighbors (aggression creates
        # hostility, not the other way around).
        aggression = p.get("aggression", 0.5)
        if archetype == "military":
            aggression = max(aggression, RAID_AGGRESSION_GATE)
        hostile_neighbors = float(obs[42])
        neighbor_count = float(obs[32])
        warlike = hostile_neighbors > 0.0 or (
            archetype == "military" and neighbor_count > 0.0
        )
        if (
            aggression > raid_gate
            and warlike
            and self.call_count % RAID_CADENCE_TICKS == 15
        ):
            return int(Action.INITIATE_RAID)

        # --- Urgency 3.6: peace diplomacy (Sprint 10) ----------------------
        # Accept incoming offers unless highly aggressive; offer peace when
        # weary of war or naturally peaceful.
        at_war = float(obs[45]) > 0.0
        incoming_offer = float(obs[46]) > 0.0
        peace_gate = 0.7 if archetype != "military" else 0.9
        weariness = (
            WAR_WEARINESS_TICKS // 2 if archetype == "military"
            else WAR_WEARINESS_TICKS
        )
        if at_war and incoming_offer and aggression < peace_gate:
            if self.call_count % 100 == 3:
                return int(Action.ACCEPT_PEACE)
        if at_war and aggression < 0.4:
            if self.call_count % 100 == 33:
                return int(Action.OFFER_PEACE)
        elif at_war and self.call_count % weariness == 55:
            return int(Action.OFFER_PEACE)

        # --- Urgency 4: resource income (sub-cadence gated) --------------
        # Mining archetypes attempt income buildings twice as often and
        # regardless of current stock, with a much higher ceiling — industry
        # is their identity. Others stop at a modest income sector.
        mining_archetype = archetype == "mining"
        income_count = float(obs[8]) + float(obs[9])
        income_cap = 1.0 if mining_archetype else 0.3
        if has_farm and can_sawmill and income_count < income_cap and (
            (mining_archetype and self.call_count % 4 == 2)
            or (wood < stockpile_floor and self.call_count % 8 == 2)
        ):
            return int(Action.BUILD_SAWMILL)
        if has_farm and can_mine and income_count < income_cap and (
            (mining_archetype and self.call_count % 4 == 0)
            or (stone < stockpile_floor and self.call_count % 8 == 6)
        ):
            return int(Action.BUILD_MINE)

        # --- Urgency 5: steady-state farm growth -------------------------
        if farms < farms_cap and can_farm:
            farm_rng = random.Random(
                (self.seed ^ 0xB7E2) + tick * 104729 + self.index * 17
            )
            if farm_rng.random() < farm_growth_chance:
                return int(Action.BUILD_FARM)
        return int(Action.WAIT)


def derive_strategy_label(
    farms: int,
    granaries: int,
    sawmills: int,
    mines: int,
    active_routes: int,
    routes_established: int,
    raids_committed: int,
    route_transfers: int = 0,
    fallback_archetype: str = "balanced",
) -> str:
    """Classify a settlement's emergent strategy from its building mix and
    behavior (Sprint 11).

    Each strategy is scored on its OWN natural scale (normalized 0..1):
    building shares for agri/mining, route initiative for trading, raid
    campaigns for military. Whoever is most fully 'expressed' wins; weak
    signals across the board fall back to the settlement's archetype —
    behavior hasn't differentiated yet, so identity defaults to intent."""
    total_buildings = farms + granaries + sawmills + mines
    magnitude = min(total_buildings, 10.0) / 10.0
    agri_share = (
        ((farms + granaries) / total_buildings) if total_buildings else 0.0
    )
    mining_share = (
        ((sawmills + mines) / total_buildings) if total_buildings else 0.0
    )
    scores = {
        "agricultural": agri_share * magnitude,
        "mining": mining_share * magnitude,
        "trading": (
            min(routes_established / 3.0, 1.0) * 0.7
            + min(active_routes / 4.0, 1.0) * 0.3
        ),
        "military": min(raids_committed / 6.0, 1.0),
    }
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best_label, best = ranked[0]
    second = ranked[1][1]
    # Expression thresholds per strategy: farming is everyone's baseline, so
    # 'agricultural' must dominate outright; trading/military need meaningful
    # campaigns. Below those bars, identity defaults to archetype intent.
    thresholds = {
        # Farming is everyone's baseline: ~90% of a typical mix is farms and
        # granaries, so 'agricultural' demands near-total dominance.
        "agricultural": 0.95,
        "mining": 0.75,
        "trading": 0.60,
        "military": 0.60,
    }
    if best < thresholds.get(best_label, 0.60):
        return fallback_archetype
    if second > best - 0.05:
        return fallback_archetype  # genuine near-tie across strategies
    return best_label



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
