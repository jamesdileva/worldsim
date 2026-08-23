"""CLI entry point: python -m worldsim <command>."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

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

    rl = sub.add_parser(
        "rl",
        help="RL utilities (headless episode runs over WorldSimEnv)",
    )
    rl_sub = rl.add_subparsers(dest="rl_command", required=True)
    run = rl_sub.add_parser("run", help="Run headless episodes")
    run.add_argument("--episodes", type=int, default=10, help="Episodes to run")
    run.add_argument("--seed", type=int, default=42, help="Base seed")
    run.add_argument("--ticks", type=int, default=1000, help="Max ticks per episode")
    run.add_argument(
        "--settlements", type=int, default=5, help="Settlements per world"
    )
    run.add_argument(
        "--plot",
        default=None,
        help="Optional output .png path for a reward-over-time plot",
    )
    train_p = rl_sub.add_parser("train", help="Train PPO on WorldSimEnv")
    train_p.add_argument("--timesteps", type=int, default=50_000,
                         help="Total training timesteps")
    train_p.add_argument("--seed", type=int, default=42, help="Base seed")
    train_p.add_argument("--size", type=int, default=64,
                        help="World size for training (small = faster)")
    train_p.add_argument("--settlements", type=int, default=3)
    train_p.add_argument("--max-ticks", type=int, default=1000)
    train_p.add_argument(
        "--generation", default="gen1", help="Checkpoint generation label"
    )
    train_p.add_argument(
        "--save-dir", default="data/world_sim/policies",
        help="Directory for checkpoints and logs",
    )
    train_p.add_argument(
        "--parallel", type=int, default=1,
        help="Number of parallel simulation workers (SubprocVecEnv)",
    )
    train_p.add_argument(
        "--compare", action="store_true",
        help="Run sequential-vs-parallel speedup benchmark and exit",
    )
    eval_p = rl_sub.add_parser(
        "evaluate", help="Paired evaluation: trained policy vs rule-based"
    )
    eval_p.add_argument("--model", default=None,
                       help="Path to SB3 checkpoint (.zip)")
    eval_p.add_argument("--policy-id", default=None,
                       help="Resolve checkpoint by registered generation "
                            "(e.g., gen1); overrides --model")
    eval_p.add_argument("--worlds", type=int, default=10)
    eval_p.add_argument("--first-seed", type=int, default=50_000)
    eval_p.add_argument("--ticks", type=int, default=3000)
    eval_p.add_argument("--size", type=int, default=256)
    eval_p.add_argument("--settlements", type=int, default=5)
    eval_p.add_argument(
        "--difficulty", choices=["normal", "hard"], default="normal",
        help="Hard worlds double disasters and halve passive gathering",
    )
    eval_p.add_argument(
        "--report", default=None,
        help="Write a markdown comparison report to this path (.md)",
    )
    eval_p.add_argument(
        "--chart", default=None,
        help="Optional bar-chart .png accompanying the report",
    )

    cmp_p = rl_sub.add_parser(
        "compare",
        help="Evaluate two policy generations vs baseline and report deltas",
    )
    cmp_p.add_argument("--gen-a", required=True, help="Generation label A")
    cmp_p.add_argument("--gen-b", required=True, help="Generation label B")
    cmp_p.add_argument("--worlds", type=int, default=10)
    cmp_p.add_argument("--first-seed", type=int, default=50_000)
    cmp_p.add_argument("--ticks", type=int, default=3000)
    cmp_p.add_argument("--size", type=int, default=256)
    cmp_p.add_argument("--settlements", type=int, default=5)
    cmp_p.add_argument(
        "--head-to-head", action="store_true",
        help="True policy-vs-policy competition in shared worlds "
             "(Sprint 22) instead of two baseline-paired evaluations",
    )

    dash = rl_sub.add_parser(
        "dashboard",
        help="Learning-curve dashboard across policy generations (Sprint 18)",
    )
    dash.add_argument("--gens", default="gen1,gen2,gen3",
                      help="Comma-separated generation labels in order")
    dash.add_argument(
        "--metric", default="survival",
        choices=["survival", "reward", "peak_population"],
        help="Primary metric for the learning curve",
    )
    dash.add_argument("--worlds", type=int, default=10)
    dash.add_argument("--first-seed", type=int, default=50_000)
    dash.add_argument("--ticks", type=int, default=3000)
    dash.add_argument("--size", type=int, default=256)
    dash.add_argument("--settlements", type=int, default=5)
    dash.add_argument(
        "--plot", default=None,
        help="Optional learning-curve .png output path",
    )

    evo = rl_sub.add_parser(
        "evolve",
        help="Population-based generational training (Sprint 19)",
    )
    evo.add_argument("--population", type=int, default=4,
                     help="Fresh candidates per generation")
    evo.add_argument("--generations", type=int, default=2)
    evo.add_argument("--mutants", type=int, default=2,
                     help="Gaussian-noise children of the champion per gen")
    evo.add_argument("--mutation-strength", type=float, default=0.05)
    evo.add_argument("--eval-ticks", type=int, default=300,
                     help="Rollout ticks used to score mutants")
    evo.add_argument("--timesteps-per-candidate", type=int, default=4096)
    evo.add_argument("--seed-base", type=int, default=1000,
                     help="Base seed; each candidate gets seed_base + offsets")
    evo.add_argument("--size", type=int, default=64)
    evo.add_argument("--settlements", type=int, default=3)
    evo.add_argument("--max-ticks", type=int, default=500)
    evo.add_argument("--parallel", type=int, default=1,
                     help="Workers per candidate training run")
    evo.add_argument(
        "--curriculum", dest="curriculum", action="store_true",
        default=True,
        help="Weight next generation's candidate worlds toward seeds the "
             "champion scored below its own mean (Sprint 21)",
    )
    evo.add_argument(
        "--no-curriculum", dest="curriculum", action="store_false",
        help="Disable failure-weighted curricula",
    )
    evo.add_argument(
        "--eval-seeds", type=int, default=3,
        help="Evaluation seed-set size used for champion scoring",
    )
    evo.add_argument(
        "--strategy-priors", default=None,
        help="Path to a strategy_priors.json (default: standard policies "
             "dir path; missing file = no priors)",
    )

    disc = rl_sub.add_parser(
        "discover",
        help="Cluster champion rollouts into emergent strategies (Sprint 23)",
    )
    disc.add_argument("--gens", default="gen1",
                      help="Comma-separated generation labels to probe")
    disc.add_argument("--worlds-per-gen", type=int, default=2,
                      help="Probe worlds per generation")
    disc.add_argument("--ticks", type=int, default=600,
                      help="Ticks per probe world")
    disc.add_argument("--size", type=int, default=48)
    disc.add_argument("--settlements", type=int, default=3)
    disc.add_argument("--clusters", type=int, default=3)
    disc.add_argument(
        "--novelty-threshold", type=float, default=0.55,
        help="Centroid distance beyond which a cluster counts as novel",
    )
    return parser
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
        diplomacy=sim.diplomacy,
        strategy_memory=sim.strategy_memory,
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
                dist = sim.strategy_distribution()
                if dist:
                    print(f"  strategies: {dist}")
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
        diplomacy=sim.diplomacy,
        strategy_memory=sim.strategy_memory,
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
            diplomacy,
            strategy_memory,
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
    wars = len(diplomacy.wars)
    alliances = len(diplomacy.alliances)
    offers = sum(len(v) for v in diplomacy.peace_offers.values())
    print(
        f"  diplomacy: {wars} wars, {alliances} alliances, {offers} live offers"
    )
    raids = [e for e in events if e.type == "raid"]
    diplo_events = [e for e in events if e.type in ("war", "peace", "peace_offer", "alliance")]
    print(
        f"  events logged: {len(events)} ({len(raids)} raids, "
        f"{len(diplo_events)} diplomatic)"
    )
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
            diplomacy,
            strategy_memory,
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
            diplomacy=diplomacy,
            strategy_memory=strategy_memory,
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
        diplomacy=sim.diplomacy,
        strategy_memory=sim.strategy_memory,
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
            diplomacy,
            strategy_memory,
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
            diplomacy=diplomacy,
            strategy_memory=strategy_memory,
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
        diplomacy=sim.diplomacy,
        strategy_memory=sim.strategy_memory,
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
            distinct_strategies = len(sim.strategy_distribution())
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
                f"{metrics['avg_survival_ticks']:.0f} ticks, "
                f"distinct strategies {distinct_strategies}"
            )
    finally:
        store.close()
    rate = 100.0 * survivors_total / max(worlds, 1)
    print(
        f"\nWorld survival rate (>=1 settlement alive): "
        f"{survivors_total}/{worlds} ({rate:.0f}%)"
    )
    return 0


def cmd_rl(args: argparse.Namespace) -> int:
    if args.rl_command == "train":
        return _cmd_rl_train(args)
    if args.rl_command == "run":
        return _cmd_rl_run(args)
    if args.rl_command == "evaluate":
        return _cmd_rl_evaluate(args)
    if args.rl_command == "compare":
        return _cmd_rl_compare(args)
    if args.rl_command == "dashboard":
        return _cmd_rl_dashboard(args)
    if args.rl_command == "evolve":
        return _cmd_rl_evolve(args)
    if args.rl_command == "discover":
        return _cmd_rl_discover(args)
    print(f"unknown rl command: {args.rl_command}", file=sys.stderr)
    return 2


def _cmd_rl_train(args: argparse.Namespace) -> int:
    from .db import WorldStore
    from .training import benchmark_parallel, register_checkpoint, train

    if args.compare:
        results = benchmark_parallel(timesteps=args.timesteps, seed=args.seed,
                                     size=args.size,
                                     num_settlements=args.settlements)
        print(json.dumps(results, indent=2))
        return 0

    save_dir = Path(args.save_dir)
    summary = train(
        total_timesteps=args.timesteps,
        seed=args.seed,
        size=args.size,
        num_settlements=args.settlements,
        max_ticks=args.max_ticks,
        save_path=save_dir / f"policy_{args.generation}",
        log_path=save_dir / "train_log.jsonl",
        verbose=0,
        n_envs=args.parallel,
    )
    store = WorldStore(DEFAULT_DB_PATH)
    try:
        record = register_checkpoint(
            store, args.generation,
            save_dir / f"policy_{args.generation}", summary,
        )
    finally:
        store.close()
    print(json.dumps(summary, indent=2))
    print(
        f"checkpoint registered: generation={args.generation} "
        f"id={record['id']} sha256={record['checksum'][:16]}..."
    )
    return 0


def _cmd_rl_evaluate(args: argparse.Namespace) -> int:
    from .db import WorldStore
    from .training import (
        evaluate_vs_baseline,
        generate_report,
        resolve_policy_path,
    )

    if not args.policy_id and not args.model:
        print("provide --policy-id or --model", file=sys.stderr)
        return 2
    disaster_mult, gather_mult = (
        (2.0, 0.5) if args.difficulty == "hard" else (1.0, 1.0)
    )

    store = WorldStore(DEFAULT_DB_PATH)
    try:
        resolved, record = resolve_policy_path(
            store, args.policy_id or "gen1", explicit_path=args.model
        )
        results = evaluate_vs_baseline(
            model_path=resolved,
            num_worlds=args.worlds,
            first_seed=args.first_seed,
            ticks=args.ticks,
            size=args.size,
            num_settlements=args.settlements,
            disaster_mult=disaster_mult,
            gather_mult=gather_mult,
        )
        generation = (
            record["generation"] if record else (args.policy_id or "explicit")
        )
        results["agent_type"] = f"policy_{generation}_vs_rulebased"
        store.insert_training_run({
            "policy_generation_a": generation,
            "eval_seed_base": args.first_seed,
            "worlds": results["worlds"],
            "wins_a": results["policy_wins"],
            "ties": results["ties"],
            "win_fraction": results["win_fraction_strict"],
            "mean_survival_a": results["mean_policy_survival"],
            "mean_survival_b": results["mean_baseline_survival"],
            "results_json": results,
            "agent_type": results["agent_type"],
        })
    finally:
        store.close()
    checksum_note = ""
    if record and record.get("checksum"):
        checksum_note = f" (checksum verified {record['checksum'][:16]}...)"
    print(
        f"\nEvaluation over {results['worlds']} worlds "
        f"[{generation}]{checksum_note} "
        f"(difficulty: {args.difficulty}):\n"
        f"  policy wins (strict survival): {results['policy_wins']} "
        f"({results['win_fraction_strict']*100:.0f}%)\n"
        f"  ties: {results['ties']}\n"
        f"  reward wins: {results['reward_wins']} "
        f"({results['reward_win_fraction']*100:.0f}%)\n"
        f"  mean baseline survival: {results['mean_baseline_survival']} ticks\n"
        f"  mean policy survival:   {results['mean_policy_survival']} ticks\n"
        f"  mean peak pop — baseline {results['mean_baseline_peak_pop']:.0f} "
        f"vs policy {results['mean_policy_peak_pop']:.0f}"
    )
    sig_lines = []
    for name, m in results["metrics"].items():
        if m["wilcoxon_p"] is not None and m["wilcoxon_p"] < 0.05:
            sig_lines.append(f"    * {name}: p={m['wilcoxon_p']}")
    if sig_lines:
        print("  statistically significant differences (p<0.05):")
        print("\n".join(sig_lines))
    else:
        print("  no statistically significant differences (p>=0.05)")
    out_dir = Path(resolved).parent
    out = out_dir / "eval_results.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"results written to {out}")
    report_path = args.report or str(out_dir / "eval_report.md")
    chart_path = args.chart or str(out_dir / "eval_chart.png")
    generate_report(results, report_path, chart_path)
    print(f"report written to {report_path} (+ {chart_path})")
    return 0


def _evaluate_generation(gen: str, args: argparse.Namespace,
                         disaster_mult: float = 1.0,
                         gather_mult: float = 1.0) -> tuple[dict, dict]:
    from .db import WorldStore
    from .training import evaluate_vs_baseline, resolve_policy_path

    store = WorldStore(DEFAULT_DB_PATH)
    try:
        resolved, record = resolve_policy_path(store, gen)
    finally:
        store.close()
    results = evaluate_vs_baseline(
        model_path=resolved,
        num_worlds=args.worlds,
        first_seed=args.first_seed,
        ticks=args.ticks,
        size=args.size,
        num_settlements=args.settlements,
        disaster_mult=disaster_mult,
        gather_mult=gather_mult,
    )
    results["agent_type"] = f"policy_{gen}_vs_rulebased"
    return results, record


def _cmd_rl_compare(args: argparse.Namespace) -> int:
    """Evaluate two generations; Sprint 22 --head-to-head runs true
    policy-vs-policy competition in shared worlds."""
    if args.head_to_head:
        return _cmd_rl_head_to_head(args)
    res_a, rec_a = _evaluate_generation(args.gen_a, args)
    res_b, rec_b = _evaluate_generation(args.gen_b, args)

    delta_win = res_a["win_fraction_strict"] - res_b["win_fraction_strict"]
    delta_surv = res_a["mean_policy_survival"] - res_b["mean_policy_survival"]
    delta_peak = (
        res_a["mean_policy_peak_pop"] - res_b["mean_policy_peak_pop"]
    )
    print(f"\nComparison {args.gen_a} vs {args.gen_b}:")
    print(
        f"  win fraction vs baseline : "
        f"{res_a['win_fraction_strict']*100:.0f}% vs "
        f"{res_b['win_fraction_strict']*100:.0f}% (delta {delta_win*100:+.0f}pp)"
    )
    print(
        f"  mean policy survival     : "
        f"{res_a['mean_policy_survival']} vs "
        f"{res_b['mean_policy_survival']} (delta {delta_surv:+.1f})"
    )
    print(
        f"  mean peak population     : "
        f"{res_a['mean_policy_peak_pop']:.1f} vs "
        f"{res_b['mean_policy_peak_pop']:.1f} (delta {delta_peak:+.1f})"
    )

    store = WorldStore(DEFAULT_DB_PATH)
    try:
        for gen, res in ((args.gen_a, res_a), (args.gen_b, res_b)):
            store.insert_training_run({
                "policy_generation_a": gen,
                "eval_seed_base": args.first_seed,
                "worlds": res["worlds"],
                "wins_a": res["policy_wins"],
                "ties": res["ties"],
                "win_fraction": res["win_fraction_strict"],
                "mean_survival_a": res["mean_policy_survival"],
                "mean_survival_b": res["mean_baseline_survival"],
                "results_json": res,
                "agent_type": res.get("agent_type"),
            })
    finally:
        store.close()
    print("both evaluation runs recorded in training_runs")
    return 0


def _cmd_rl_dashboard(args: argparse.Namespace) -> int:
    """Sprint 18: learning-curve dashboard across policy generations."""
    from .training import compare_generations, generate_learning_curve_plot

    generations = [g.strip() for g in args.gens.split(",") if g.strip()]
    print(
        f"Evaluating {len(generations)} generations "
        f"({', '.join(generations)}) on {args.worlds} worlds × "
        f"{args.ticks} ticks..."
    )
    report = compare_generations(
        generations=generations,
        num_worlds=args.worlds,
        first_seed=args.first_seed,
        ticks=args.ticks,
        size=args.size,
        num_settlements=args.settlements,
    )
    curve = report["curve"]

    metric_key = {
        "survival": "mean_survival",
        "reward": "reward_win_fraction",
        "peak_population": "mean_peak_pop",
    }[args.metric]

    print("\nLearning curve:")
    print(f"  {'gen':<8} | {'survival':>9} | {'peak pop':>8} | "
          f"{'reward wins':>11} | trained eps")
    for gen in generations:
        c = curve[gen]
        print(f"  {gen:<8} | {c['mean_survival']:>9} | "
              f"{c['mean_peak_pop']:>8} | "
              f"{c['reward_win_fraction']*100:>10.0f}% | "
              f"{c['episodes_trained']}")

    values = [curve[g][metric_key] for g in generations]
    first, last = values[0], values[-1]
    if first:
        improvement = (last - first) / abs(first) * 100
        change_str = f"{improvement:+.1f}%"
    else:
        change_str = "n/a (first-generation value is zero)"
    print(f"\nPrimary metric ({args.metric}):")
    for gen, v in zip(generations, values):
        marker = " <-" if v == max(values) else ""
        print(f"  {gen:<8} {v}{marker}")
    print(
        f"  gen1 -> {generations[-1]} change: {change_str}"
    )

    mono = report["monotonic"]
    print("\nMonotonicity (non-decreasing OR non-increasing):")
    for key in ("survival", "reward_wins", "peak_pop"):
        print(f"  {key}: {mono[key]}  values={mono[key + '_values']}")

    regressions = report["regressions"]
    if regressions:
        print(f"\nREGRESSIONS detected ({len(regressions)} seeds where "
              f"{generations[-1]} < {generations[0]} on survival):")
        for r in regressions:
            print(f"  seed {r['seed']}")
    else:
        print(f"\nNo regressions: {generations[-1]} never loses to "
              f"{generations[0]} on survival")

    if args.plot:
        out = generate_learning_curve_plot(curve, args.plot)
        print(f"learning-curve plot written to {out}")

    out_json = Path("data/world_sim/policies/dashboard_results.json")
    with out_json.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"full results written to {out_json}")
    return 0


POLICIES_LOG_DIR = Path("data/world_sim/policies")


def _cmd_rl_evolve(args: argparse.Namespace) -> int:
    """Sprint 19: population-based generational training."""
    from .population import evolve

    results = evolve(
        generations=args.generations,
        population_size=args.population,
        n_mutants=args.mutants,
        mutation_strength=args.mutation_strength,
        timesteps_per_candidate=args.timesteps_per_candidate,
        seed_base=args.seed_base,
        size=args.size,
        num_settlements=args.settlements,
        max_ticks=args.max_ticks,
        n_envs=args.parallel,
        eval_ticks=args.eval_ticks,
        eval_seed_base=9000,
        eval_seed_count=args.eval_seeds,
        curriculum=args.curriculum,
        strategy_prior_path=args.strategy_priors,
    )
    print("\nEvolution summary:")
    for gen_result in results["generations"]:
        print(
            f"  {gen_result['generation']}: champion="
            f"{gen_result['champion']} "
            f"(origin={gen_result.get('champion_origin')}, "
            f"score {gen_result['champion_score']:.4f}, "
            f"parent={gen_result['parent']})"
        )
    out = POLICIES_LOG_DIR / "evolve_results.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"results written to {out}")
    return 0


def _cmd_rl_head_to_head(args: argparse.Namespace) -> int:
    """Sprint 22: true policy-vs-policy competition in shared worlds."""
    from .db import WorldStore
    from .competition import head_to_head_eval
    from .training import resolve_policy_path

    store = WorldStore(DEFAULT_DB_PATH)
    try:
        path_a, rec_a = resolve_policy_path(store, args.gen_a)
        path_b, rec_b = resolve_policy_path(store, args.gen_b)
    finally:
        store.close()

    results = head_to_head_eval(
        model_path_a=path_a,
        model_path_b=path_b,
        num_worlds=args.worlds,
        first_seed=args.first_seed,
        ticks=args.ticks,
        size=args.size,
        disaster_mult=2.0 if getattr(args, "difficulty", None) == "hard" else 1.0,
    )
    print(
        f"\nHead-to-head {args.gen_a} vs {args.gen_b} "
        f"over {results['worlds']} shared worlds:\n"
        f"  A wins: {results['a_wins']} | B wins: {results['b_wins']} | "
        f"ties: {results['ties']}\n"
        f"  mean cumulative reward — A {results['mean_reward_a']} vs "
        f"B {results['mean_reward_b']}\n"
        f"  mean territory share — A {results['mean_territory_share_a']} "
        f"vs B {results['mean_territory_share_b']}"
    )
    for metric in ("reward_permutation_p", "territory_permutation_p"):
        p = results[metric]
        if p is not None and p < 0.05:
            print(f"  * {metric}: p={p} (significant)")
    out = Path("data/world_sim/policies/h2h_results.json")
    with out.open("w", encoding="utf-8") as fh:
        json.dump({
            "gen_a": args.gen_a,
            "gen_b": args.gen_b,
            **results,
        }, fh, indent=2)

    store = WorldStore(DEFAULT_DB_PATH)
    try:
        store.insert_training_run({
            "policy_generation_a": args.gen_a,
            "policy_generation_b": args.gen_b,
            "eval_seed_base": args.first_seed,
            "worlds": results["worlds"],
            "wins_a": results["a_wins"],
            "ties": results["ties"],
            "win_fraction": round(results["a_wins"] / max(1, results["worlds"]), 3),
            "mean_survival_a": sum(
                r["A_survival_ticks"] for r in results["results"]
            ) / max(1, len(results["results"])),
            "mean_survival_b": sum(
                r["B_survival_ticks"] for r in results["results"]
            ) / max(1, len(results["results"])),
            "results_json": {"head_to_head": True, **results},
            "agent_type": "head_to_head",
        })
    finally:
        store.close()
    print(f"results written to {out}; match recorded in training_runs")
    return 0


def _cmd_rl_discover(args: argparse.Namespace) -> int:
    """Sprint 23: behavioral clustering + novelty detection over champion
    rollouts; discovered strategies persisted with exemplars."""
    from .db import WorldStore
    from .discovery import (
        DISCOVERY_LOG_PATH,
        collect_generation_samples,
        discover_strategies,
        save_discovery_log,
    )
    from .training import resolve_policy_path

    generations = [g.strip() for g in args.gens.split(",") if g.strip()]
    seeds = [70000 + i * 37 for i in range(args.worlds_per_gen)]

    samples = []
    store = WorldStore(DEFAULT_DB_PATH)
    try:
        for gen in generations:
            resolved, _record = resolve_policy_path(store, gen)
            gen_samples = collect_generation_samples(
                model_path=resolved,
                generation=gen,
                seeds=seeds,
                size=args.size,
                num_settlements=args.settlements,
                ticks=args.ticks,
            )
            samples.extend(gen_samples)
            print(f"[discover] {gen}: collected {len(gen_samples)} samples")
    finally:
        store.close()

    analysis = discover_strategies(
        samples, n_clusters=args.clusters,
        novelty_threshold=args.novelty_threshold,
    )

    print(f"\nDiscovered {len(analysis['clusters'])} strategy clusters:")
    for cluster in analysis["clusters"]:
        novelty = "  [NOVEL]" if cluster["novel"] else ""
        print(
            f"  {cluster['name']:<24} size={cluster['size']} "
            f"weak-label={cluster['weak_supervision_label']:<13} "
            f"closest-known={cluster['closest_known']} "
            f"(d={cluster['closest_distance']}){novelty}"
        )
        print(f"    centroid: {cluster['centroid']}")
        exemplars = [
            m for m in cluster["members"]
            if m.get("checkpoint_path")
        ]
        if exemplars:
            print(f"    exemplar: {exemplars[0]}")

    log_path = save_discovery_log(analysis["clusters"],
                                  DISCOVERY_LOG_PATH)
    print(f"\ndiscovery log written to {log_path}")
    return 0


def _cmd_rl_run(args: argparse.Namespace) -> int:
    """Headless episode runner over WorldSimEnv with a random policy."""
    import numpy as np

    from .env import WorldSimEnv

    env = WorldSimEnv(seed=args.seed, num_settlements=args.settlements,
                      max_ticks=args.ticks)
    totals = []
    lengths = []
    survivors = 0
    episode_curves: list[list[float]] = []
    breakdown_totals: dict[str, float] = {}
    hacking_ticks = 0
    for episode in range(args.episodes):
        obs, info = env.reset(seed=args.seed + episode)
        done = False
        total = 0.0
        steps = 0
        curve: list[float] = []
        rng = np.random.default_rng(args.seed + episode)
        while not done:
            action = int(env.action_space.sample())
            obs, reward, terminated, truncated, info = env.step(action)
            total += reward
            steps += 1
            curve.append(reward)
            for name, value in info["reward_breakdown"].items():
                breakdown_totals[name] = breakdown_totals.get(name, 0.0) + value
            if info["hacking_flag"]:
                hacking_ticks += 1
            done = terminated or truncated
        totals.append(total)
        lengths.append(steps)
        episode_curves.append(curve)
        alive = sum(1 for s in env.sim.settlements if s.is_alive)
        survivors += 1 if alive > 0 else 0
        print(
            f"  episode {episode}: seed={args.seed + episode} "
            f"steps={steps} return={total:.2f} "
            f"final pop={info['population']}"
        )
    print(
        f"\n{args.episodes} episodes | mean return "
        f"{sum(totals) / len(totals):.2f} | mean length "
        f"{sum(lengths) / len(lengths):.0f} ticks | worlds surviving: "
        f"{survivors}/{args.episodes}"
    )
    print("reward breakdown totals:")
    for name, value in sorted(breakdown_totals.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<20} {value:>10.2f}")
    if hacking_ticks:
        print(f"reward-hacking flags raised on {hacking_ticks} ticks")
    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 5))
        for i, curve in enumerate(episode_curves):
            ax.plot(range(1, len(curve) + 1), curve, label=f"ep {i}", alpha=0.7)
        ax.set_xlabel("tick")
        ax.set_ylabel("reward")
        ax.set_title("Reward per tick — benchmark episodes")
        ax.legend(loc="upper right", fontsize=8)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        plt.close(fig)
        print(f"reward plot written to {args.plot}")
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
        "rl": cmd_rl,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
