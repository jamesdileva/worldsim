"""Strategic advice generation (Sprint 27).

Turns Sprint 26 summaries into structured strategic priorities via the
Sprint 25 Ollama client. Contract:
- Advisory-only: advice is returned/logged, NEVER executed or fed into
  simulation physics this sprint.
- Never raises: every failure mode (server down, timeout, garbage output,
  wrong JSON types) degrades to an ok=False AdviceResult.
- Parsing is strict: only well-formed JSON with non-empty string lists
  survives; anything else is "no advice".
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from .llm import LLMResult

MAX_PRIORITIES = 5

SYSTEM_PROMPT = """\
You are the strategic advisor for one settlement in a deterministic \
civilization simulator. You never execute anything; you only advise. \
World rules: settlements need positive net food to grow, buildings cost \
wood/stone, trade routes need friendly-enough relations, raids create \
enemies and wars, happiness collapses under long starvation.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"priorities": ["<most important action>", "<second>", ...], \
"rationale": "<1-3 sentences explaining why>"}

Rules for the JSON:
- 1 to 5 priorities, each a short imperative phrase (max ~12 words).
- Order by importance, most important first.
- Base every priority on the provided state summary only.
- Output must be valid JSON and nothing else."""

USER_PROMPT_TEMPLATE = """\
Current state of settlement {name}:

{summary}

Give your strategic priorities as JSON now."""


class _GenerateClient(Protocol):
    def generate(self, prompt: str,
                 system: str | None = None) -> LLMResult: ...


@dataclass
class StrategicAdvice:
    priorities: list[str]
    rationale: str


@dataclass
class AdviceResult:
    ok: bool
    advice: StrategicAdvice | None = None
    error: str = ""
    raw: str = ""
    elapsed_s: float = 0.0


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_advice(text: str) -> StrategicAdvice | None:
    """Strictly parse model output into StrategicAdvice, else None.

    Tolerates markdown fences / chatter around the JSON object but nothing
    inside it: keys must exist, priorities must be a list of non-empty
    strings, rationale a non-empty string."""
    if not text or not text.strip():
        return None
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    priorities = data.get("priorities")
    rationale = data.get("rationale")
    if not isinstance(priorities, list) or not isinstance(rationale, str):
        return None
    cleaned = [p.strip() for p in priorities
               if isinstance(p, str) and p.strip()]
    if not cleaned or not rationale.strip():
        return None
    return StrategicAdvice(priorities=cleaned[:MAX_PRIORITIES],
                           rationale=rationale.strip())


def build_advice_prompt(summary: str, name: str = "the settlement",
                        system: str | None = None,
                        user_template: str | None = None) -> tuple[str, str]:
    """(system, user) prompt pair for an advice request."""
    return (
        system or SYSTEM_PROMPT,
        (user_template or USER_PROMPT_TEMPLATE).format(
            name=name, summary=summary),
    )


def advise(client: _GenerateClient, summary: str,
           name: str = "the settlement") -> AdviceResult:
    """Request + parse strategic advice. Never raises."""
    system, user = build_advice_prompt(summary, name=name)
    started = time.time()
    result = client.generate(user, system=system)
    if not result.ok:
        return AdviceResult(ok=False, error=result.error, raw=result.text,
                            elapsed_s=result.elapsed_s)
    advice = parse_advice(result.text)
    if advice is None:
        return AdviceResult(ok=False, error="unparseable model output",
                            raw=result.text, elapsed_s=result.elapsed_s)
    return AdviceResult(ok=True, advice=advice, raw=result.text,
                        elapsed_s=time.time() - started)


@dataclass
class AdviceLog:
    """Append-only advisory log (jsonl). Side channel — never sim state."""

    entries: list[dict] = field(default_factory=list)

    def record(self, name: str, tick: int, result: AdviceResult) -> dict:
        entry = {
            "name": name,
            "tick": tick,
            "ok": result.ok,
            "error": result.error,
            "priorities": result.advice.priorities if result.advice else [],
            "rationale": result.advice.rationale if result.advice else "",
        }
        self.entries.append(entry)
        return entry

    def append_to(self, path) -> None:
        import json as _json
        from pathlib import Path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for entry in self.entries:
                handle.write(_json.dumps(entry) + "\n")
