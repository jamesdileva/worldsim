"""Sprint 23: strategy discovery - signatures, clustering, novelty, logs."""

import json

import numpy as np
import pytest

from worldsim.discovery import (
    behavior_signature,
    discover_strategies,
    load_discovery_log,
    novelty_vs_archetypes,
    save_discovery_log,
)


def test_signature_building_dims_normalized():
    sig = behavior_signature(farms=30, granaries=10, sawmills=5, mines=5,
                             routes_established=0, raids_committed=0,
                             roads=0)
    assert sig[:3].sum() == pytest.approx(1.0)
    assert sig[3] == 0.0 and sig[4] == 0.0 and sig[5] == 0.0


def test_signature_activity_dims_capped():
    sig = behavior_signature(farms=1, granaries=0, sawmills=1, mines=0,
                             routes_established=99, raids_committed=99,
                             roads=999)
    assert sig[3] == pytest.approx(1.0)
    assert sig[4] == pytest.approx(1.0)
    assert sig[5] == pytest.approx(1.0)


def test_novelty_flags_far_from_all_archetypes():
    sig = np.array([0.05, 0.05, 0.05, 0.15, 0.95, 0.95])
    novel, closest, dist = novelty_vs_archetypes(sig)
    assert novel is True
    assert closest == "military"
    assert dist > 0.55


def test_novelty_not_triggered_for_known_profile():
    sig = np.array([0.90, 0.03, 0.07, 0.0, 0.0, 0.0])
    novel, closest, dist = novelty_vs_archetypes(sig)
    assert novel is False
    assert closest == "agricultural"

# ----------------------------------------------------------------------
# Clustering
# ----------------------------------------------------------------------

def _sample(gen, seed, sig, label):
    return {"generation": gen, "seed": seed,
            "checkpoint_path": f"pol_{gen}.zip", "signature": list(sig),
            "strategy_label": label}


def test_clustering_separates_distinct_profiles():
    farm_sig = behavior_signature(farms=30, granaries=5, sawmills=0, mines=0,
                                  routes_established=0, raids_committed=0,
                                  roads=0)
    raid_sig = behavior_signature(farms=2, granaries=0, sawmills=1, mines=1,
                                  routes_established=0, raids_committed=12,
                                  roads=20)
    trade_sig = behavior_signature(farms=5, granaries=1, sawmills=0, mines=0,
                                   routes_established=6, raids_committed=0,
                                   roads=50)
    samples = [
        _sample("gA", 9000 + i, list(farm_sig), "agricultural")
        for i in range(4)
    ] + [_sample("gA", 9100 + i, list(raid_sig), "military")
         for i in range(4)] + [
        _sample("gA", 9200 + i, list(trade_sig), "trading")
        for i in range(4)
    ]
    analysis = discover_strategies(samples, n_clusters=3)
    assert len(analysis["clusters"]) == 3
    sizes = sorted(c["size"] for c in analysis["clusters"])
    assert sizes == [4, 4, 4]  # clean separation


def test_novel_cluster_flagged():
    weird_sig = [0.05, 0.05, 0.05, 0.15, 0.95, 0.95]
    farm_sig = behavior_signature(farms=30, granaries=5, sawmills=0, mines=0,
                                  routes_established=0, raids_committed=0,
                                  roads=0)
    samples = ([_sample("gA", 9000 + i, list(farm_sig), "agricultural")
                for i in range(6)]
               + [_sample("gA", 9100 + i, weird_sig, "unknown")
                  for i in range(6)])
    analysis = discover_strategies(samples, n_clusters=2)
    novel = analysis["novel_clusters"]
    assert len(novel) >= 1
    assert any("novel" in c["name"] for c in novel)


# ----------------------------------------------------------------------
# Persistence & exemplar reproducibility
# ----------------------------------------------------------------------

def test_discovery_log_round_trip(tmp_path):
    log = [{
        "name": "routes-focused", "size": 2,
        "exemplar": {"seed": 70000, "checkpoint_path": "pol_gen1.zip"},
    }]
    path = save_discovery_log(log, tmp_path / "disc.json")
    loaded = load_discovery_log(path)
    assert loaded == log
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == (
        "routes-focused"
    )
