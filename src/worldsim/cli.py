"""CLI entry point: python -m worldsim <command>."""

from __future__ import annotations

import argparse
import sys

from .db import DEFAULT_DB_PATH, WorldStore
from .simulation import DEFAULT_SETTLEMENT_COUNT, Simulation
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
    _add_simulate_args(sub)
    return parser


def _add_simulate_args(sub) -> None:
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
        "--size", type=int, default=DEFAULT_SIZE, help="Grid size (default 256)"
    )
    sim.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="SQLite database path"
    )
    sim.add_argument(
        "--no-save", action="store_true", help="Do not persist the final state"
    )


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
    for _ in range(args.ticks):
        sim.step()
        if sim.tick % args.report_interval == 0 or not any_alive:
            print()
            for s in settlements:
                print(f"  [{s.name}] {sim.status_line(s)}")
            routes = sim.active_routes()
            if routes:
                total = sum(r.transfers for r in routes)
                print(f"  trade: {len(routes)} active routes, {total} units moved")
            any_alive = any(s.is_alive for s in settlements)
            if not any_alive:
                print("All settlements have collapsed.")
                break

    if not args.no_save:
        store = WorldStore(args.db)
        try:
            world_id = store.save_world(
                world,
                sim.settlements,
                trade_routes=sim.trade_routes,
                ruins=sim.ruins,
                disaster_events=sim.disaster_events,
            )
        finally:
            store.close()
        print(f"\nSaved to {args.db} (world_id={world_id})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"generate": cmd_generate, "simulate": cmd_simulate}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
