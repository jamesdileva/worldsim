"""Inter-settlement relations: hostile / friendly / neutral (Sprint 9).

Pairwise scores in [-100, +100]. Trade pushes scores positive, raids push
them negative, and everything decays toward neutral over time — hostility
persists roughly 2000 ticks unless re-triggered.
"""

from __future__ import annotations

HOSTILE_THRESHOLD = -25.0
FRIENDLY_THRESHOLD = +25.0
WAR_THRESHOLD = -60.0  # routes deactivate below this

TRADE_ESTABLISHED_BONUS = 10.0
TRADE_TRANSFER_BONUS = 0.05
RAID_ATTEMPTED_PENALTY = 20.0
RAID_SUCCESS_PENALTY = 30.0
# -50 (worst common case) neutralizes in ~2000 ticks.
DECAY_PER_TICK = 0.025

SCORE_MIN = -100.0
SCORE_MAX = +100.0


def relation_label(score: float) -> str:
    if score < HOSTILE_THRESHOLD:
        return "hostile"
    if score > FRIENDLY_THRESHOLD:
        return "friendly"
    return "neutral"


class RelationMatrix:
    """Symmetric pairwise relations keyed by frozenset{id_a, id_b}."""

    def __init__(self) -> None:
        self._scores: dict[frozenset, float] = {}

    @staticmethod
    def _key(a: str, b: str) -> frozenset:
        return frozenset((a, b))

    def score(self, a: str, b: str) -> float:
        return self._scores.get(self._key(a, b), 0.0)

    def label(self, a: str, b: str) -> str:
        return relation_label(self.score(a, b))

    def is_hostile(self, a: str, b: str) -> bool:
        return self.score(a, b) < HOSTILE_THRESHOLD

    def is_friendly(self, a: str, b: str) -> bool:
        return self.score(a, b) > FRIENDLY_THRESHOLD

    def adjust(self, a: str, b: str, delta: float) -> float:
        key = self._key(a, b)
        new = max(SCORE_MIN, min(SCORE_MAX, self._scores.get(key, 0.0) + delta))
        self._scores[key] = new
        return new

    def decay_tick(self) -> None:
        """Move every score toward 0 by DECAY_PER_TICK."""
        for key, score in list(self._scores.items()):
            if score > 0:
                self._scores[key] = max(0.0, score - DECAY_PER_TICK)
            elif score < 0:
                self._scores[key] = min(0.0, score + DECAY_PER_TICK)

    def pairs(self) -> list[tuple[str, str, float]]:
        return [
            (sorted(key)[0], sorted(key)[1], score)
            for key, score in self._scores.items()
        ]

    def to_dict(self) -> dict[str, list]:
        return {
            "|".join(sorted(key)): score for key, score in self._scores.items()
        }

    @classmethod
    def from_dict(cls, obj: dict[str, float]) -> RelationMatrix:
        matrix = cls()
        for pair_key, score in obj.items():
            a, b = pair_key.split("|")
            matrix._scores[frozenset((a, b))] = score
        return matrix
