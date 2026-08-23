"""Settlement/world state summarization for LLM prompts (Sprint 26).

Contract:
- Pure functions of (sim state, tick): identical state -> byte-identical
  text. No uuid4, no wall-clock, dict iteration always sorted.
- Two verbosity tiers: "tiny" (~1 line per settlement) and "full"
  (multi-section). Tiny lines target the ~200-token budget.
- Missing/None fields render as explicit placeholders ("unknown"), never
  crash. Dead settlements render a DEAD line instead of stats.
"""

from __future__ import annotations

from worldsim.buildings import BUILDING_SPECS, BuildingType
from worldsim.clock import describe as _describe_tick

TIER_TINY = "tiny"
TIER_FULL = "full"

UNKNOWN = "unknown"
# Rough prompt-budget heuristic: ~4 chars per token.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _num(value, fmt: str) -> str:
    """Format a number deterministically; None/non-numeric -> placeholder."""
    if value is None or isinstance(value, bool) or not isinstance(
        value, (int, float)
    ):
        return UNKNOWN
    return format(value, fmt)


def summarize_settlement(sim, settlement, tier: str = TIER_FULL,
                         max_events: int = 5) -> str:
    """Deterministic text summary of one settlement."""
    if not settlement.is_alive:
        died = settlement.destroyed_at_tick
        died_txt = _num(died, "d") if died is not None else UNKNOWN
        return f"{settlement.name}: DEAD (population 0, died tick {died_txt})"

    personality = settlement.personality or {}
    archetype = personality.get("archetype") or UNKNOWN

    if tier == TIER_TINY:
        return _one_liner(sim, settlement, archetype)

    lines: list[str] = []
    lines.append(f"Settlement {settlement.name} [{archetype}]")
    lines.append(
        f"  population={settlement.population} "
        f"food={_num(settlement.food_stock, '.1f')} "
        f"net_food={_fmt_signed(settlement.net_food_rate)} /tick "
        f"happiness={_num(settlement.happiness, '.2f')}"
    )
    lines.append(f"  strategy={settlement.strategy_label} "
                 f"reputation={_num(sim.diplomacy.rep(settlement.id), '.0f')}")
    lines.append(
        f"  military: army={_num(settlement.army, '.1f')} "
        f"fort={settlement.fort_level} "
        f"siege_progress={settlement.siege_progress}"
    )
    lines.append(
        f"  era={settlement.era} "
        f"research={_num(settlement.research_points, '.0f')} "
        f"technologies: "
        f"{', '.join(settlement.technologies) or 'none'}"
    )

    resources = sorted((settlement.resource_inventory or {}).items())
    res_txt = ", ".join(f"{k}={_num(v, '.1f')}" for k, v in resources)
    lines.append(f"  resources: {res_txt or 'none'}")

    counts = sim.buildings_of(settlement)
    bld = ", ".join(
        f"{BUILDING_SPECS[bt].name.lower()}={counts[bt]}"
        for bt in BuildingType
        if counts.get(bt)
    )
    lines.append(f"  buildings: {bld or 'none'}")
    territory = sim.territory_of(settlement)
    roads = len(sim.roads_of(settlement))
    lines.append(f"  territory={len(territory)} tiles, roads={roads}")
    queue = list(settlement.build_queue or [])
    lines.append(f"  build_queue: {', '.join(queue) if queue else 'empty'}")

    lines.append(_relations_section(sim, settlement))
    events = _recent_events(sim, settlement.id, max_events)
    if events:
        lines.append("  recent events:")
        lines.extend(f"    {e}" for e in events)
    else:
        lines.append("  recent events: none")
    return "\n".join(lines)


