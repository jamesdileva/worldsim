"""Sprint 27: advice prompt/parse/degradation tests.

All HTTP mocked; the live round-trip test auto-skips without Ollama and
enforces >=90% parseability when it does run.
"""

import pytest

from worldsim.advice import (
    MAX_PRIORITIES,
    AdviceLog,
    AdviceResult,
    StrategicAdvice,
    advise,
    build_advice_prompt,
    parse_advice,
)
from worldsim.llm import LLMConfig, LLMResult, OllamaClient

GOOD_TEXT = ('{"priorities": ["Build a farm", "Open a trade route"], '
             '"rationale": "Food is fine; expand economy."}')


class FakeClient:
    def __init__(self, result: LLMResult):
        self.result = result
        self.calls: list[tuple[str, str | None]] = []

    def generate(self, prompt, system=None):
        self.calls.append((prompt, system))
        return self.result


def _ok(text: str) -> LLMResult:
    return LLMResult(ok=True, text=text, model="stub", elapsed_s=0.1)


# ----------------------------------------------------------------------
# Parser matrix
# ----------------------------------------------------------------------

def test_parse_clean_json():
    advice = parse_advice(GOOD_TEXT)
    assert advice == StrategicAdvice(
        priorities=["Build a farm", "Open a trade route"],
        rationale="Food is fine; expand economy.")


def test_parse_with_markdown_fence_and_chatter():
    text = f"Here is my advice:\n```json\n{GOOD_TEXT}\n```\nHope that helps!"
    advice = parse_advice(text)
    assert advice is not None
    assert advice.priorities[0] == "Build a farm"


def test_parse_strips_whitespace_and_caps_priorities():
    many = ", ".join(f'"p{i}"' for i in range(10))
    advice = parse_advice(
        '{"priorities": [' + many + '], "rationale": "r"}')
    assert advice is not None
    assert len(advice.priorities) == MAX_PRIORITIES
    assert advice.priorities == [f"p{i}" for i in range(MAX_PRIORITIES)]


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "no json at all",
    "{broken json",
    "[]",
    '{"rationale": "missing priorities"}',
    '{"priorities": [], "rationale": "empty list"}',
    '{"priorities": ["a"], "rationale": ""}',
    '{"priorities": ["a"]}',
    '{"priorities": "not a list", "rationale": "r"}',
    '{"priorities": [42], "rationale": "r"}',
    '{"priorities": [""], "rationale": "r"}',
    '{"priorities": ["a"], "rationale": null}',
])
def test_garbage_degrades_to_none(text):
    assert parse_advice(text) is None


# ----------------------------------------------------------------------
# advise() degradation contract (never raises)
# ----------------------------------------------------------------------

def test_advise_happy_path_passes_prompts_through():
    client = FakeClient(_ok(GOOD_TEXT))
    result = advise(client, "SUMMARY TEXT", name="Alpha")
    assert result.ok and result.advice.priorities == [
        "Build a farm", "Open a trade route"]
    # summary reached the user prompt; system prompt present
    prompt, system = client.calls[0]
    assert "SUMMARY TEXT" in prompt
    assert "Alpha" in prompt
    assert system and "advisor" in system


def test_advise_server_failure_degrades():
    client = FakeClient(LLMResult(ok=False, error="unreachable"))
    result = advise(client, "S")
    assert not result.ok
    assert "unreachable" in result.error
    assert result.advice is None


def test_advise_garbage_output_never_raises():
    client = FakeClient(_ok("I am a helpful assistant! Have a nice day."))
    result = advise(client, "S")
    assert not result.ok
    assert result.error == "unparseable model output"
    assert result.raw.startswith("I am")


def test_advise_empty_output_degrades():
    client = FakeClient(_ok(""))
    assert not advise(client, "S").ok


# ----------------------------------------------------------------------
# Prompt templates
# ----------------------------------------------------------------------

def test_build_advice_prompt_shape():
    system, user = build_advice_prompt("THE SUMMARY", name="Beta")
    assert "JSON" in system
    assert "never execute" in system.lower() or "advis" in system.lower()
    assert "THE SUMMARY" in user
    assert "Beta" in user
    assert system != user


# ----------------------------------------------------------------------
# Advisory log
# ----------------------------------------------------------------------

def test_advice_log_records_success_and_failure(tmp_path):
    log = AdviceLog()
    good = AdviceResult(ok=True, advice=StrategicAdvice(["a"], "why"),
                        raw=GOOD_TEXT)
    bad = AdviceResult(ok=False, error="boom")
    log.record("Alpha", 100, good)
    log.record("Beta", 200, bad)
    out = tmp_path / "advice_log.jsonl"
    log.append_to(out)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    import json
    first, second = (json.loads(l) for l in lines)
    assert first["ok"] and first["priorities"] == ["a"]
    assert not second["ok"] and second["error"] == "boom"
    assert second["priorities"] == []


# ----------------------------------------------------------------------
# Live round-trip: >=90% parseable on real model (auto-skips)
# ----------------------------------------------------------------------

def _live_client():
    client = OllamaClient(LLMConfig())
    if not client.is_available():
        pytest.skip("Ollama not reachable")
    return client


LIVE_SEEDS = [11, 22, 33, 44, 55, 66, 77, 88, 99, 110]


@pytest.mark.slow
def test_live_roundtrip_ninety_percent_parseable():
    from worldsim import summaries
    from worldsim.simulation import Simulation
    from worldsim.world import World

    client = _live_client()
    parsed = 0
    total = 0
    for seed in LIVE_SEEDS:
        world = World(seed=seed, size=64)
        sim = Simulation(world=world)
        sim.spawn_settlements(2)
        for _ in range(40):
            sim.step()
        for s in sorted((x for x in sim.settlements if x.is_alive),
                        key=lambda x: x.name):
            total += 1
            result = advise(client,
                            summaries.summarize_settlement(sim, s),
                            name=s.name)
            if result.ok:
                parsed += 1
    assert total > 0
    assert parsed / total >= 0.9, f"{parsed}/{total} parseable"
