"""Sprint 26: deterministic summary formatting tests.

Format pins use a hand-built stub so exact strings are stable regardless of
world dynamics; determinism/budget tests run against real simulations.
"""

import pytest

from worldsim.buildings import BUILDING_SPECS, BuildingType
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.summaries import (
    TIER_FULL,
    TIER_TINY,
    estimate_tokens,
    summarize_settlement,
    summarize_world,
)
from worldsim.world import World


# ----------------------------------------------------------------------
# Stubs
# ----------------------------------------------------------------------

class StubRelations:
    def score(self, a, b):
        return 10.0 if {a, b} == {"s1", "s2"} else 0.0

    def label(self, a, b):
        return "neutral"

    def is_hostile(self, a, b):
        return False


class StubDiplomacy:
    alliances = set()

    def rep(self, sid):
        return -7.5

    def at_war(self, a, b):
        return {a, b} == {"s1", "s3"}

    def is_allied(self, a, b):
        return {a, b} == {"s1", "s2"}

    def wars_of(self, sid):
        return [frozenset({"s1", "s3"})] if sid == "s1" else []


class StubWorld:
    seed = 1234
    size = 64
    tick = 500


class StubSim:
    """Minimal duck-typed sim covering everything summaries touches."""

    def __init__(self):
        self.world = StubWorld()
        self.relations = StubRelations()
        self.diplomacy = StubDiplomacy()
        self.event_log = []
        self.settlements = []
        self.highway_projects = []
        self.treaties = []
        self._buildings = {}
        self._territory = {}
        self._neighbors = {}

    tick = 500

    def buildings_of(self, s):
        return self._buildings.get(s.id, {})

    def territory_of(self, s):
        return self._territory.get(s.id, [])

    def roads_of(self, s):
        return set()

    def neighbors_of(self, s):
        return self._neighbors.get(s.id, [])

    def active_routes(self):
        return []

    def active_disasters(self):
        return []


def _make_settlement(sid="s1", name="Alpha", **overrides) -> Settlement:
    s = Settlement(
        name=name,
        spawn_x=10,
        spawn_y=10,
        id=sid,
        personality={"archetype": "agricultural", "expansionism": 0.5},
        strategy_label="farming",
        population=42,
        food_stock=120.5,
        happiness=0.62,
        net_food_rate=-2.25,
        resource_inventory={"wood": 30.0, "stone": 15.0},
        build_queue=["Farm"],
        raids_committed=0,
        routes_established=0,
    )
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _stub_with_settlements(*settlements) -> StubSim:
    sim = StubSim()
    sim.settlements = list(settlements)
    by_id = {s.id: s for s in settlements}
    for s in settlements:
        sim._neighbors[s.id] = [
            o for o in settlements if o.id != s.id and o.id in by_id
        ]
    sim._territory["s1"] = [(0, 0), (1, 0), (0, 1)]
    sim._buildings["s1"] = {
        BuildingType.FARM: 2,
        BuildingType.MINE: 1,
    }
    return sim


# ----------------------------------------------------------------------
# Format pins (exact strings)
# ----------------------------------------------------------------------

def test_tiny_oneliner_exact_format():
    alpha = _make_settlement()
    beta = _make_settlement("s2", "Beta")
    gamma = _make_settlement("s3", "Gamma")
    sim = _stub_with_settlements(alpha, beta, gamma)
    line = summarize_settlement(sim, alpha, tier=TIER_TINY)
    assert line == (
        "Alpha[agricultural|farming|era1], pop=42, food=120, net=-2.2, "
        "happy=0.62, terr=3, bld(F/S/M/G)=2/0/1/0, "
        "mil(army/fort/siege)=0/0/0, "
        "allies=Beta, WAR=Gamma"
    )


def test_world_header_exact_format():
    sim = _stub_with_settlements(_make_settlement())
    first_line = summarize_world(sim, tier=TIER_TINY).splitlines()[0]
    assert first_line == (
        "World seed=1234 size=64 tick=500 (year 0, winter (tick 500)) | "
        "settlements: 1 alive / 1 total"
    )


def test_full_summary_sections_present_in_order():
    sim = _stub_with_settlements(_make_settlement(), _make_settlement(
        "s2", "Beta"), _make_settlement("s3", "Gamma"))
    text = summarize_settlement(sim, _make_settlement(), tier=TIER_FULL)
    lines = text.splitlines()
    assert lines[0] == "Settlement Alpha [agricultural]"
    assert lines[1] == (
        "  population=42 food=120.5 net_food=-2.2 /tick happiness=0.62"
    )
    assert lines[2] == "  strategy=farming reputation=-8"
    assert lines[3] == "  military: army=0.0 fort=0 siege_progress=0"
    assert lines[4] == "  era=1 research=0 technologies: none"
    assert lines[5] == "  resources: stone=15.0, wood=30.0"
    assert lines[6] == "  buildings: farm=2, mine=1"
    assert lines[7] == "  territory=3 tiles, roads=0"
    assert lines[8] == "  build_queue: Farm"
    assert lines[9] == (
        "  relations: Beta(allied, +10), Gamma(AT WAR, +0)"
    )
    assert lines[10] == "  recent events: none"