def summarize_world(sim, tier: str = TIER_FULL,
                    max_events_per_settlement: int = 5) -> str:
    """Top-level stats + per-settlement summaries (one-liners in tiny tier)."""
    living = [s for s in sim.settlements if s.is_alive]
    header = (
        f"World seed={sim.world.seed} size={sim.world.size} "
        f"tick={sim.tick} ({_describe_tick(sim.tick)}) | "
        f"settlements: {len(living)} alive / {len(sim.settlements)} total"
    )

    sections = [header]

    from .markets import market_prices

    price_txt = ", ".join(f"{r}={p}" for r, p in
                          sorted(market_prices(sim).items()))
    sections.append(f"Market prices per unit: {price_txt}")

    highways_done = sum(
        1 for p in sim.highway_projects if p.completed)
    highways_wip = len(sim.highway_projects) - highways_done
    sections.append(
        f"Highways: {highways_done} operational, {highways_wip} under "
        f"construction"
    )

    from .treaties import federations

    name_by_id = {s.id: s.name for s in sim.settlements}
    treaty_parts = sorted(
        f"{name_by_id.get(t.party_a, UNKNOWN)}-{name_by_id.get(t.party_b, UNKNOWN)}"
        f"({'+'.join(sorted(t.clauses))})"
        for t in sim.treaties
    )
    sections.append(
        f"Treaties ({len(sim.treaties)}): "
        + (", ".join(treaty_parts) or "none")
    )
    fed_parts = [
        "+".join(sorted(name_by_id.get(sid, UNKNOWN) for sid in fed))
        for fed in federations(sim)
    ]
    sections.append("Federations: " + (", ".join(fed_parts) or "none"))

    wars = _war_lines(sim)
    if wars:
        sections.append("Wars: " + "; ".join(wars))
    disasters = [
        d.type.name.lower() for d in sim.active_disasters()
    ]
    if disasters:
        sections.append("Active disasters: " + ", ".join(disasters))

    routes = sim.active_routes()
    name_by_id = {s.id: s.name for s in sim.settlements}
    route_parts = sorted(
        f"{name_by_id.get(r.source_id, UNKNOWN)}->{name_by_id.get(r.dest_id, UNKNOWN)}"
        for r in routes
    )
    sections.append(
        f"Trade routes ({len(routes)}): " + (", ".join(route_parts) or "none")
    )

    ordered = sorted(living, key=lambda s: s.name)
    for s in ordered:
        sections.append(summarize_settlement(
            sim, s, tier=tier, max_events=max_events_per_settlement))
    return "\n".join(sections)


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------

def _fmt_signed(value) -> str:
    return _num(value, "+.1f")


def _one_liner(sim, settlement, archetype: str) -> str:
    counts = sim.buildings_of(settlement)
    bld = "/".join(str(counts.get(bt, 0)) for bt in BuildingType)

    parts = [
        f"{settlement.name}[{archetype}|{settlement.strategy_label}|"
        f"era{settlement.era}]",
        f"pop={settlement.population}",
        f"food={_num(settlement.food_stock, '.0f')}",
        f"net={_fmt_signed(settlement.net_food_rate)}",
        f"happy={_num(settlement.happiness, '.2f')}",
        f"terr={len(sim.territory_of(settlement))}",
        f"bld(F/S/M/G)={bld}",
        f"mil(army/fort/siege)="
        f"{_num(settlement.army, '.0f')}/{settlement.fort_level}/"
        f"{settlement.siege_progress}",
    ]

    hostile, allied, at_war = [], [], []
    others = sorted(
        (n for n in sim.neighbors_of(settlement) if n.is_alive),
        key=lambda n: n.name,
    )
    for other in others:
        if sim.diplomacy.at_war(settlement.id, other.id):
            at_war.append(other.name)
        elif sim.diplomacy.is_allied(settlement.id, other.id):
            allied.append(other.name)
        elif sim.relations.is_hostile(settlement.id, other.id):
            hostile.append(other.name)
    if hostile:
        parts.append("hostile=" + ",".join(hostile))
    if allied:
        parts.append("allies=" + ",".join(allied))
    if at_war:
        parts.append("WAR=" + ",".join(at_war))
    return ", ".join(parts)


def _relations_section(sim, settlement) -> str:
    others = sorted(
        (n for n in sim.neighbors_of(settlement) if n.is_alive),
        key=lambda n: n.name,
    )
    if not others:
        return "  relations: none known"
    entries = []
    for other in others:
        score = sim.relations.score(settlement.id, other.id)
        label = sim.relations.label(settlement.id, other.id)
        if sim.diplomacy.at_war(settlement.id, other.id):
            label = "AT WAR"
        elif sim.diplomacy.is_allied(settlement.id, other.id):
            label = "allied"
        entries.append(f"{other.name}({label}, {_num(score, '+.0f')})")
    return "  relations: " + ", ".join(entries)


def _war_lines(sim) -> list[str]:
    name_by_id = {s.id: s.name for s in sim.settlements}
    seen: set[frozenset] = set()
    lines: list[str] = []
    for settlement in sorted(sim.settlements, key=lambda s: s.name):
        for pair in sim.diplomacy.wars_of(settlement.id):
            if pair in seen:
                continue
            seen.add(pair)
            a, b = sorted(pair)
            lines.append(
                f"{name_by_id.get(a, UNKNOWN)} vs "
                f"{name_by_id.get(b, UNKNOWN)}"
            )
    return sorted(lines)


def _recent_events(sim, settlement_id: str, limit: int) -> list[str]:
    picked = []
    for event in reversed(sim.event_log):
        if settlement_id in event.actor_ids:
            picked.append(f"[t{event.tick}] {event.type}: {event.description}")
            if len(picked) >= limit:
                break
    return list(reversed(picked))
