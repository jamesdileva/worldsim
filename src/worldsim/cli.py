"""CLI entry point: python -m worldsim <command>."""

from __future__ import annotations

import argparse
import sys
import uuid

from .actions import Action, action_category
from .clock import describe
from .db import DEFAULT_DB_PATH, WorldStore
from .simulation import (
    DEFAULT_SETTLEMENT_COUNT,
    Simulation,
    simulation_from_state,
)
from .tiles import TerrainType
from .world import DEFAULT_SIZE, World


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worldsim",
        description="Autonomous AI civilization simulator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate a seeded world")
    gen.add_argument("--seed", type=int, required=True, help="World seed")
    gen.add_argument(
        "--size", type=int, default=DEFAULT_SIZE, help="Grid size (default 256)"
    )
    gen.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )
    gen.add_argument(
        "--no-save", action="store_true", help="Do not persist the generated world"
    )

    sim = sub.add_parser("simulate", help="Run a headless simulation")
    sim.add_argument("--seed", type=int, required=True, help="World seed")
    sim.add_argument("--ticks", type=int, default=1000, help="Ticks to run")
    sim.add_argument(
        "--settlements",
        type=int,
        default=DEFAULT_SETTLEMENT_COUNT,
        help="Number of settlements to spawn (default 3)",
    )
    sim.add_argument(
        "--report-interval", type=int, default=100, help="Ticks between status lines"
    )
    sim.add_argument(
        "--save-interval",
        type=int,
        default=500,
        help="Auto-save every N ticks (0 disables; default 500)",
    )
    sim.add_argument(
        "--world-id", default=None, help="Use a specific world id for saves"
    )
    sim.add_argument(
        "--agent",
        default="rulebased",
        choices=["rulebased"],
        help="Agent type driving settlements (default rulebased)",
    )
    sim.add_argument(
        "--size", type=int, default=DEFAULT_SIZE, help="Grid size (default 256)"
    )
    sim.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )
    sim.add_argument(
        "--no-save", action="store_true", help="Do not persist the final state"
    )

    save = sub.add_parser(
        "save", help="Deterministically re-run a seed and store it under an id"
    )
    save.add_argument("--seed", type=int, required=True, help="World seed")
    save.add_argument("--ticks", type=int, default=0, help="Ticks to advance")
    save.add_argument("--world-id", required=True, help="Id to store the world under")
    save.add_argument(
        "--settlements",
        type=int,
        default=DEFAULT_SETTLEMENT_COUNT,
        help="Number of settlements to spawn",
    )
    save.add_argument(
        "--size", type=int, default=DEFAULT_SIZE, help="Grid size (default 256)"
    )
    save.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )

    load = sub.add_parser("load", help="Restore a saved world and print its state")
    load.add_argument("--world-id", required=True, help="World id to restore")
    load.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )

    step = sub.add_parser("step", help="Advance a saved world by N ticks")
    step.add_argument("--world-id", required=True, help="World id to advance")
    step.add_argument("--ticks", type=int, default=1, help="Ticks to advance")
    step.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )

    god = sub.add_parser("god", help="Apply a God Mode intervention")
    god.add_argument("--world-id", required=True, help="World id to intervene in")
    god.add_argument(
        "--action",
        required=True,
        choices=["smite", "bless_food", "bless_wood", "bless_stone", "destroy"],
        help="Intervention to apply",
    )
    god.add_argument(
        "--settlement-index", type=int, default=0, help="Target settlement index"
    )
    god.add_argument("--amount", type=int, default=5, help="Magnitude of intervention")
    god.add_argument("--x", type=int, default=None, help="Tile x (destroy action)")
    god.add_argument("--y", type=int, default=None, help="Tile y (destroy action)")
    god.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )

    events = sub.add_parser("events", help="List God Mode events for a world")
    events.add_argument("--world-id", required=True)
    events.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )

    bench = sub.add_parser(
        "benchmark", help="Run the rule-based agent on benchmark worlds"
    )
    bench.add_argument(
        "--first-seed", type=int, default=50000, help="First benchmark seed"
    )
    bench.add_argument(
        "--num-worlds", type=int, default=10, help="Number of benchmark worlds"
    )
    bench.add_argument(
        "--ticks", type=int, default=5000, help="Ticks per world (default 5000)"
    )
    bench.add_argument(
        "--settlements",
        type=int,
        default=DEFAULT_SETTLEMENT_COUNT,
        help="Settlements per world",
    )
    bench.add_argument(
        "--size", type=int, default=DEFAULT_SIZE, help="Grid size (default 256)"
    )
    bench.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )
    return parser


