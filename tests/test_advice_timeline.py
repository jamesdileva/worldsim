"""Sprint 63: LLM advice is visible in the world timeline."""

from worldsim.advice import AdviceResult, StrategicAdvice
from worldsim.llm_agent import attach_llm_agent
from worldsim.reasoning import ReasoningConfig
from worldsim.simulation import Simulation
from worldsim.world import World


class FakeAdvisor:
    """Poll-based advisor stub: one ready advice result."""

    def __init__(self, result):
        self._result = result
        self.submitted = 0

    @property
    def busy(self):
        return False

    def poll(self, key):
        result = self._result
        self._result = None
        return result

    def submit(self, key, summary, name):
        self.submitted += 1
        return True


def _ok_result():
    return AdviceResult(
        ok=True,
        advice=StrategicAdvice(
            priorities=["Build a granary before winter.",
                        "Train defenders."],
            rationale="Food stocks look thin and neighbors are restless.",
        ),
    )


def test_consumed_advice_lands_in_event_log():
    sim = Simulation(World(seed=42, size=64))
    sim.spawn_settlements(count=1)
    s = sim.settlements[0]
    agent = attach_llm_agent(
        sim, s.id,
        client=object(),  # marks llm_active; advisor path owns requests
        advisor=FakeAdvisor(_ok_result()),
        config=ReasoningConfig(interval_ticks=None),
    )
    assert agent is not None
    agent.observe(sim, s)
    agent.decide(agent.fallback.observe(sim, s))

    advice_events = [e for e in sim.event_log if e.type == "advice"]
    assert len(advice_events) == 1
    text = advice_events[0].description
    assert s.name in text
    assert "counsel" in text
    assert "granary" in text.lower()
    assert "intents:" in text


def test_advice_event_category_is_counsel():
    from worldsim.timeline import category_of

    assert category_of("advice") == "counsel"
