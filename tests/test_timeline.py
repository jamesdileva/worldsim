"""Sprint 46: world event timeline + category histogram."""

import os

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.simulation import Simulation
from worldsim.timeline import (
    build_timeline,
    category_histogram,
    category_of,
    render_timeline,
)
from worldsim.visualization import CATEGORY_COLORS, export_event_histogram
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


def _seed_events(sim):
    a = sim.settlements[0].id
    b = sim.settlements[1].id
    sim.log_event("raid", [a], "raid one")          # warfare, t0
    sim.event_log[-1].tick = 10
    sim.log_event("alliance", [a, b], "allied")      # diplomacy, t0
    sim.event_log[-1].tick = 20
    sim.log_event("technology", [a], "discovered X")  # civilization, t0
    sim.event_log[-1].tick = 30
    sim.log_event("divine", [], "GOD did something")  # divine, t0
    sim.event_log[-1].tick = 40
    return sim


# ----------------------------------------------------------------------
# Category mapping
# ----------------------------------------------------------------------

def test_known_types_map_to_expected_categories():
    assert category_of("battle") == "warfare"
    assert category_of("treaty") == "diplomacy"
    assert category_of("technology") == "civilization"
    assert category_of("trade_route") == "trade"
    assert category_of("divine") == "divine"
    assert category_of("fire") == "disasters"


def test_unknown_type_falls_into_other():
    assert category_of("something_new") == "other"


# ----------------------------------------------------------------------
# Filtering
# ----------------------------------------------------------------------

def test_type_filter():
    sim = _seed_events(_sim())
    events = build_timeline(sim, types={"raid", "battle"})
    assert [e.type for e in events] == ["raid"]


def test_category_filter():
    sim = _seed_events(_sim())
    events = build_timeline(sim, categories={"diplomacy", "divine"})
    assert [e.type for e in events] == ["alliance", "divine"]


def test_actor_filter():
    sim = _seed_events(_sim())
    b_id = sim.settlements[1].id
    events = build_timeline(sim, actor_id=b_id)
    assert len(events) == 1 and events[0].type == "alliance"


def test_since_filter_and_limit_keeps_oldest_first():
    sim = _seed_events(_sim())
    events = build_timeline(sim, since_tick=15)
    assert [e.tick for e in events] == [20, 30, 40]
    limited = build_timeline(sim, limit=2)
    assert [e.tick for e in limited] == [10, 20]


def test_filters_combine():
    sim = _seed_events(_sim())
    events = build_timeline(sim, categories={"warfare"}, since_tick=5,
                            until_tick=15)
    assert len(events) == 1 and events[0].type == "raid"


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------

def test_render_includes_stamps_and_categories():
    sim = _seed_events(_sim())
    text = render_timeline(sim, build_timeline(sim, types={"raid"}))
    assert "[t10] (year" in text and "[warfare] raid" in text
    raw = render_timeline(sim, build_timeline(sim, types={"raid"}),
                          date_stamps=False)
    assert "(year" not in raw and "[t10]" in raw


def test_empty_timeline_renders_nothing():
    sim = _sim()
    assert render_timeline(sim, []) == ""


# ----------------------------------------------------------------------
# Histogram series
# ----------------------------------------------------------------------

def test_histogram_series_zero_filled_per_window():
    sim = _seed_events(_sim())  # events at t10/20/30/40 -> window 1
    windows, series = category_histogram(sim, window=100)
    assert windows == [100]
    assert series["warfare"] == [1]
    assert series["diplomacy"] == [1]
    assert series["civilization"] == [1]
    assert series["divine"] == [1]


def test_histogram_spans_multiple_windows():
    sim = _sim()
    for tick in range(1, 6):
        sim.log_event("raid", ["a"], f"r{tick}")
        sim.event_log[-1].tick = tick * 250  # windows of 500 -> 2 buckets
    windows, series = category_histogram(sim, window=500)
    assert windows == [500, 1000, 1500]  # ceil(1250/500) buckets
    assert series["warfare"] == [1, 2, 2]


# ----------------------------------------------------------------------
# Chart export
# ----------------------------------------------------------------------

def test_histogram_export_creates_file(tmp_path):
    sim = _seed_events(_sim())
    out = tmp_path / "hist.png"
    export_event_histogram(sim, out, window=100)
    assert os.path.getsize(out) > 1000


def test_histogram_export_bytes_deterministic(tmp_path):
    sim = _seed_events(_sim())
    out1, out2 = tmp_path / "a.png", tmp_path / "b.png"
    export_event_histogram(sim, out1, window=100)
    export_event_histogram(sim, out2, window=100)
    assert out1.read_bytes() == out2.read_bytes()


def test_category_colors_cover_known_categories():
    from worldsim.timeline import EVENT_CATEGORIES

    for cat in set(EVENT_CATEGORIES.values()):
        assert cat in CATEGORY_COLORS


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