def test_recent_events_chronological_capped():
    sim = _stub_with_settlements(_make_settlement())

    class Event:
        def __init__(self, tick, type_, actor_ids, description):
            self.tick = tick
            self.type = type_
            self.actor_ids = actor_ids
            self.description = description

    sim.event_log = [
        Event(100, "raid", ["s1"], "Alpha raided"),
        Event(200, "disaster", ["other"], "unrelated"),
        Event(300, "trade_route", ["s1"], "trade A"),
        Event(400, "peace", ["s1", "s2"], "peace B"),
        Event(500, "alliance", ["s1"], "alliance C"),
        Event(600, "raid", ["s1"], "raid D"),
    ]
    events = summarize_settlement(sim, _make_settlement(), tier=TIER_FULL,
                                  max_events=4).splitlines()[11:]
    assert events == [
        "    [t300] trade_route: trade A",
        "    [t400] peace: peace B",
        "    [t500] alliance: alliance C",
        "    [t600] raid: raid D",
    ]


def test_war_lines_sorted_deduplicated():
    from worldsim.summaries import _war_lines
    sim = _stub_with_settlements(_make_settlement(), _make_settlement(
        "s2", "Beta"), _make_settlement("s3", "Gamma"))
    # wars_of returns the pair once per endpoint query; dedupe must hold.
    lines = _war_lines(sim)
    assert lines == ["Alpha vs Gamma"]


# ----------------------------------------------------------------------
# Placeholders / graceful handling
# ----------------------------------------------------------------------

def test_dead_settlement_renders_dead_line():
    sim = _stub_with_settlements()
    dead = _make_settlement(population=0, destroyed_at_tick=333)
    text = summarize_settlement(sim, dead, tier=TIER_FULL)
    assert text == "Alpha: DEAD (population 0, died tick 333)"


def test_dead_settlement_unknown_death_tick():
    sim = _stub_with_settlements()
    dead = _make_settlement(population=0, destroyed_at_tick=None)
    assert summarize_settlement(sim, dead).endswith("died tick unknown)")


def test_empty_personality_unknown_archetype():
    sim = _stub_with_settlements()
    s = _make_settlement(personality={})
    line = summarize_settlement(sim, s, tier=TIER_TINY)
    assert line.startswith("Alpha[unknown|farming|era1]")


def test_none_numeric_fields_render_placeholder():
    sim = _stub_with_settlements()
    s = _make_settlement(food_stock=None, happiness=None,
                         net_food_rate=None)
    line = summarize_settlement(sim, s, tier=TIER_TINY)
    assert "food=unknown" in line
    assert "happy=unknown" in line
    assert "net=unknown" in line


def test_empty_build_queue_and_resources():
    sim = _stub_with_settlements()
    s = _make_settlement(build_queue=[], resource_inventory={})
    text = summarize_settlement(sim, s, tier=TIER_FULL)
    assert "  build_queue: empty" in text
    assert "  resources: none" in text


# ----------------------------------------------------------------------
# Determinism + budget (real sims)
#----------------------------------------------------------------------

@pytest.fixture
def stepped_sim():
    world = World(seed=42, size=64)
    sim = Simulation(world=world)
    sim.spawn_settlements(3)
    for _ in range(60):
        sim.step()
    return sim


def test_byte_identical_within_same_sim(stepped_sim):
    a = summarize_world(stepped_sim, tier=TIER_FULL)
    b = summarize_world(stepped_sim, tier=TIER_FULL)
    assert a == b


def test_byte_identical_across_identical_sims():
    texts = []
    for _ in range(2):
        world = World(seed=42, size=64)
        sim = Simulation(world=world)
        sim.spawn_settlements(3)
        for _ in range(60):
            sim.step()
        texts.append(summarize_world(sim, tier=TIER_FULL))
    assert texts[0] == texts[1]


def test_tiny_world_summary_under_token_budget(stepped_sim):
    text = summarize_world(stepped_sim, tier=TIER_TINY)
    assert estimate_tokens(text) < 200 * len(
        [s for s in stepped_sim.settlements if s.is_alive])


def test_tiny_oneliner_under_200_tokens(stepped_sim):
    for s in stepped_sim.settlements:
        if not s.is_alive:
            continue
        line = summarize_settlement(stepped_sim, s, tier=TIER_TINY)
        assert estimate_tokens(line) <= 200


def test_building_names_match_specs():
    names = {BUILDING_SPECS[bt].name.lower() for bt in BuildingType}
    assert names == {"farm", "sawmill", "mine", "granary"}
