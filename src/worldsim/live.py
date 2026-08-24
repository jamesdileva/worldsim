"""Interactive living-world shell (Sprint 51).

One session owns one world: launch, watch, inspect, play god, revert —
all against an in-memory simulation without process reloads.

Contract:
- Every mutation flows through the SAME methods the CLI uses (god_*
  handlers, undo-point capture), so audit and undo semantics are
  identical across interfaces.
- Catastrophic actions ask y/n confirmation in the shell (the friendlier
  equivalent of the CLI's --force/--confirm flags).
"""

from __future__ import annotations

import cmd as _cmd

from .db import WorldStore, serialize_world
from .simulation import Simulation


def _undo_state_json(sim, serialize_fn) -> str:
    """Full-state serialization used for shell undo points."""
    return serialize_fn(
        sim.world,
        sim.settlements,
        trade_routes=sim.trade_routes,
        ruins=sim.ruins,
        disaster_events=sim.disaster_events,
        relations=sim.relations,
        contested=sim.contested,
        building_debuffs=sim.building_debuffs,
        event_log=sim.event_log,
        diplomacy=sim.diplomacy,
        strategy_memory=sim.strategy_memory,
        highway_projects=sim.highway_projects,
        treaties=sim.treaties,
        contamination_zones=sim.contamination_zones,
    )