def cmd_generate(args: argparse.Namespace) -> int:
    world = World(seed=args.seed, size=args.size)
    breakdown = world.terrain_breakdown()
    total = args.size * args.size

    print(f"Generated world (seed={args.seed}, size={args.size}x{args.size})")
    print("\nTerrain breakdown:")
    for tt in TerrainType:
        count = breakdown[tt]
        print(f"  {tt.name:<10} {count:>7}  ({100.0 * count / total:.1f}%)")

    yields = world.resource_yield()
    print("\nBase resource yields:")
    for res, amount in yields.items():
        print(f"  {res:<6} {amount}")

    if not args.no_save:
        store = WorldStore(args.db)
        try:
            world_id = store.save_world(world)
        finally:
            store.close()
        print(f"\nSaved to {args.db} (world_id={world_id})")

    print("\nASCII map:")
    print(world.render_ascii())
    return 0


def _autosave(store: WorldStore, args, sim: Simulation, world_id: str | None) -> str:
    """Persist current state under the given id (or a fresh one)."""
    kwargs = dict(
        settlements=sim.settlements,
        trade_routes=sim.trade_routes,
        ruins=sim.ruins,
        disaster_events=sim.disaster_events,
        relations=sim.relations,
        contested=sim.contested,
        building_debuffs=sim.building_debuffs,
        event_log=sim.event_log,
    )
    return store.save_world_with_id(
        world_id if world_id is not None else str(uuid.uuid4()), sim.world, **kwargs
    )


def cmd_simulate(args: argparse.Namespace) -> int:
    world = World(seed=args.seed, size=args.size)
    sim = Simulation(world)
    settlements = sim.spawn_settlements(count=args.settlements)

    print(
        f"Simulating seed={args.seed} for {args.ticks} ticks "
        f"({len(settlements)} settlements)"
    )
    for s in settlements:
        print(
            f"  '{s.name}' at {s.spawn_x},{s.spawn_y}"
        )
    any_alive = True
    store = None if args.no_save else WorldStore(args.db)
    last_flush = 0
    try:
        for _ in range(args.ticks):
            sim.step()
            if store is not None and sim.tick - last_flush >= 500:
                sim.flush_experiences(store)
                last_flush = sim.tick
            if (
                store is not None
                and args.save_interval > 0
                and sim.tick % args.save_interval == 0
            ):
                wid = _autosave(store, args, sim, args.world_id)
                print(f"[auto-save] tick {sim.tick} -> {wid}")
            if sim.tick % args.report_interval == 0 or not any_alive:
                print()
                for s in settlements:
                    print(f"  [{s.name}] {sim.status_line(s)}")
                routes = sim.active_routes()
                if routes:
                    total = sum(r.transfers for r in routes)
                    print(
                        f"  trade: {len(routes)} active routes, "
                        f"{total} units moved"
                    )
                print(f"  clock: {describe(sim.tick)}")
                any_alive = any(s.is_alive for s in settlements)
                if not any_alive:
                    print("All settlements have collapsed.")
                    break
        if store is not None:
            sim.flush_experiences(store)
            wid = _autosave(store, args, sim, args.world_id)
            store.insert_world_events(sim.event_log)
            print(f"\nSaved to {args.db} (world_id={wid})")
    finally:
        if store is not None:
            store.close()

    if sim.action_counts:
        print("\nAgent action distribution:")
        named = sorted(
            ((Action(k), v) for k, v in sim.action_counts.items()),
            key=lambda kv: -kv[1],
        )
        for action, count in named[:10]:
            print(f"  {action.name:<24} {count:>6}  ({action_category(action)})")
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    """Deterministically re-run a seed and persist under a fixed id."""
    world = World(seed=args.seed, size=args.size)
    sim = Simulation(world)
    sim.spawn_settlements(count=args.settlements)
    sim.run(args.ticks)
    store = WorldStore(args.db)
    try:
        store.save_world_with_id(
            args.world_id,
            sim.world,
            settlements=sim.settlements,
            trade_routes=sim.trade_routes,
            ruins=sim.ruins,
            disaster_events=sim.disaster_events,
            relations=sim.relations,
            contested=sim.contested,
            building_debuffs=sim.building_debuffs,
            event_log=sim.event_log,
        )
        store.insert_world_events(sim.event_log)
    finally:
        store.close()
    print(f"Saved world {args.world_id} (seed={args.seed}, tick={sim.tick})")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    store = WorldStore(args.db)
    try:
        (
            world,
            settlements,
            routes,
            ruins,
            disasters,
            relations,
            contested,
            debuffs,
            events,
        ) = store.load_latest_snapshot(args.world_id)
    finally:
        store.close()
    alive = [s for s in settlements if s.is_alive]
    print(f"Loaded world {args.world_id}: {describe(world.tick)}")
    print(f"  settlements: {len(alive)} alive / {len(settlements)} ever")
    for s in settlements:
        status = f"pop {s.population}" if s.is_alive else "DEAD"
        print(f"    [{s.name}] {status}")
    print(f"  trade routes: {sum(1 for r in routes if r.active)} active")
    print(f"  ruins: {len(ruins)}, disasters recorded: {len(disasters)}")
    hostile_pairs = sum(
        1 for _, _, score in relations.pairs() if score < -25
    )
    print(
        f"  relations: {len(relations.pairs())} tracked pairs, "
        f"{hostile_pairs} hostile"
    )
    print(f"  contested tiles: {len(contested)}, active debuffs: {len(debuffs)}")
    raids = [e for e in events if e.type == "raid"]
    print(f"  events logged: {len(events)} ({len(raids)} raids)")
    breakdown = world.terrain_breakdown()
    print(
        "  terrain: "
        + ", ".join(f"{tt.name}={breakdown[tt]}" for tt in TerrainType)
    )
    return 0


