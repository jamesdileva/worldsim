"""Sprint 30: paired rule-based vs LLM-advised comparison.

Fast tier uses a fake LLM client; no network."""

import json

import pytest

from worldsim.advice import AdviceResult
from worldsim.comparison import (
    _run_llm_arm,
    compare_llm_vs_baseline,
    verdict_text,
)
from worldsim.llm import LLMResult
from worldsim.training import _run_baseline, generate_report

GOOD = LLMResult(ok=True, text=json.dumps(
    {"priorities": ["build more farms", "claim territory"], "rationale": "r"}))
FAIL = LLMResult(ok=False, error="server down")


class FakeClient:
    def __init__(self, result=GOOD):
        self.result = result

    def generate(self, prompt, system=None):
        return self.result


BASE_KWARGS = dict(num_worlds=2, first_seed=1000, ticks=80, size=48,
                   num_settlements=3)


def test_pipeline_runs_and_shape_matches_report():
    results = compare_llm_vs_baseline(client=FakeClient(), **BASE_KWARGS)
    # report-compatible keys (generate_report reads these)
    for key in ("worlds", "difficulty", "policy_wins", "ties",
                "win_fraction_strict", "reward_win_fraction",
                "mean_baseline_survival", "mean_policy_survival",
                "metrics", "results"):
        assert key in results
    assert len(results["results"]) == 2
    assert [r["seed"] for r in results["results"]] == [1000, 1001]
    for metric in ("survival_ticks", "peak_population", "territory",
                   "buildings", "routes_established", "cumulative_reward"):
        m = results["metrics"][metric]
        assert {"baseline_mean", "policy_mean", "delta",
                "wilcoxon_p"} <= set(m)
        assert "perm_p" in m


def test_report_generation_from_llm_comparison(tmp_path):
    results = compare_llm_vs_baseline(client=FakeClient(), **BASE_KWARGS)
    md = tmp_path / "report.md"
    out = generate_report(results, md)
    assert out == md and md.exists()
    text = md.read_text(encoding="utf-8")
    assert "| Metric | Baseline | Policy | Delta | Wilcoxon p |" in text
    assert str(1000) in text


def test_llm_down_arms_byte_identical():
    """Degradation is honest: with every advice request failing, the
    LLMDrivenAgent falls back to the identical RuleBasedAgent (same seed,
    same index) so both arms produce identical numbers."""
    base = _run_baseline(1000, size=48, num_settlements=3, ticks=80)
    pol = _run_llm_arm(1000, size=48, num_settlements=3, ticks=80,
                       client=FakeClient(result=FAIL))
    assert base[0] == pol[0]          # survival ticks
    assert base[1] == pol[1]          # peak population
    assert base[3] == pol[3]          # cumulative reward
    assert base[2]["territory"] == pol[2]["territory"]
    assert base[2]["buildings"] == pol[2]["buildings"]


def test_working_advice_actually_executes_actions():
    _, _, end, _ = _run_llm_arm(1000, size=48, num_settlements=3, ticks=120,
                                client=FakeClient(),
                                advice_interval_ticks=30)
    assert end["validated_actions"] >= 1


def test_verdict_inconclusive_when_degraded():
    results = {"advice": {"degraded": True},
               "metrics": {}}
    text = verdict_text(results)
    assert "inconclusive" in text.lower()


def test_verdict_reports_significant_metrics():
    results = {
        "advice": {"degraded": False},
        "metrics": {
            "survival_ticks": {"wilcoxon_p": 0.01, "delta": +50.0},
            "territory": {"wilcoxon_p": 0.03, "delta": -4.0},
            "buildings": {"wilcoxon_p": 0.40, "delta": +9.0},
        },
    }
    text = verdict_text(results)
    assert "BETTER at: survival_ticks" in text
    assert "WORSE at: territory" in text
    assert "buildings" not in text.split("significantly")[0].split(":")[-1]


def test_verdict_no_significance():
    results = {
        "advice": {"degraded": False},
        "metrics": {"survival_ticks": {"wilcoxon_p": 0.9, "delta": +5.0}},
    }
    assert "No statistically significant" in verdict_text(results)
