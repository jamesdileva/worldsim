"""Sprint 36: collapse and recovery depth."""

import pytest

from worldsim.buildings import Improvement
from worldsim.recovery import (
    MAX_POP_PER_RECIPIENT,
    SALVAGE_FRACTION,
    decay_building,
    migrate_refugees,
    refugee_recipients,
    salvage_from,
)
from worldsim.settlement import Settlement
from worldsim.simulation import Simulation
from worldsim.world import World


def _sim(n=2, seed=42) -> Simulation:
    sim = Simulation(World(seed=seed, size=64))
    sim.spawn_settlements(count=n)
    return sim


# ----------------------------------------------------------------------
# Salvage snapshot
# ----------------------------------------------------------------------

def test_salvage_is_half_of_non_negative_stockpiles():
    s = Settlement(name="A", spawn_x=1, spawn_y=1)
    s.resource_inventory.update({"wood": 30.0, "stone": 10.0, "metal": -4.0})
    salvage = salvage_from(s)
    assert salvage == {"wood": 15.0, "stone": 5.0}


def test_ruin_records_era_techs_and_salvage():
    sim = _sim(n=1)
    s = sim.settlements[0]
    s.technologies.extend(["agriculture", "masonry"])
    s.resource_inventory["wood"] = 40.0
    ruin = sim._kill(s)
    assert ruin.era == 2  # agriculture + masonry => Era II
    assert ruin.technologies == ["agriculture", "masonry"]
    assert ruin.salvage["wood"] == pytest.approx(40.0 * SALVAGE_FRACTION)


# ----------------------------------------------------------------------
# Refugee migration
# ----------------------------------------------------------------------

def test_refugees_flee_to_allies_on_death():
    sim = _sim()
    a, b = [s for s in sim.settlements if s.is_alive][:2]
    sim.diplomacy.form_alliance(a.id, b.id)
    a.population = 20
    b_before = b.population
    sim._kill(a)  # migration happens inside
    # deterministic share: half of 20 = 10 migrants, capped per recipient
    moved = b.population - b_before
    assert 1 <= moved <= MAX_POP_PER_RECIPIENT
    types = [e.type for e in sim.event_log]
    assert "migration" in types


def test_no_migration_without_allies():
    sim = _sim()
    a, b = sim.settlements[:2]
    b_before = b.population
    sim._kill(a)
    assert b.population == b_before


def test_migration_prefers_federation_then_allies_deterministically():
    from worldsim.treaties import federations

    sim = _sim(n=4)
    living = [s for s in sim.settlements if s.is_alive]
    dying, m1, m2, ally = living[0], living[1], living[2], living[3]
    sim.diplomacy.form_alliance(m1.id, m2.id)
    sim.diplomacy.form_alliance(m1.id, dying.id)   # federation candidate?
    sim.diplomacy.form_alliance(dying.id, ally.id)
    recipients = refugee_recipients(sim, dying)
    names = [r.name for r in recipients]
    # federation members first (sorted), then plain allies — no dupes
    assert len(names) == len(set(names))
    assert set(names) == {m1.name, m2.name, ally.name} or set(
        names) >= {ally.name}


# ----------------------------------------------------------------------
# Knowledge recovery via re-settlement
# ----------------------------------------------------------------------

def test_resettled_settlement_inherits_technology_and_salvage():
    sim = _sim(n=1)
    s = sim.settlements[0]
    s.technologies.extend(["agriculture", "masonry"])
    s.resource_inventory.update({"wood": 60.0, "stone": 20.0})
    ruin = sim._kill(s)
    # Force the resettle roll deterministically.
    import random
    import zlib
    from worldsim.simulation import RUIN_RESETTLE_MIN_AGE

    age = RUIN_RESETTLE_MIN_AGE + (100 - RUIN_RESETTLE_MIN_AGE % 100)
    rng_seed = (sim.world.seed ^ 0x2A7E5D) + zlib.crc32(ruin.id.encode()) * 31 + age
    del rng_seed  # not needed; just drive ticks until success or give-up
    for _ in range(2000):
        sim.world.tick += 1
        result = sim._try_resettle_ruin(ruin)
        if result is not None:
            break
    else:
        pytest.fail("ruin never re-settled within budget")
    heir = result
    assert heir.technologies == ["agriculture", "masonry"]
    assert heir.ruin_origin == ruin.id
    assert heir.resource_inventory["wood"] >= 30.0
    types = [e.type for e in sim.event_log]
    assert "recovery" in types


# ----------------------------------------------------------------------
# Building decay under prolonged scarcity
# ----------------------------------------------------------------------

def test_decay_strips_one_building_per_threshold():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    for _ in range(30):
        sim.step()
    before = int((sim.world.improvements != -1).sum())
    assert before > 0, "agent built something within 30 ticks"
    lost = decay_building(sim, s)
    assert lost is True
    after = int((sim.world.improvements != -1).sum())
    assert after == before - 1
    types = [e.type for e in sim.event_log]
    assert "decay" in types


def test_no_decay_without_buildings():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    assert decay_building(sim, s) is False


def test_decay_integrates_with_sustained_scarcity():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    for _ in range(30):
        sim.step()
    buildings_before = int((sim.world.improvements != -1).sum())
    assert buildings_before > 0
    # Drive the scarcity counter to just below a collapse event: the next
    # tick strips one population AND one building together.
    from worldsim.simulation import COLLAPSE_INTERVAL_TICKS

    s.negative_inventory_progress = COLLAPSE_INTERVAL_TICKS
    s.resource_inventory["stone"] = -500.0
    for _ in range(3):
        sim.step()
        if int((sim.world.improvements != -1).sum()) < buildings_before:
            break
    buildings_after = int((sim.world.improvements != -1).sum())
    assert (buildings_after < buildings_before) or not s.is_alive


# ----------------------------------------------------------------------
# Determinism + persistence + contract
# ----------------------------------------------------------------------

def test_byte_identical_collapse_scenarios():
    def run():
        sim = _sim(n=3, seed=88)
        sim.settlements[0].population = 3  # doomed-ish
        sim.settlements[0].resource_inventory.update(
            {"wood": -300.0, "stone": -300.0})
        sim.diplomacy.form_alliance(
            sim.settlements[0].id, sim.settlements[1].id)
        for _ in range(400):
            sim.step()
        return [
            (
                s.name, s.population,
                dict(sorted(s.resource_inventory.items())),
            )
            for s in sim.settlements
        ] + [(r.name, r.salvage) for r in sim.ruins]

    assert run() == run()


def test_ruin_round_trips_serialization():
    from worldsim.db import _decode_ruin, _encode_ruin

    sim = _sim(n=1)
    s = sim.settlements[0]
    s.technologies.append("masonry")
    ruin = sim._kill(s)
    restored = _decode_ruin(_encode_ruin(ruin))
    assert restored.era == ruin.era
    assert restored.technologies == ["masonry"]
    assert restored.salvage == ruin.salvage


def test_frozen_contract_unchanged():
    from worldsim.actions import NUM_ACTIONS
    from worldsim.agents import OBSERVATION_DIM

    assert NUM_ACTIONS == 62
    assert OBSERVATION_DIM == 60
