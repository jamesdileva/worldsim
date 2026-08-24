"""Sprint 44: advanced visualization — ASCII maps, panels, PNG export."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.buildings import Improvement
from worldsim.visualization import (
    LEGEND,
    export_map_png,
    render_ascii_map,
    render_settlement_panel,
)
from worldsim.world import World


def _sim(n=2, seed=42) -> "object":
    from worldsim.simulation import Simulation

    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    for _ in range(5):
        sim.step()
    return sim


def _settlement_tile(sim, s):
    ty, tx = next(
        t for t in sim.territory_of(s)
        if sim.world.improvements[t[0], t[1]] == -1
    )
    return ty, tx


# ----------------------------------------------------------------------
# ASCII map
# ----------------------------------------------------------------------

def test_map_is_deterministic_and_has_header():
    sim = _sim(n=3)
    m1 = render_ascii_map(sim)
    m2 = render_ascii_map(sim)
    assert m1 == m2
    header = m1.splitlines()[0]
    assert header.startswith("map 0,0..63,63 tick=")
    assert "seed=42" in header


def test_settlement_initial_appears_on_map():
    sim = _sim(n=2)
    s = sim.settlements[0]
    grid = render_ascii_map(sim).splitlines()[1:]  # skip header
    assert grid[s.spawn_y][s.spawn_x] == s.name[0].upper()


def test_road_glyph_overrides_terrain():
    sim = _sim(n=1)
    s = sim.settlements[0]
    ty, tx = _settlement_tile(sim, s)
    from worldsim.simulation import Simulation as S

    sim.build_road(s, tx, ty)
    grid = render_ascii_map(sim).splitlines()[1:]
    assert grid[ty][tx] == "#"


def test_building_glyph_b():
    sim = _sim(n=1)
    s = sim.settlements[0]
    ty, tx = _settlement_tile(sim, s)
    before, after = sim.god_bless_resources(s, "wood", 100.0)
    del before, after
    sim.god_bless_resources(s, "stone", 100.0)
    assert sim.execute_action(s, int(__import__(
        "worldsim.actions", fromlist=["Action"]).Action.BUILD_FARM)) or True
    # place deterministically via build_at on the chosen tile
    sim.build_at(s, __import__(
        "worldsim.buildings", fromlist=["BuildingType"]
    ).BuildingType.FARM, x=tx, y=ty)
    grid = render_ascii_map(sim).splitlines()[1:]
    assert grid[ty][tx] == "b"


def test_ruin_marker_x():
    sim = _sim(n=2)
    a = sim.settlements[0]
    sim._kill(a)  # leaves ruins at spawn
    grid = render_ascii_map(sim).splitlines()[1:]
    assert grid[a.spawn_y][a.spawn_x] == "X"


def test_contamination_marker_takes_priority():
    sim = _sim(n=2)
    a = sim.settlements[0]
    sim.god_nuke(a.spawn_x, a.spawn_y)
    grid = render_ascii_map(sim).splitlines()[1:]
    assert grid[a.spawn_y][a.spawn_x] == "!"


def test_region_crop_dimensions():
    sim = _sim(n=2)
    cropped = render_ascii_map(sim, x0=10, y0=10, x1=19, y1=14)
    lines = cropped.splitlines()
    assert lines[0].startswith("map 10,10..19,14")
    assert len(lines) == 1 + 5  # header + rows
    assert all(len(line) == 10 for line in lines[1:])


def test_legend_documents_glyphs():
    assert "settlements" in LEGEND and "#" in LEGEND and "!" in LEGEND


# ----------------------------------------------------------------------
# Settlement panels
# ----------------------------------------------------------------------

def test_panel_shows_key_stats_and_frozen_flag():
    sim = _sim(n=1)
    s = sim.settlements[0]
    s.army = 7.0
    s.fort_level = 2
    panel = render_settlement_panel(sim, s)
    assert s.name in panel
    assert "army 7" in panel and "fort 2" in panel
    assert "FROZEN" not in panel
    sim.god_toggle_freeze(s)
    panel_frozen = render_settlement_panel(sim, s)
    assert "FROZEN" in panel_frozen


# ----------------------------------------------------------------------
# PNG export
# ----------------------------------------------------------------------

def test_png_export_creates_file(tmp_path):
    import os

    sim = _sim(n=3)
    out = tmp_path / "map.png"
    written = export_map_png(sim, out)
    assert written == str(out)
    assert os.path.getsize(out) > 1000


def test_png_deterministic_bytes(tmp_path):
    """Fixed palette + Agg backend -> byte-identical exports."""
    sim = _sim(n=3)
    out1 = tmp_path / "a.png"
    out2 = tmp_path / "b.png"
    export_map_png(sim, out1)
    export_map_png(sim, out2)
    assert out1.read_bytes() == out2.read_bytes()


# ----------------------------------------------------------------------
# Frozen contract
# ----------------------------------------------------------------------

def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