class WorldShell(_cmd.Cmd):
    """`worldsim live` — the World Simulator shell."""

    intro = (
        "WorldSim live shell — type 'help' for commands.\n"
        "'new' creates a world, 'load <id>' opens a saved one."
    )

    def __init__(self, store=None, world_id=None, sim=None,
                 stdin=None, stdout=None) -> None:
        super().__init__(stdin=stdin, stdout=stdout)
        self.store = store if store is not None else WorldStore()
        self.sim = sim
        self.world_id = world_id
        self._undo_json = None
        self._undo_label = None
        # Kept after commit so 'undo' can restore this session's last
        # pre-mutation state without hitting the store.
        self._restore_json = None
        self._restore_label = None
        self.update_prompt()

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def update_prompt(self) -> None:
        wid = self.world_id or "unsaved"
        tick = self.sim.tick if self.sim is not None else "-"
        self.prompt = f"{wid}@t{tick}> "

    def _require_world(self):
        if self.sim is None:
            print("no world loaded — use 'new' or 'load <world-id>'")
            return False
        return True

    def _capture_undo(self, label: str) -> None:
        self._undo_json = _undo_state_json(self.sim, serialize_world)
        self._undo_label = label

    def _commit_mutation(self) -> None:
        """Persist the captured undo point after a mutation applied, and
        stage it for in-session 'undo'."""
        if self._undo_json is not None:
            if self.world_id:
                self.store.save_undo_point(
                    self.world_id, self._undo_json, self.sim.tick,
                    label=self._undo_label or "mutation",
                )
            self._restore_json = self._undo_json
            self._restore_label = self._undo_label
        self._undo_json = None
        self.update_prompt()

    def _confirm(self, question: str) -> bool:
        try:
            answer = input(f"{question} [y/N]: ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")

    def _settlement_by_name(self, name: str):
        lowered = name.lower()
        return next(
            (s for s in self.sim.settlements
             if s.name.lower() == lowered), None)

    def default(self, line):
        print(f"unknown command: {line.split()[0]!r} (try 'help')")

    def emptyline(self):
        pass

    # ------------------------------------------------------------------
    # World lifecycle
    # ------------------------------------------------------------------

    def do_new(self, arg):
        """new [name] [--seed N] [--size N] [--settlements N]"""
        name = None
        seed, size, count = 42, 64, 3
        tokens = arg.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "--seed" and i + 1 < len(tokens):
                seed = int(tokens[i + 1]); i += 2
            elif token == "--size" and i + 1 < len(tokens):
                size = int(tokens[i + 1]); i += 2
            elif token == "--settlements" and i + 1 < len(tokens):
                count = int(tokens[i + 1]); i += 2
            elif not token.startswith("--"):
                name = token; i += 1
            else:
                i += 1
        from .world import World

        self.sim = Simulation(World(seed=seed, size=size))
        for s in self.sim.spawn_settlements(count=count):
            print(f"  {s.name} @ ({s.spawn_x},{s.spawn_y})")
        self.world_id = name
        self._undo_json = None
        self.update_prompt()

    def do_load(self, arg):
        """load <world-id> — open a saved world."""
        from .cli import _load_sim_from_store

        wid = arg.strip()
        if not wid:
            print("usage: load <world-id>")
            return
        try:
            self.sim = _load_sim_from_store(self.store, wid)
        except Exception as exc:
            print(f"load failed: {exc}")
            return
        self.world_id = wid
        print(f"loaded {wid} at tick {self.sim.tick}")
        self.update_prompt()

    def do_save(self, arg):
        """save [world-id] — persist the current world."""
        if not self._require_world():
            return
        wid = arg.strip() or self.world_id or f"world-{self.sim.world.seed}"
        self.store.save_world_with_id(
            wid,
            self.sim.world,
            settlements=self.sim.settlements,
            trade_routes=self.sim.trade_routes,
            ruins=self.sim.ruins,
            disaster_events=self.sim.disaster_events,
            relations=self.sim.relations,
            contested=self.sim.contested,
            building_debuffs=self.sim.building_debuffs,
            event_log=self.sim.event_log,
            diplomacy=self.sim.diplomacy,
            strategy_memory=self.sim.strategy_memory,
            highway_projects=self.sim.highway_projects,
            treaties=self.sim.treaties,
            contamination_zones=self.sim.contamination_zones,
        )
        self.world_id = wid
        print(f"saved as {wid} at tick {self.sim.tick}")
        self.update_prompt()

    def do_status(self, arg):
        """status — clock + settlement lines."""
        if not self._require_world():
            return
        from .clock import describe

        print(f"tick {self.sim.tick} ({describe(self.sim.tick)})")
        for s in sorted((x for x in self.sim.settlements if x.is_alive),
                        key=lambda x: x.name):
            print(f"  [{s.name}] {self.sim.status_line(s)}")

    def do_quit(self, arg):
        """quit — exit the shell (world keeps living only if saved)."""
        print("bye")
        return True

    def do_EOF(self, arg):
        print()
        return True

    # ------------------------------------------------------------------
    # Time control
    # ------------------------------------------------------------------

    def do_step(self, arg):
        """step [n] — advance n ticks (default 1)."""
        if not self._require_world():
            return
        n = int(arg) if arg.strip() else 1
        for _ in range(n):
            self.sim.step()
        self.update_prompt()

    def do_watch(self, arg):
        """watch [interval] — auto-step forever; Ctrl+C pauses."""
        if not self._require_world():
            return
        interval = int(arg) if arg.strip() else 500
        print("watching — Ctrl+C to pause")
        try:
            while True:
                self.sim.step()
                if self.sim.tick % interval == 0:
                    self.do_status("")
        except KeyboardInterrupt:
            print("\npaused")
        self.update_prompt()

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def do_map(self, arg):
        """map [x0 y0 x1 y1] — ASCII world map."""
        if not self._require_world():
            return
        from .visualization import LEGEND, render_ascii_map

        coords = [int(t) for t in arg.split()] if arg.strip() else []
        kwargs = {}
        if len(coords) == 4:
            kwargs = dict(x0=coords[0], y0=coords[1],
                          x1=coords[2], y1=coords[3])
        print(render_ascii_map(self.sim, **kwargs))
        print(LEGEND)

    def do_panels(self, arg):
        """panels — detail panel per living settlement."""
        if not self._require_world():
            return
        from .visualization import render_settlement_panel

        for s in sorted((x for x in self.sim.settlements if x.is_alive),
                        key=lambda x: x.name):
            print(render_settlement_panel(self.sim, s))
            print()

    def do_chronicle(self, arg):
        """chronicle [name] — one civilization's saga, or summary of all."""
        if not self._require_world():
            return
        from .histories import civilizations_summary, render_chronicle

        name = arg.strip()
        if name:
            target = self._settlement_by_name(name)
            if target is None:
                print(f"no settlement named {name!r}")
                return
            print(render_chronicle(self.sim, target))
        else:
            for line in civilizations_summary(self.sim):
                print(line)

    def do_timeline(self, arg):
        """timeline [limit] — recent world events, oldest first."""
        if not self._require_world():
            return
        from .timeline import build_timeline, render_timeline

        limit = int(arg) if arg.strip() else 30
        events = build_timeline(
            self.sim,
            since_tick=max(0, self.sim.tick - 2000), limit=limit)
        text = render_timeline(self.sim, events, date_stamps=False)
        print(text if text else "(no recent events)")

    # ------------------------------------------------------------------
    # Playing god (same handlers + audit as the CLI)
    # ------------------------------------------------------------------

    def do_smite(self, arg):
        """smite <name> <amount> — kill population (confirms >= 25)."""
        if not self._require_world():
            return
        parts = arg.split()
        if len(parts) != 2:
            print("usage: smite <name> <amount>")
            return
        target = self._settlement_by_name(parts[0])
        if target is None:
            print(f"no settlement named {parts[0]!r}")
            return
        amount = int(parts[1])
        if amount >= 25 and not self._confirm(
            f"smite {amount} population from {target.name}?"
        ):
            print("cancelled")
            return
        self._capture_undo("smite")
        self.sim.god_smite(target, amount)
        self._commit_mutation()
        print(f"{target.name}: population now {target.population}")

    def do_bless(self, arg):
        """bless <name> <food|wood|stone|metal|happiness> [amount]"""
        if not self._require_world():
            return
        parts = arg.split()
        if len(parts) < 2:
            print("usage: bless <name> <resource> [amount]")
            return
        target = self._settlement_by_name(parts[0])
        if target is None:
            print(f"no settlement named {parts[0]!r}")
            return
        resource = parts[1].lower()
        amount = float(parts[2]) if len(parts) > 2 else 100.0
        self._capture_undo("bless")
        if resource == "happiness":
            _before, _after = sim_god_bless_happiness(self.sim, target)
        elif resource == "food":
            sim_god_bless_resources(self.sim, target, "food", amount)
        elif resource in ("wood", "stone", "metal"):
            sim_god_bless_resources(self.sim, target, resource, amount)
        else:
            print(f"unknown resource {resource!r}")
            self._undo_json = None
            return
        self._commit_mutation()
        print(f"blessed {target.name}")

    def do_nuke(self, arg):
        """nuke <x> <y> — nuclear strike (always confirms)."""
        if not self._require_world():
            return
        parts = arg.split()
        if len(parts) != 2:
            print("usage: nuke <x> <y>")
            return
        x, y = int(parts[0]), int(parts[1])
        if not self._confirm(f"NUKE ({x}, {y})? This cannot be undone "
                             "without 'undo'"):
            print("cancelled")
            return
        self._capture_undo("nuke")
        _before, after = self.sim.god_nuke(x, y)
        self._commit_mutation()
        print(f"improvements annihilated: {after['improvements_destroyed']}")
        print(f"deaths: {after['deaths']}")
        print(f"contaminated until tick {after['contaminated_until']}")

    def do_disaster(self, arg):
        """disaster <drought|fire|plague> <x> <y> [radius]"""
        if not self._require_world():
            return
        parts = arg.split()
        if len(parts) < 3:
            print("usage: disaster <type> <x> <y> [radius]")
            return
        dtype, x, y = parts[0], int(parts[1]), int(parts[2])
        radius = int(parts[3]) if len(parts) > 3 else None
        kwargs = {"radius": radius} if radius is not None else {}
        try:
            self._capture_undo("disaster")
            _before, after = self.sim.god_trigger_disaster(
                dtype, x, y, **kwargs)
        except ValueError as exc:
            print(exc)
            self._undo_json = None
            return
        self._commit_mutation()
        print(f"affected: {_before['affected_settlements']}")

    def do_spawn(self, arg):
        """spawn <x> <y> [name] — found a new settlement."""
        if not self._require_world():
            return
        parts = arg.split()
        if len(parts) < 2:
            print("usage: spawn <x> <y> [name]")
            return
        name = parts[2] if len(parts) > 2 else None
        self._capture_undo("spawn")
        _before, after = self.sim.god_spawn_settlement(
            int(parts[0]), int(parts[1]), name=name)
        self._commit_mutation()
        print(f"founded {after['name']}")

    def do_freeze(self, arg):
        """freeze <name> — toggle time-stop for a settlement."""
        if not self._require_world():
            return
        target = self._settlement_by_name(arg.strip())
        if target is None:
            print(f"no settlement named {arg.strip()!r}")
            return
        self._capture_undo("freeze")
        _before, after = sim_god_toggle_freeze(self.sim, target)
        self._commit_mutation()
        print(f"{target.name}: frozen={after['frozen']}")

    def do_terraform(self, arg):
        """terraform <water|desert|plains|fertile|forest|mountain> <x> <y>
        [radius]"""
        if not self._require_world():
            return
        parts = arg.split()
        if len(parts) < 3:
            print("usage: terraform <terrain> <x> <y> [radius]")
            return
        terrain, x, y = parts[0], int(parts[1]), int(parts[2])
        self._capture_undo("terraform")
        try:
            if len(parts) > 3:
                _before, after = sim_god_terraform_region(
                    self.sim, x, y, int(parts[3]), terrain)
                print(f"changed {after['tiles_changed']} tiles, "
                      f"{after['improvements_lost']} improvements lost")
            else:
                sim_god_terraform(self.sim, x, y, terrain)
                print(f"tile ({x}, {y}) is now {terrain}")
        except ValueError as exc:
            print(exc)
            self._undo_json = None
            return
        self._commit_mutation()

    # ------------------------------------------------------------------
    # Undo / branching
    # ------------------------------------------------------------------

    def do_undo(self, arg):
        """undo — revert to the state before your last mutation."""
        if self._restore_json is None:
            print("nothing to undo in this session")
            return
        label = self._restore_label or "mutation"
        self.sim = Simulation.from_state_json(self._restore_json)
        print(f"undid '{label}' — back at tick {self.sim.tick}")
        # Consume the restore point (matching CLI semantics: one undo
        # per intervention).
        self._restore_json = None
        self.update_prompt()

    def do_branch(self, arg):
        """branch <new-world-id> — save current state as a NEW world."""
        if not self._require_world():
            return
        wid = arg.strip()
        if not wid:
            print("usage: branch <new-world-id>")
            return
        self.store.save_world_with_id(
            wid,
            self.sim.world,
            settlements=self.sim.settlements,
            trade_routes=self.sim.trade_routes,
            ruins=self.sim.ruins,
            disaster_events=self.sim.disaster_events,
            relations=self.sim.relations,
            contested=self.sim.contested,
            building_debuffs=self.sim.building_debuffs,
            event_log=self.sim.event_log,
            diplomacy=self.sim.diplomacy,
            strategy_memory=self.sim.strategy_memory,
            highway_projects=self.sim.highway_projects,
            treaties=self.sim.treaties,
            contamination_zones=self.sim.contamination_zones,
            skip_entity_rows=True,
        )
        print(f"branch saved as {wid} (coexists with {self.world_id})")


# Thin wrappers so tests/monkeypatching stay simple.
def sim_god_bless_resources(sim, settlement, resource, amount):
    return sim.god_bless_resources(settlement, resource, amount)


def sim_god_bless_happiness(sim, settlement):
    return sim.god_bless_happiness(settlement)


def sim_god_toggle_freeze(sim, settlement):
    return sim.god_toggle_freeze(settlement)


def sim_god_terraform(sim, x, y, terrain):
    return sim.god_terraform(x, y, terrain)


def sim_god_terraform_region(sim, x, y, radius, terrain):
    return sim.god_terraform_region(x, y, radius, terrain)


def start_live_shell(store=None, world_id=None, sim=None,
                     stdin=None, stdout=None) -> WorldShell:
    shell = WorldShell(store=store, world_id=world_id, sim=sim,
                       stdin=stdin, stdout=stdout)
    shell.cmdloop()
    return shell
