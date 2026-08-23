"""Ollama LLM integration (Sprint 25, Phase 5).

Zero-dependency client over stdlib urllib. Core contract: every method
returns an LLMResult (ok/text/error/elapsed) and NEVER raises into the
simulation or training path — the sim must run untouched when Ollama is
down, slow, or misconfigured (roadmap §19, §28.5).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("data/world_sim/llm_config.json")
# 127.0.0.1 over localhost: avoids IPv6-first resolution quirks against
# Ollama's loopback-only listener.
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"
# First call may include model load-from-disk on modest hardware.
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_TEMPERATURE = 0.7
_REDIRECT_CODES = {301, 302, 303, 307, 308}


@dataclass
class LLMConfig:
    host: str = DEFAULT_HOST
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def load(cls, path: str | Path | None = None,
             overrides: dict | None = None) -> "LLMConfig":
        """Config precedence: overrides > JSON file > defaults."""
        config_path = Path(path) if path else DEFAULT_CONFIG_PATH
        values: dict = {}
        if config_path.exists():
            try:
                values.update(
                    json.loads(config_path.read_text(encoding="utf-8"))
                )
            except (json.JSONDecodeError, OSError):
                pass  # corrupt/unreadable file degrades to defaults
        values.update({k: v for k, v in (overrides or {}).items()
                       if v is not None})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in values.items() if k in known})


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    error: str = ""
    model: str = ""
    elapsed_s: float = 0.0


@dataclass
class OllamaClient:
    config: LLMConfig = field(default_factory=LLMConfig)

    # -- HTTP core -------------------------------------------------------

    def _post(self, endpoint: str, payload: dict):
        url = f"{self.config.host.rstrip('/')}/{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.config.timeout_s)
        except urllib.error.HTTPError as exc:
            # urllib won't re-POST on redirects; follow once manually.
            if exc.code in _REDIRECT_CODES and exc.headers.get("Location"):
                new_url = exc.headers["Location"]
                if new_url.startswith("/"):
                    base = "/".join(url.split("/")[:3])
                    new_url = base + new_url
                req2 = urllib.request.Request(
                    new_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                return urllib.request.urlopen(req2,
                                              timeout=self.config.timeout_s)
            raise

    def _get(self, endpoint: str):
        url = f"{self.config.host.rstrip('/')}/{endpoint}"
        return urllib.request.urlopen(url, timeout=min(
            self.config.timeout_s, 10.0))

    # -- Public API --------------------------------------------------------

    def generate(self, prompt: str, system: str | None = None) -> LLMResult:
        """One-shot completion via /api/generate. Never raises."""
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }
        if system:
            payload["system"] = system
        return self._request(payload, "/api/generate", extract=lambda
                             data: data.get("response", ""))

    def chat(self, messages: list[dict]) -> LLMResult:
        """Multi-turn completion via /api/chat. Never raises."""
        return self._request({"model": self.config.model,
                              "messages": messages, "stream": False},
                             "/api/chat",
                             extract=lambda data: data.get("message", {}).get(
                                 "content", ""))

    def _request(self, payload: dict, endpoint: str, extract) -> LLMResult:
        started = time.time()
        try:
            with self._post(endpoint, payload) as response:
                data = json.loads(response.read().decode("utf-8"))
            elapsed = round(time.time() - started, 2)
            return LLMResult(ok=True, text=extract(data),
                             model=payload.get("model", ""),
                             elapsed_s=elapsed)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return LLMResult(
                ok=False,
                error=f"Ollama unreachable at {self.config.host}: {reason}",
                model=payload.get("model", ""),
                elapsed_s=round(time.time() - started, 2),
            )
        except TimeoutError:
            return LLMResult(ok=False,
                             error=f"Ollama timed out after "
                                   f"{self.config.timeout_s}s",
                             model=payload.get("model", ""),
                             elapsed_s=round(time.time() - started, 2))
        except json.JSONDecodeError as exc:
            return LLMResult(ok=False, error=f"Malformed Ollama response: "
                                             f"{exc}",
                             model=payload.get("model", ""))
        except OSError as exc:
            return LLMResult(ok=False, error=f"Ollama request failed: {exc}",
                             model=payload.get("model", ""))

    # -- Health --------------------------------------------------------------

    def is_available(self) -> bool:
        try:
            with self._get("/api/tags") as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def list_models(self) -> list[str]:
        try:
            with self._get("/api/tags") as response:
                tags = json.loads(response.read().decode("utf-8"))
            return [m.get("name", "") for m in tags.get("models", [])]
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                TimeoutError):
            return []
