"""Civilization histories (Sprint 45).

Each settlement's story reconstructed deterministically from the event
log — no new state, nothing new to persist. Chronicles read like sagas:
founding, discoveries, wars, battles, treaties, disasters, divine
interventions, migration, death, and recovery.

Population curves come from the S37 epoch history (`sim.history`), which
records a per-settlement `populations` map every HISTORY_INTERVAL_TICKS.
Epochs are RAM-only within a run: chronicle TEXT works across save/load
(event log is persisted) but curves require epochs from the current run.
"""

from __future__ import annotations

from .settlement import Settlement


def settlement_events(sim, settlement_id: str) -> list:
    """Chronological events involving the settlement. Deterministic."""
    return [
        e for e in sim.event_log if settlement_id in e.actor_ids
    ]


def build_chronicle(sim, settlement: Settlement,
                    max_lines: int = 100) -> list[str]:
    """The civilization's story as chronological lines.

    Deterministic: pure function of the persisted event log plus
    settlement fields."""
    lines: list[str] = [
        f"[t{settlement.created_at_tick}] founded at "
        f"({settlement.spawn_x}, {settlement.spawn_y})"
    ]
    for event in settlement_events(sim, settlement.id):
        lines.append(f"[t{event.tick}] {event.type}: {event.description}")
        if len(lines) >= max_lines:
            lines.append("... chronicle truncated")
            break

    if not settlement.is_alive:
        died = settlement.destroyed_at_tick
        lines.append(
            f"[t{died}] fell; population gone"
            + (
                " — but its people live on in other settlements"
                if any(
                    "migration" == e.type and settlement.id in e.actor_ids
                    for e in sim.event_log
                )
                else ""
            )
        )
        # Recovery cross-reference: did anyone rise from its ruins?
        ruin_ids = {
            r.id for r in getattr(sim, "ruins", [])
            if r.settlement_id == settlement.id
        }
        if ruin_ids:
            for other in sim.settlements:
                if other.ruin_origin in ruin_ids and other is not settlement:
                    lines.append(
                        f"[t{other.created_at_tick}] reborn as "
                        f"{other.name} among the ruins"
                    )
                    break
    return lines


def render_chronicle(sim, settlement: Settlement,
                     max_lines: int = 100) -> str:
    """Header + chronicle lines, ready to print."""
    status = (
        f"alive, pop {settlement.population}"
        if settlement.is_alive
        else f"fallen (tick {settlement.destroyed_at_tick})"
    )
    header = (
        f"=== Chronicle of {settlement.name} [{status}] ==="
    )
    body = build_chronicle(sim, settlement, max_lines=max_lines)
    return "\n".join([header] + body)


def population_curves(sim) -> tuple[list[int], dict[str, list[int]]]:
    """(ticks, name -> population samples) from epoch history."""
    ticks = [h["tick"] for h in sim.history]
    curves: dict[str, list[int]] = {}
    for h in sim.history:
        for name, pop in sorted(h.get("populations", {}).items()):
            curves.setdefault(name, []).append(pop)
    return ticks, curves


def civilizations_summary(sim) -> list[str]:
    """One line per civilization ever known to this world: current fate
    plus headline stats. Deterministic."""
    lines = []
    for s in sorted(sim.settlements, key=lambda x: x.name):
        era = s.era
        techs = len(s.technologies)
        wars_fought = sum(
            1 for e in sim.event_log
            if e.type == "battle" and s.id in e.actor_ids
        )
        if s.is_alive:
            lines.append(
                f"{s.name}: alive | pop {s.population} | era {era} | "
                f"{techs} techs | {wars_fought} battles | "
                f"founded t{s.created_at_tick}"
            )
        else:
            lines.append(
                f"{s.name}: fallen (t{s.destroyed_at_tick}) | era {era} | "
                f"{techs} techs remembered | founded t{s.created_at_tick}"
            )
    return lines
