"""Discrete action space: 60 settlement-level actions (Sprint 7).

Category layout follows docs/architecture_detailed.md §5.2. This enum is the
RL contract — IDs are never renumbered, only appended or wired to handlers.
Actions without a handler yet (military, research, diplomacy...) execute as
validated no-ops until their mechanics arrive in later sprints.
"""

from __future__ import annotations

import enum


class Action(enum.IntEnum):
    # Production (10): 0-9
    BUILD_FARM = 0
    BUILD_SAWMILL = 1
    BUILD_MINE = 2
    BUILD_GRANARY = 3
    UPGRADE_BUILDING = 4
    REPAIR_STRUCTURE = 5
    BUILD_SECOND_FARM = 6
    BUILD_SECOND_SAWMILL = 7
    BUILD_SECOND_MINE = 8
    DEMOLISH_BUILDING = 9

    # Infrastructure (10): 10-19
    BUILD_ROAD = 10
    EXPAND_ROAD_NETWORK = 11
    CONNECT_TERRITORY = 12
    REPAIR_ROADS = 13
    BUILD_ROAD_EAST = 14
    BUILD_ROAD_WEST = 15
    BUILD_ROAD_NORTH = 16
    BUILD_ROAD_SOUTH = 17
    SURVEY_TERRITORY = 18
    IDLE_INFRASTRUCTURE = 19

    # Expansion (10): 20-29
    CLAIM_TERRITORY = 20
    FOUND_NEW_SETTLEMENT = 21
    SCOUT_NEARBY = 22
    CLAIM_AGGRESSIVE = 23
    CLAIM_DEFENSIVE = 24
    CONSOLIDATE_TERRITORY = 25
    ABANDON_OUTLYING_TILE = 26
    PREPARE_EXPANSION = 27
    MAP_REGION = 28
    IDLE_EXPANSION = 29

    # Economy (8): 30-37
    ESTABLISH_TRADE_ROUTE = 30
    REQUEST_RESOURCE_TRADE = 31
    STORE_SURPLUS = 32
    SELL_RESOURCES = 33
    BUY_RESOURCES = 34
    BALANCE_BUDGET = 35
    HEDGE_SCARCITY = 36
    IDLE_ECONOMY = 37

    # Military (6): 38-43 — unwired until Sprint 9+
    TRAIN_DEFENDER = 38
    TRAIN_RAIDER = 39
    FORTIFY_BORDER = 40
    INITIATE_RAID = 41
    DISBAND_MILITARY = 42
    IDLE_MILITARY = 43

    # Research (4): 44-47 — unwired until Phase 3+
    RESEARCH_TECHNOLOGY = 44
    PRIORITIZE_INNOVATION = 45
    SHARE_KNOWLEDGE = 46
    IDLE_RESEARCH = 47

    # Social (6): 48-53
    BOOST_MORALE = 48
    REALLOCATE_WORKERS = 49
    OPTIMIZE_LAYOUT = 50
    HOST_FESTIVAL = 51
    RATION_FOOD = 52
    IDLE_SOCIAL = 53

    # Meta (6): 54-59
    RE_EVALUATE_STRATEGY = 54
    SAVE_STATE = 55
    CHECK_NEIGHBORS = 56
    EMERGENCY_RESPONSE = 57
    WAIT = 58
    IDLE = 59


NUM_ACTIONS = len(Action)

# Actions with real handlers in the current simulation.
WIRED_ACTIONS: dict[Action, str] = {
    Action.BUILD_FARM: "build_farm",
    Action.BUILD_SAWMILL: "build_sawmill",
    Action.BUILD_MINE: "build_mine",
    Action.BUILD_GRANARY: "build_granary",
    Action.BUILD_ROAD: "build_road",
    Action.EXPAND_ROAD_NETWORK: "expand_road_network",
    Action.CLAIM_TERRITORY: "claim_territory",
    Action.ESTABLISH_TRADE_ROUTE: "establish_trade_route",
    Action.BOOST_MORALE: "boost_morale",
    Action.WAIT: "wait",
    Action.IDLE: "idle",
}

# Category prefixes for stats/reporting.
def action_category(action: Action) -> str:
    value = int(action)
    if value < 10:
        return "production"
    if value < 20:
        return "infrastructure"
    if value < 30:
        return "expansion"
    if value < 38:
        return "economy"
    if value < 44:
        return "military"
    if value < 48:
        return "research"
    if value < 54:
        return "social"
    return "meta"
