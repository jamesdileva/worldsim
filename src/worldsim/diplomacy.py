"""Diplomacy: wars, alliances, peace treaties, reputation (Sprint 10).

- War is declared automatically after 3 raids by the same attacker on the
  same victim within 500 ticks.
- Alliances form from sustained mutual trade (tracked on TradeRoute).
- Peace requires bilateral offers within their validity window; the
  aggressor (side with more logged raids) pays 25% stockpile tribute.
- Reputation per settlement: raids cost, treaties/alliances reward, and it
  decays by 0.1 per 100 ticks of non-interaction.
"""

from __future__ import annotations

WAR_RAID_THRESHOLD = 3
WAR_WINDOW_TICKS = 500

PEACE_OFFER_VALIDITY_TICKS = 200
PEACE_TRIBUTE_FRACTION = 0.25

REPUTATION_MIN = -100.0
REPUTATION_MAX = +100.0
REPUTATION_TRADE_FLOOR = -50.0  # below this, others refuse new routes
REPUTATION_RAID_COST = 5.0
REPUTATION_TREATY_BONUS = 10.0
REPUTATION_DECAY_PER_TICK = 0.001  # = 0.1 per 100 ticks


class DiplomacyState:
    """Alliances, wars, pending peace offers, and per-settlement reputation."""

    def __init__(self) -> None:
        self.alliances: set[frozenset] = set()
        # war_key -> {"start_tick": int, "raids": [(tick, attacker_id, victim_id)]}
        self.wars: dict[frozenset, dict] = {}
        # offerer_id -> {target_id: expiry tick}
        self.peace_offers: dict[str, dict[str, int]] = {}
        self.reputation: dict[str, float] = {}
        # (attacker_id, victim_id) -> [raid ticks] for pre-war escalation
        self._raid_history: dict[tuple[str, str], list[int]] = {}

    # -- Reputation ----------------------------------------------------

    def rep(self, settlement_id: str) -> float:
        return self.reputation.get(settlement_id, 0.0)

    def adjust_rep(self, settlement_id: str, delta: float) -> float:
        new = max(REPUTATION_MIN, min(REPUTATION_MAX, self.rep(settlement_id) + delta))
        self.reputation[settlement_id] = new
        return new

    def decay_tick(self, interacted_ids: set[str]) -> None:
        """Decay reputation for settlements that did not interact this tick."""
        for sid in list(self.reputation):
            if sid not in interacted_ids:
                self.reputation[sid] = max(
                    REPUTATION_MIN, self.reputation[sid] - REPUTATION_DECAY_PER_TICK
                )

    # -- Wars ------------------------------------------------------------

    @staticmethod
    def _war_key(a: str, b: str) -> frozenset:
        return frozenset((a, b))

    def at_war(self, a: str, b: str) -> bool:
        key = self._war_key(a, b)
        return key in self.wars

    def wars_of(self, settlement_id: str) -> list[frozenset]:
        return [
            key
            for key in self.wars
            if settlement_id in key
        ]

    def record_raid(self, attacker_id: str, victim_id: str, tick: int) -> bool:
        """Log a raid; declare war at WAR_RAID_THRESHOLD inside the window.
        Returns True if this raid triggered a declaration."""
        if self.at_war(attacker_id, victim_id):
            war = self.wars[self._war_key(attacker_id, victim_id)]
            war["raids"].append((tick, attacker_id, victim_id))
            return False
        history = self._raid_history.setdefault(
            (attacker_id, victim_id), []
        )
        history.append(tick)
        # Prune raids outside the escalation window.
        history[:] = [
            t for t in history if tick - t <= WAR_WINDOW_TICKS
        ]
        if len(history) >= WAR_RAID_THRESHOLD:
            self.wars[self._war_key(attacker_id, victim_id)] = {
                "start_tick": tick,
                "raids": [
                    (t, attacker_id, victim_id) for t in history
                ],
            }
            history.clear()
            return True
        return False

    def declare_war(self, a: str, b: str, tick: int) -> None:
        self.wars.setdefault(self._war_key(a, b), {"start_tick": tick, "raids": []})

    def conclude_peace(self, a: str, b: str) -> tuple[int, int]:
        """End the war between a and b. Returns (raids_by_a, raids_by_b)."""
        key = self._war_key(a, b)
        war = self.wars.pop(key, {"raids": []})
        by_a = sum(1 for _, att, _ in war["raids"] if att == a)
        by_b = sum(1 for _, att, _ in war["raids"] if att == b)
        self.peace_offers.pop(a, None)
        self.peace_offers.pop(b, None)
        return by_a, by_b

    # -- Alliances ---------------------------------------------------------

    def is_allied(self, a: str, b: str) -> bool:
        return self._war_key(a, b) in self.alliances

    def form_alliance(self, a: str, b: str) -> bool:
        key = self._war_key(a, b)
        if key in self.alliances:
            return False
        self.alliances.add(key)
        return True

    # -- Peace offers -------------------------------------------------------

    def offer_peace(self, offerer_id: str, target_id: str, tick: int) -> None:
        self.peace_offers.setdefault(offerer_id, {})[target_id] = (
            tick + PEACE_OFFER_VALIDITY_TICKS
        )

    def has_live_offer(self, offerer_id: str, target_id: str, tick: int) -> bool:
        expiry = self.peace_offers.get(offerer_id, {}).get(target_id)
        return expiry is not None and tick <= expiry

    def expire_stale_offers(self, tick: int) -> None:
        for offerer, targets in list(self.peace_offers.items()):
            self.peace_offers[offerer] = {
                target: expiry
                for target, expiry in targets.items()
                if expiry > tick
            }

    # -- Serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "alliances": [sorted(pair) for pair in self.alliances],
            "wars": {
                "|".join(sorted(key)): war for key, war in self.wars.items()
            },
            "peace_offers": self.peace_offers,
            "reputation": self.reputation,
            "raid_history": {
                f"{att}|{vic}": raids
                for (att, vic), raids in getattr(self, "_raid_history", {}).items()
            },
        }

    @classmethod
    def from_dict(cls, obj: dict) -> DiplomacyState:
        state = cls()
        state.alliances = {frozenset(pair) for pair in obj.get("alliances", [])}
        for key, war in obj.get("wars", {}).items():
            state.wars[frozenset(key.split("|"))] = war
        state.peace_offers = obj.get("peace_offers", {})
        state.reputation = obj.get("reputation", {})
        history = {}
        for pair_key, raids in obj.get("raid_history", {}).items():
            att, vic = pair_key.split("|")
            history[(att, vic)] = raids
        state._raid_history = history
        return state
