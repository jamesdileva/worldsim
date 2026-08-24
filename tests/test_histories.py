"""Sprint 45: civilization histories — chronicles + population curves."""

import pytest

from worldsim.actions import NUM_ACTIONS
from worldsim.agents import OBSERVATION_DIM
from worldsim.histories import (
    build_chronicle,
    civilizations_summary,
    population_curves,
    render_chronicle,
    settlement_events,
)
from worldsim.simulation import HISTORY_INTERVAL_TICKS, Simulation
from worldsim.visualization import export_population_chart
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Chronicle construction (deterministic, from the event log)
# ----------------------------------------------------------------------

def test_chronicle_starts_with_founding_line():
    sim = _sim(n=1)
    s = sim.settlements[0]
    lines = build_chronicle(sim, s)
    assert lines[0] == (
        f"[t{s.created_at_tick}] founded at ({s.spawn_x}, {s.spawn_y})"
    )


def test_chronicle_includes_intervening_events_in_order():
    sim = _sim(n=1)
    s = sim.settlements[0]
    sim.god_bless_resources(s, "food", 10)
    sim.god_smite(s, 1)
    lines = build_chronicle(sim, s)
    divine_lines = [l for l in lines if "divine" in l]
    assert len(divine_lines) == 2
    # Order proven by content, not string offsets (both lines share [t0]).
    assert "blessed" in divine_lines[0]
    assert "smote" in divine_lines[1]


def test_chronicle_of_dead_settlement_mentions_fall_and_rebirth():
    sim = _sim(n=2)
    a, b = [s for s in sim.settlements if s.is_alive][:2]
    a.population = 5
    ruin = sim._kill(a)
    # Someone rises from its ruins.
    heir = Simulation(World(seed=1, size=64)).spawn_settlements(count=1)[0]
    heir.ruin_origin = ruin.id
    heir.created_at_tick = sim.tick
    heir.name = "Reborn"
    sim.settlements.append(heir)
    text = "\n".join(build_chronicle(sim, a))
    assert "fell" in text
    assert "reborn as Reborn" in text


def test_chronicle_capped_with_truncation_marker():
    sim = _sim(n=1)
    s = sim.settlements[0]
    for i in range(30):
        sim.log_event("test", [s.id], f"event {i}")
    lines = build_chronicle(sim, s, max_lines=10)
    assert len(lines) == 11  # founding + 9 events + truncation marker
    assert lines[-1] == "... chronicle truncated"


def test_render_chronicle_header_shows_status():
    sim = _sim(n=1)
    s = sim.settlements[0]
    text = render_chronicle(sim, s)
    assert text.startswith(f"=== Chronicle of {s.name} [alive, pop")
    sim._kill(s)
    fallen = render_chronicle(sim, s)
    assert "[fallen" in fallen.splitlines()[0]


def test_settlement_events_filtered_by_actor():
    sim = _sim(n=2)
    a, b = sim.settlements[:2]
    sim.log_event("raid", [a.id], "A raided")
    sim.log_event("raid", [b.id], "B raided")
    events_a = settlement_events(sim, a.id)
    assert all(a.id in e.actor_ids for e in events_a)
    assert len(events_a) == 1


# ----------------------------------------------------------------------
# Population curves from epoch history
# ----------------------------------------------------------------------

def test_epoch_records_include_populations_map():
    sim = _sim(n=3)
    for _ in range(HISTORY_INTERVAL_TICKS + 7):
        sim.step()
    epoch = sim.history[-1]
    living_names = {
        s.name for s in sim.settlements if s.is_alive
    }
    assert set(epoch["populations"]) == living_names


def test_population_curves_track_growth():
    sim = _sim(n=2)
    for _ in range(HISTORY_INTERVAL_TICKS * 2):
        sim.step()
    ticks, curves = population_curves(sim)
    assert len(ticks) >= 2
    assert set(curves) <= {s.name for s in sim.settlements}
    for name, samples in curves.items():
        assert samples[0] > 0


def test_population_chart_export(tmp_path):
    sim = _sim(n=2)
    for _ in range(HISTORY_INTERVAL_TICKS + 3):
        sim.step()
    out = tmp_path / "pops.png"
    written = export_population_chart(sim, out)
    import os

    assert os.path.getsize(out) > 1000 and written == str(out)


def test_population_chart_requires_epochs(tmp_path):
    sim = _sim(n=1)  # no epochs recorded yet
    with pytest.raises(ValueError):
        export_population_chart(sim, tmp_path / "none.png")


# ----------------------------------------------------------------------
# Civilizations summary + contract
# ----------------------------------------------------------------------

def test_civilizations_summary_lists_alive_and_fallen():
    sim = _sim(n=2)
    victim = sim.settlements[0]
    victim.technologies.append("masonry")
    sim._kill(victim)
    lines = civilizations_summary(sim)
    by_name = {line.split(":")[0]: line for line in lines}
    assert f"{victim.name}: fallen" in by_name[victim.name]
    other = [
        line for name, line in by_name.items() if name != victim.name
    ][0]
    assert ": alive |" in other
    assert "1 techs remembered" in by_name[victim.name]


def test_summary_deterministic_across_identical_sims():
    def run():
        sim = _sim(n=3, seed=77)
        for _ in range(60):
            sim.step()
        return civilizations_summary(sim)

    assert run() == run()


def test_frozen_contract_unchanged():
    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
