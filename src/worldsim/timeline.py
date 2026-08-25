"""World event timeline (Sprint 46).

A chronological, filterable view over the persisted event log — the
whole-world counterpart to Sprint 45's per-civilization chronicles.
Works across save/load because the event log is persisted.

Deterministic: pure functions of state; category mapping is fixed.
"""

from __future__ import annotations

# Fixed category taxonomy for grouping/coloring. Unknown types fall into
# "other".
EVENT_CATEGORIES: dict[str, str] = {
    "raid": "warfare",
    "battle": "warfare",
    "war": "warfare",
    "siege": "warfare",
    "alliance": "diplomacy",
    "peace": "diplomacy",
    "peace_offer": "diplomacy",
    "diplomacy": "diplomacy",
    "treaty": "diplomacy",
    "technology": "civilization",
    "era": "civilization",
    "strategy": "civilization",
    "strategy_evolution": "civilization",
    "recovery": "civilization",
    "decay": "civilization",
    "migration": "civilization",
    "collapse": "civilization",
    "trade_route": "trade",
    "divine": "divine",
    "advice": "counsel",
    "disaster": "disasters",
    "drought": "disasters",
    "fire": "disasters",
    "plague": "disasters",
}


def category_of(event_type: str) -> str:
    return EVENT_CATEGORIES.get(event_type, "other")


def build_timeline(
    sim,
    types: set[str] | None = None,
    categories: set[str] | None = None,
    actor_id: str | None = None,
    since_tick: int | None = None,
    until_tick: int | None = None,
    limit: int = 200,
    tail: bool = False,
) -> list:
    """Chronologically filtered events.

    All filters AND together. By default limit keeps the OLDEST events
    first so timelines read start-to-finish deterministically; tail=True
    keeps the NEWEST events instead (live feeds)."""
    picked = []
    for e in sim.event_log:
        if types is not None and e.type not in types:
            continue
        if categories is not None and category_of(e.type) not in categories:
            continue
        if actor_id is not None and actor_id not in e.actor_ids:
            continue
        if since_tick is not None and e.tick < since_tick:
            continue
        if until_tick is not None and e.tick >= until_tick:
            continue
        picked.append(e)
    if tail:
        return picked[-max(0, limit):]
    return picked[: max(0, limit)]


def render_timeline(sim, events: list, date_stamps: bool = True) -> str:
    """Rendered lines with tick stamps (optionally human dates)."""
    from .clock import describe

    lines = []
    for e in events:
        stamp = f"[t{e.tick}]"
        if date_stamps:
            stamp += f" ({describe(e.tick)})"
        lines.append(f"{stamp} [{category_of(e.type)}] {e.type}: "
                     f"{e.description}")
    return "\n".join(lines)


def category_histogram(
    sim, window: int = 500,
) -> tuple[list[int], dict[str, list[int]]]:
    """Event counts per category per time window.

    Windows span [0..max(tick, 1)) aligned from tick 0; every category
    that appears anywhere is present in every window (0-filled), so
    series are plottable without padding logic."""
    ticks = sim.event_log[-1].tick if sim.event_log else 0
    n_windows = max(1, -(-ticks // window))
    windows = [(i + 1) * window for i in range(n_windows)]
    all_categories = sorted({category_of(e.type) for e in sim.event_log})
    series: dict[str, list[int]] = {
        cat: [0] * n_windows for cat in all_categories
    }
    for e in sim.event_log:
        idx = min(e.tick // window, n_windows - 1)
        series[category_of(e.type)][idx] += 1
    return windows, series
