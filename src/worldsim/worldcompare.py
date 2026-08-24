"""World comparison (Sprint 49): structural diff between two worlds.

Built for branch timelines (Sprint 43): compare the origin world against
a branch to see exactly where histories diverged — or compare entirely
different seeds.

Contract:
- Pure functions of the two simulations; identical inputs produce
  byte-identical reports and charts.
- Event divergence keys are (tick, type, description) tuples: stable
  across save/load since all three persist inside state_json.
"""

from __future__ import annotations


def _settlement_rows(sim):
    rows = {}
    for s in sim.settlements:
        rows[s.name] = {
            "alive": s.is_alive,
            "population": s.population,
            "era": s.era,
            "technologies": sorted(s.technologies),
            "army": round(s.army, 4),
            "fort_level": s.fort_level,
            "territory": len(sim.territory_of(s)),
            "food_stock": round(s.food_stock, 3),
            "resources": {
                k: round(v, 3)
                for k, v in sorted(s.resource_inventory.items())
            },
        }
    return rows


def _event_keys(sim):
    return {
        (e.tick, e.type, e.description) for e in sim.event_log
    }


def _counts(sim):
    return {
        "wars": len(sim.diplomacy.wars),
        "alliances": len(sim.diplomacy.alliances),
        "treaties": len(getattr(sim, "treaties", [])),
        "highway_projects": len(getattr(sim, "highway_projects", [])),
        "contamination_zones": sum(
            1 for z in getattr(sim, "contamination_zones", [])
            if z.is_active(sim.tick)
        ),
        "trade_routes_active": len(
            [r for r in sim.trade_routes if r.active]),
        "ruins": len(getattr(sim, "ruins", [])),
    }


def compare_worlds(sim_a, sim_b) -> dict:
    """Structural comparison of two simulations at their current ticks."""
    rows_a = _settlement_rows(sim_a)
    rows_b = _settlement_rows(sim_b)

    settlement_diff = []
    for name in sorted(set(rows_a) | set(rows_b)):
        ra, rb = rows_a.get(name), rows_b.get(name)
        if ra is None:
            settlement_diff.append({
                "name": name, "only_in": "b", "changed_fields": ["exists"],
            })
            continue
        if rb is None:
            settlement_diff.append({
                "name": name, "only_in": "a", "changed_fields": ["exists"],
            })
            continue
        changed = [
            field for field in sorted(ra)
            if ra[field] != rb[field]
        ]
        if changed:
            settlement_diff.append({
                "name": name,
                "only_in": None,
                "changed_fields": changed,
            })

    keys_a = _event_keys(sim_a)
    keys_b = _event_keys(sim_b)
    events_only_a = sorted(keys_a - keys_b)
    events_only_b = sorted(keys_b - keys_a)

    counts_a = _counts(sim_a)
    counts_b = _counts(sim_b)

    identical = (
        not settlement_diff
        and not events_only_a
        and not events_only_b
        and counts_a == counts_b
    )
    return {
        "meta": {
            "tick_a": sim_a.tick,
            "tick_b": sim_b.tick,
            "seed_a": sim_a.world.seed,
            "seed_b": sim_b.world.seed,
        },
        "identical": identical,
        "settlements": {
            "only_in_a": sorted(n for d in settlement_diff
                                if d["only_in"] == "a" for n in [d["name"]]),
            "only_in_b": sorted(n for d in settlement_diff
                                if d["only_in"] == "b" for n in [d["name"]]),
            "changed": [
                {"name": d["name"], "fields": d["changed_fields"]}
                for d in settlement_diff if d["only_in"] is None
            ],
        },
        "counts": {"a": counts_a, "b": counts_b,
                   "differences": sorted(
                       k for k in set(counts_a) | set(counts_b)
                       if counts_a.get(k) != counts_b.get(k))},
        "events": {
            "only_in_a_count": len(events_only_a),
            "only_in_b_count": len(events_only_b),
            "only_in_a_sample": [
                {"tick": t, "type": ty, "description": d}
                for t, ty, d in events_only_a[:20]
            ],
            "only_in_b_sample": [
                {"tick": t, "type": ty, "description": d}
                for t, ty, d in events_only_b[:20]
            ],
        },
    }


def render_compare_markdown(comparison: dict) -> str:
    """Markdown report for a comparison result."""
    meta = comparison["meta"]
    lines = [
        "# World Comparison",
        "",
        f"- World A: tick {meta['tick_a']}, seed {meta['seed_a']}",
        f"- World B: tick {meta['tick_b']}, seed {meta['seed_b']}",
        f"- Identical: **{comparison['identical']}**",
        "",
        "## Settlement changes",
        "",
    ]
    s = comparison["settlements"]
    if not (s["only_in_a"] or s["only_in_b"] or s["changed"]):
        lines.append("None.")
    else:
        if s["only_in_a"]:
            lines.append(f"- Only in A: {', '.join(s['only_in_a'])}")
        if s["only_in_b"]:
            lines.append(f"- Only in B: {', '.join(s['only_in_b'])}")
        for change in s["changed"]:
            lines.append(
                f"- {change['name']}: differs in "
                f"{', '.join(change['fields'])}"
            )

    lines += ["", "## World counters", ""]
    counts = comparison["counts"]
    keys = sorted(counts["a"])
    lines.append("| Counter | A | B |")
    lines.append("|---|---|---|")
    for key in keys:
        marker = " *" if counts["a"].get(key) != counts["b"].get(key) else ""
        lines.append(
            f"| {key} | {counts['a'].get(key)} "
            f"| {counts['b'].get(key)}{marker} |"
        )

    events = comparison["events"]
    lines += [
        "",
        f"## Events only in A: {events['only_in_a_count']} | "
        f"only in B: {events['only_in_b_count']} (first 20 listed)",
        "",
    ]
    for direction in ("only_in_a_sample", "only_in_b_sample"):
        for e in events[direction]:
            lines.append(
                f"- [{direction}] t{e['tick']} {e['type']}: "
                f"{e['description']}"
            )
    return "\n".join(lines)


def export_compare_chart(sim_a, sim_b, path, dpi: int = 100) -> str:
    """Grouped bars (population / territory / army) per shared settlement.

    Byte-deterministic like every other export here."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    names = sorted(
        {s.name for s in sim_a.settlements}
        & {s.name for s in sim_b.settlements}
    )
    by_name_a = {s.name: s for s in sim_a.settlements}
    by_name_b = {s.name: s for s in sim_b.settlements}

    def value(sim, name, metric):
        settlement = (by_name_a[name] if sim is sim_a else by_name_b[name])
        if metric == "territory":
            return len(sim.territory_of(settlement))
        return getattr(settlement, metric)

    metrics = ("population", "territory", "army")
    fig, axes = plt.subplots(1, 3, figsize=(9.0, 2.8), dpi=dpi)
    width = 0.38
    xs = np.arange(len(names))
    for ax, metric in zip(axes, metrics):
        vals_a = [value(sim_a, n, metric) for n in names]
        vals_b = [value(sim_b, n, metric) for n in names]
        ax.bar(xs - width / 2, vals_a, width, label="A", color="#4169aa")
        ax.bar(xs + width / 2, vals_b, width, label="B", color="#b22222")
        ax.set_xticks(xs)
        ax.set_xticklabels(names, fontsize=6, rotation=30)
        ax.set_title(metric, fontsize=8)
        ax.tick_params(labelsize=6)
        ax.legend(fontsize=5)
    fig.suptitle("World comparison", fontsize=9)
    fig.tight_layout()
    out = str(path)
    fig.savefig(out, dpi=dpi)
    plt.close(fig)
    return out