def cmd_step(args: argparse.Namespace) -> int:
    store = WorldStore(args.db)
    try:
        (
            world,
            settlements,
            routes,
            ruins,
            disasters,
            relations,
            contested,
            debuffs,
            events,
        ) = store.load_latest_snapshot(args.world_id)
        sim = simulation_from_state(
            world,
            settlements,
            routes,
            ruins,
            disasters,
            relations=relations,
            contested=contested,
            building_debuffs=debuffs,
            event_log=events,
        )
        before_tick = sim.tick
        sim.run(args.ticks)
        store.update_world(
            args.world_id,
            sim.world,
            settlements=sim.settlements,
            trade_routes=sim.trade_routes,
            ruins=sim.ruins,
            disaster_events=sim.disaster_events,
            relations=sim.relations,
            contested=sim.contested,
            building_debuffs=sim.building_debuffs,
            event_log=sim.event_log,
        )
        store.insert_world_events(sim.event_log)
    finally:
        store.close()
    print(
        f"Stepped world {args.world_id} from tick {before_tick} "
        f"to {sim.tick} ({describe(sim.tick)})"
    )
    for s in sim.settlements:
        print(f"  [{s.name}] {sim.status_line(s)}")
    return 0


def cmd_god(args: argparse.Namespace) -> int:
    store = WorldStore(args.db)
    try:
        (
            world,
            settlements,
            routes,
            ruins,
            disasters,
            relations,
            contested,
            debuffs,
            events,
        ) = store.load_latest_snapshot(args.world_id)
        sim = simulation_from_state(
            world,
            settlements,
            routes,
            ruins,
            disasters,
            relations=relations,
            contested=contested,
            building_debuffs=debuffs,
            event_log=events,
        )
        target = None
        before: dict | None = None
        after: dict | None = None
        if args.action == "destroy":
            if args.x is None or args.y is None:
                print("destroy requires --x and --y", file=sys.stderr)
                return 2
            before, after = sim.god_destroy_improvement(args.x, args.y)
            target = f"tile({args.x},{args.y})"
        else:
            if not (0 <= args.settlement_index < len(sim.settlements)):
                print("invalid --settlement-index", file=sys.stderr)
                return 2
            target = sim.settlements[args.settlement_index].name
            if args.action == "smite":
                before, after = sim.god_smite(
                    sim.settlements[args.settlement_index], args.amount
                )
            elif args.action == "bless_food":
                before, after = sim.god_bless_resources(
                    sim.settlements[args.settlement_index], "food", args.amount
                )
            elif args.action == "bless_wood":
                before, after = sim.god_bless_resources(
                    sim.settlements[args.settlement_index], "wood", args.amount
                )
            elif args.action == "bless_stone":
                before, after = sim.god_bless_resources(
                    sim.settlements[args.settlement_index], "stone", args.amount
                )
        store.log_god_event(
            args.world_id, sim.tick, args.action, target, before, after
        )
        store.update_world(
            args.world_id,
            sim.world,
            settlements=sim.settlements,
            trade_routes=sim.trade_routes,
            ruins=sim.ruins,
            disaster_events=sim.disaster_events,
            relations=sim.relations,
            contested=sim.contested,
            building_debuffs=sim.building_debuffs,
            event_log=sim.event_log,
        )
    finally:
        store.close()
    print(f"God event '{args.action}' applied at {describe(sim.tick)}")
    print(f"  target: {target}")
    print(f"  before: {before}")
    print(f"  after:  {after}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    store = WorldStore(args.db)
    try:
        events = store.get_god_events(args.world_id)
    finally:
        store.close()
    print(f"God events for {args.world_id}: {len(events)}")
    for e in events:
        print(f"  tick {e['tick']:>6} | {e['action_type']:<12} | {e['target']}")
        print(f"    {e['before_state']} -> {e['after_state']}")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run the rule-based baseline on benchmark worlds and log metrics."""
    store = WorldStore(args.db)
    try:
        print(
            f"Benchmarking {args.num_worlds} worlds "
            f"(seeds {args.first_seed}+), {args.ticks} ticks each"
        )
        survivors_total = 0
        worlds = 0
        for i in range(args.num_worlds):
            seed = args.first_seed + i
            world = World(seed=seed, size=args.size)
            sim = Simulation(world)
            settlements = sim.spawn_settlements(count=args.settlements)
            peak = 0
            survival_ticks: list[int] = []
            for tick in range(1, args.ticks + 1):
                sim.step()
                for s in settlements:
                    if s.is_alive:
                        peak = max(peak, s.population)
                        survival_ticks.append(tick)
                if not any(s.is_alive for s in settlements):
                    break
            alive = [s for s in settlements if s.is_alive]
            survivors = len(alive)
            survivors_total += 1 if survivors > 0 else 0
            worlds += 1
            inv = lambda s, r: sum(  # noqa: E731
                x.resource_inventory.get(r, 0.0) for x in settlements
            )
            metrics = {
                "seed": seed,
                "agent_type": "rulebased",
                "ticks_requested": args.ticks,
                "settlements": len(settlements),
                "survivors": survivors,
                "peak_population": peak,
                "final_population": sum(
                    s.population for s in settlements
                ),
                "avg_survival_ticks": (
                    sum(survival_ticks) / len(survival_ticks)
                    if survival_ticks
                    else 0.0
                ),
                "food_final": sum(s.food_stock for s in settlements),
                "wood_final": inv(None, "wood"),
                "stone_final": inv(None, "stone"),
            }
            store.insert_benchmark_run(metrics)
            print(
                f"  seed {seed}: survivors {survivors}/{len(settlements)}, "
                f"peak pop {peak}, avg survival "
                f"{metrics['avg_survival_ticks']:.0f} ticks"
            )
    finally:
        store.close()
    rate = 100.0 * survivors_total / max(worlds, 1)
    print(
        f"\nWorld survival rate (>=1 settlement alive): "
        f"{survivors_total}/{worlds} ({rate:.0f}%)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "generate": cmd_generate,
        "simulate": cmd_simulate,
        "save": cmd_save,
        "load": cmd_load,
        "step": cmd_step,
        "god": cmd_god,
        "events": cmd_events,
        "benchmark": cmd_benchmark,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
