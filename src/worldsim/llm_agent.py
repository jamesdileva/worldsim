"""LLM-driven agent with validated intents and rule-based fallback
(Sprint 28).

Contract:
- Advice is requested through an injected `advice_provider` callable —
  this sprint does NOT schedule inference inside decide(); Sprint 29 owns
  budget-aware scheduling. Providers may be None (pure fallback).
- Every mapped intent is validated against sim mechanics BEFORE the tick
  executes; invalid intents are dropped with telemetry and the agent
  falls back to RuleBasedAgent behavior for that decision.
- With the LLM down (provider None / failure / garbage) the agent behaves
  as a plain RuleBasedAgent: full episodes survive.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from .actions import Action
from .advice import AdviceResult, advise
from .agents import Agent, RuleBasedAgent
from .intents import IntentTelemetry, map_advice_to_actions, validate_action
from .llm import LLMConfig, OllamaClient
from .reasoning import BackgroundAdvisor, ReasoningConfig, should_reason
from .settlement import Settlement
from .summaries import TIER_TINY, summarize_settlement

DEFAULT_ADVICE_INTERVAL_TICKS = 24


class LLMDrivenAgent(Agent):
    """Rule-based decisions, overridden by validated LLM intent when fresh
    advice exists.

    Two request paths:
    - `client` only: synchronous advice inside observe() (S28 behavior).
    - `advisor` set: non-blocking background requests gated by the
      ReasoningConfig scheduler; results applied on a later tick."""

    def __init__(
        self,
        seed: int,
        settlement_index: int,
        client: OllamaClient | None = None,
        advisor: BackgroundAdvisor | None = None,
        config: ReasoningConfig | None = None,
        advice_interval_ticks: int = DEFAULT_ADVICE_INTERVAL_TICKS,
        tier: str = TIER_TINY,
    ) -> None:
        self.seed = seed
        self.index = settlement_index
        self.client = client
        self.advisor = advisor
        self.config = config or ReasoningConfig(
            interval_ticks=advice_interval_ticks)
        self.advice_interval_ticks = advice_interval_ticks
        self.tier = tier
        self.fallback = RuleBasedAgent(seed, settlement_index)
        self.telemetry = IntentTelemetry()
        # Validated action queue for the current tick (max one per decide).
        self._pending: deque[Action] = deque()
        self._last_advice_tick: int | None = None
        self._last_summary: str = ""
        self.last_action: int = int(Action.IDLE)

    @property
    def llm_active(self) -> bool:
        return self.client is not None or self.advisor is not None

    def observe(self, sim, settlement: Settlement) -> np.ndarray:
        # Re-validate queued intents against CURRENT state: a farm intent
        # queued last tick may be unaffordable now.
        fresh: deque[Action] = deque()
        for action in self._pending:
            ok, reason = validate_action(sim, settlement, action)
            if ok:
                fresh.append(action)
            else:
                self.telemetry.record_drop(f"stale_{reason}")
        self._pending = fresh

        if self.advisor is not None:
            result = self.advisor.poll(settlement.id)
            if result is not None and result.ok and result.advice is not None:
                self._last_advice_tick = sim.tick
                self._queue_intents(sim, settlement, result)
            due, _why = should_reason(
                self.config, sim, settlement, self._last_advice_tick)
            if due and not self.advisor.busy:
                summary = summarize_settlement(sim, settlement,
                                               tier=self.tier)
                self._last_summary = summary
                self.advisor.submit(settlement.id, summary, settlement.name)
        elif (
            self.client is not None
            and not self._pending
            and self._advice_due(sim.tick)
        ):
            self._request_and_queue_intents(sim, settlement)
            self._last_advice_tick = sim.tick
        return self.fallback.observe(sim, settlement)

    def decide(self, obs: np.ndarray) -> int:
        if self._pending:
            action = self._pending.popleft()
            self.telemetry.actions_validated += 1
            self.last_action = int(action)
            return int(action)
        self.telemetry.fallback_decisions += 1
        action = self.fallback.decide(obs)
        self.last_action = action
        return action

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _advice_due(self, tick: int) -> bool:
        return (
            self._last_advice_tick is None
            or tick - self._last_advice_tick >= self.advice_interval_ticks
        )

    def _request_and_queue_intents(self, sim, settlement: Settlement) -> None:
        """Synchronous path (S28): request advice, map + validate; never
        raises."""
        try:
            summary = summarize_settlement(sim, settlement, tier=self.tier)
            self._last_summary = summary
            result = self._call_advisor(summary, settlement.name)
        except Exception:  # noqa: BLE001 - provider bugs must not crash sims
            self.telemetry.advice_failures += 1
            return
        if result is None or not result.ok or result.advice is None:
            self.telemetry.advice_failures += 1
            return
        self._queue_intents(sim, settlement, result)

    def _queue_intents(self, sim, settlement: Settlement,
                       result: AdviceResult) -> None:
        candidates = map_advice_to_actions(
            result.advice, telemetry=self.telemetry)
        queued: list[str] = []
        dropped: list[str] = []
        for action in candidates:
            ok, reason = validate_action(sim, settlement, action)
            if ok:
                self._pending.append(action)
                queued.append(action.name.lower())
            else:
                self.telemetry.record_drop(reason)
                dropped.append(reason)
        # S63: advice is now visible in the world timeline — otherwise
        # the advisor's work was invisible outside jsonl side channels.
        try:
            sim.log_event(
                "advice",
                [settlement.id],
                f"{settlement.name} weighed counsel: "
                f"{self._advice_digest(result)} | intents: "
                f"{len(queued)} accepted"
                + (f" ({', '.join(queued)})" if queued else "")
                + (f", {len(dropped)} dropped" if dropped else ""),
            )
        except Exception:  # noqa: BLE001 - logging must never break sims
            pass

    @staticmethod
    def _advice_digest(result: AdviceResult) -> str:
        advice = result.advice
        if advice is None:
            return "empty advice"
        parts = [p for p in advice.priorities if p]
        digest = "; ".join(parts[:3]) if parts else "no priorities"
        if advice.rationale:
            rationale = advice.rationale.strip()
            if len(rationale) > 120:
                rationale = rationale[:117] + "..."
            digest += f" — {rationale}"
        return digest

    def _call_advisor(self, summary: str,
                      name: str) -> AdviceResult | None:
        assert self.client is not None
        return advise(self.client, summary, name=name)


def attach_llm_agent(sim, settlement_id: str,
                     **kwargs) -> LLMDrivenAgent | None:
    """Swap an LLMDrivenAgent in for a settlement's slot in sim.agents.

    Returns None if the settlement id is unknown. The replaced slot keeps
    index alignment required by Simulation.step."""
    index = next(
        (i for i, s in enumerate(sim.settlements) if s.id == settlement_id),
        None,
    )
    if index is None:
        return None
    seed = sim.world.seed
    while len(sim.agents) <= index:
        sim.agents.append(None)
    agent = LLMDrivenAgent(seed=seed, settlement_index=index, **kwargs)
    sim.agents[index] = agent
    return agent
