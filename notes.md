# WorldSim — Working Notes & Session History

Running log of work sessions and decisions. Newest sessions at the top.
Decisions worth remembering are marked **[DECISION]**.

---

## Session 4 — 2026-08-20 — Sprint 4 (+ minimal Sprint 9 pull-forward): Economy, Trade & Multi-Spawn

**Scope:** Sprint 4 (economy/trade) combined with a *minimal* slice of
Sprint 9 (multi-spawn) — trade is meaningless with one settlement. Full
Sprint 9 content (raids, relations, contested tiles) stays put.

### What was built

- Multi-spawn: `spawn_settlements(n)` — seeded best-food sites ≥32 tiles
  apart (Chebyshev), distance relaxed before giving up; unique per-settlement
  names via index-derived seed offset
- `TradeRoute` dataclass + `establish_route()` — allowed only between
  adjacent territories (8-neighborhood dilation check), one route per pair
- Direction-agnostic transfer: each tick the donor is whichever side holds
  more of the resource the other needs most; 1 unit/tick; food tradable via
  food_stock
- Auto-trade rule: every 24 ticks connect all adjacent unlinked pairs
- Economic collapse: any inventory < 0 sustained 48 ticks → −1 population;
  timer resets on recovery
- Scarcity slowdown: negative inventory halves build-queue processing rate
- Death now deactivates the dead settlement's trade routes (`_kill` helper)
- SQLite: `resources` (per-settlement inventory snapshots) + `trade_routes`
  tables; routes also serialized in snapshot JSON for exact round-trips
- CLI: `--settlements N` (default 3), per-settlement status lines + trade
  summary
- Tests: 80 passing (19 new)

### Decisions

- **[DECISION] Pulled minimal multi-spawn forward from Sprint 9.** Trade
  requires neighbors; raids/relations/contested tiles remain in Sprint 9.
- **[DECISION] Trade adjacency = territories within 1 tile**, not road-path
  connectivity. Spec says "connected by roads", but road networks don't
  interconnect yet; adjacency is the honest minimal proxy. Revisit when
  networks meet (§9.3).
- **[DECISION] Routes are direction-agnostic links**: donor/receiver chosen
  per tick by largest amount imbalance across {food, wood, stone, metal}.
  Simpler and more robust than fixed-direction routes with auto resource
  selection.
- **[DECISION] Collapse is spec-literal** (−1 pop per 48 ticks of negative
  inventory) and stacks with starvation — harsh but matches the doc.
- **[DECISION] Scarcity = any inventory < 0**; effect is half-rate
  construction ("poverty"), per spec's "scarcity leads to economic slowdown".
- **[DECISION] Spawn distance relaxation:** if no site satisfies 32-tile
  separation, retry unconstrained rather than failing — keeps small maps and
  high counts working deterministically.
- **[DECISION] `deserialize_world` now returns a 3-tuple**
  (world, settlements, trade_routes). Minor API break, updated all callers.

### Gotchas / bugs found & fixed

- All settlements got identical names (name seed used only world seed) —
  fixed with index-derived offset (7919 × index).
- Collapse test initially failed because the settlement *grew* while
  collapsing (positive food stock); test now isolates income to observe the
  collapse mechanic cleanly.
- Scarcity test initially set wood negative, which made the queued Farm
  *unaffordable* — revealing that an unaffordable queue head blocks the queue
  indefinitely (see known issues).

### Known issues / deferred

- Unaffordable build-queue head blocks the queue until resources recover
  (auto-rule only enqueues affordable items so it rarely triggers naturally).
- Trade value "computed from production efficiency and distance" (spec) not
  implemented — routes just count transfers; metrics come with the dashboard.
- Road-connected trade (spec-literal) deferred until road networks can span
  settlements.
- `_territories_adjacent` is O(n²) pair checks over full-grid masks every 24
  ticks — fine at n=3, revisit at Sprint 9 scale.

### Acceptance criteria status

- ✅ Two settlements establish a trade route when adjacent
- ✅ Route transfers 1 resource/tick (490 units moved by tick 1000 in demo)
- ✅ Economic collapse: inventory < 0 for 48 ticks → population loss
- ✅ Economic metrics tracked and stored (`resources` table)
- ✅ Bonus: multi-spawn (3 default), scarcity slowdown, route persistence

### Next up (Sprint 5)

Disasters, death & recovery: Drought/Fire/Plague, climate-driven frequency,
collapse → neutral ruins, spontaneous re-settlement after 500 ticks,
happiness/stability system, 2x recovery on reclaimed ruins.

---

## Session 3 — 2026-08-20 — Sprint 3: Buildings, Roads & Infrastructure

**Scope:** Phase 1 / Sprint 3 — Farm/Sawmill/Mine/Granary with costs and
per-tick outputs, roads at 50% movement cost, connectivity flagging,
construction/destruction, persistence.

### What was built

- `buildings.py` — `BuildingType`/`Improvement` enums, `BuildingSpec` table
  (costs, outputs, valid terrain), road cost/multiplier, base food capacity
- `world.py` — `improvements` int8 grid (single source of truth for buildings
  AND roads); `movement_cost` now halves on road tiles
- `simulation.py` — `build_at()` (validates ownership/terrain/unimproved/
  affordability), `build_road()`, `destroy_building()`, FIFO build queue
  processed per tick, `road_connectivity()` BFS with settlement center as hub,
  building outputs + passive gathering in `_produce_resources()`,
  `food_capacity()` via granaries, auto-build rule (least-built-first),
  auto-road rule (extends network one tile per interval)
- `settlement.py` — starting reserves (30 wood / 15 stone), `build_queue`
  field, `consume_food(income, capacity)` with overflow-wasted semantics
- `db.py` — snapshots now include improvements array + build queues
- Tests: 60 passing (19 new buildings tests; 5 terrain-gated skips)

### Decisions

- **[DECISION] Improvements grid is the single source of truth** for buildings
  and roads (like the ownership grid). No duplicate building lists to drift;
  per-settlement counts derived by masking with ownership.
- **[DECISION] Farm allowed on Plains AND Fertile** (spec acceptance only
  requires Plains; fertile is farmland). Sawmill → Forest only, Mine →
  Mountain only, Granary → any owned land tile.
- **[DECISION] Unspecified costs/outputs defined:** Sawmill 4w2s → +2 wood/tick;
  Mine 6w4s → +2 stone +1 metal/tick; Granary 5w5s → +500 food cap;
  Road 1 stone/tile. Farm kept exactly per spec: 5w3s → +2 food/tick.
- **[DECISION] Food storage cap introduced:** base 500 + 500/granary. Income
  above free storage is wasted. Fixes Session 2's unbounded-stock issue.
  Crucially, `net_food_rate` records *uncapped* production so expansion
  decisions don't stall when stock is full (this exact bug was found and fixed).
- **[DECISION] Passive gathering trickle:** workers gather 25% of terrain
  wood/stone/metal yields on owned tiles per tick. Without it the economy
  hard-deadlocks: every building costs wood and sawmills need wood to build.
- **[DECISION] Instant construction** when the queue head is affordable; queue
  structure is future-ready for agents (Sprint 7) without multi-tick
  construction complexity.
- **[DECISION] Auto-build rule = least-built-type-first** (ties broken by
  priority order farm>sawmill>mine>granary). First attempt ("always farm")
  produced a 100%-farm mix; least-built balances the demo economy until real
  agents arrive.
- **[DECISION] Settlement center is a road-network hub**: roads adjacent to
  spawn count as connected even though the center tile itself has no road.
- **[DECISION] Persistence via snapshot JSON only** (improvements array +
  build queues). No new tables — §24.1 schema doesn't call for them yet.

### Gotchas / bugs found & fixed

- Storage cap clamped income to 0 near cap → `net_food_rate` ≤ 0 → territory
  claiming stalled permanently. Fix: cap applies to stock only; decision
  signal uses uncapped production rate.
- Wood deadlock: no passive wood income meant construction stopped forever
  once starting reserves ran out (observed live: buildings frozen at 4).
- Seed 42's spawn territory contains zero Plains tiles (all Fertile) — tests
  that assume specific terrain near spawn must tolerate equivalents or skip.

### Known issues / deferred

- Auto rules are placeholders; agents replace them in Sprints 7–8.
- Road network grows greedily adjacent-to-existing; no pathfinding toward
  targets yet (meaningful route selection is §9.2, later sprint).
- 5 tests skip when seed-42 territory lacks forest/mountain — could use a
  guaranteed-feature seed instead.
- Per-tick cost still O(territory); unchanged from Session 2 note.

### Acceptance criteria status

- ✅ Farm (5 wood, 3 stone) builds on Plains; produces +2 food/tick
- ✅ Buildings destroyed when tile lost (release_territory clears improvements)
- ✅ Roads on owned tiles; movement cost 50% of terrain normal
- ✅ Road connectivity checked; disconnected roads flagged (BFS from hub)
- ✅ Build queue exists and processes FIFO (UI inspector deferred — no UI yet)

### Next up (Sprint 4)

Economy, resource production & trade: inter-settlement trade routes over road
networks, trade value from efficiency/distance, scarcity slowdowns, economic
collapse (inventory < 0 for 48 ticks → population loss), `resources` +
`trade_routes` tables. Requires multiple settlements — may pull multi-spawn
forward from Sprint 9.

---

## Session 2 — 2026-08-20 — Sprint 2: Settlements & Population Dynamics

**Scope:** Phase 1 / Sprint 2 — Settlement entity, food income/consumption,
growth (+1/24 ticks), starvation (−1/48 ticks), death at pop 0, 3×3 spawn
territory + ring expansion via `claim_territory`, tile ownership, `settlements`
table, minimal headless `simulate` CLI.

### What was built

- `settlement.py` — `Settlement` dataclass; pure state transitions:
  `consume_food()` (income − pop × 1/tick) and `step_population()`
  (growth/starvation counters)
- `simulation.py` — deterministic tick engine (`Simulation.step()/run()`),
  seeded spawn search (best-food 3×3 window in central region via valid-mode
  3×3 box filter), territory claiming (initial 3×3, ring expansion over
  unowned 8-neighbors), auto-claim rule, death releases territory
- `world.py` — added `ownership` int32 array (−1 = unowned) and
  `food_yield_grid()`
- `db.py` — snapshots now include ownership + settlements (exact round-trip);
  new `settlements` table (id, name, world_id, spawn_x/y, created/destroyed tick)
- `cli.py` — new `simulate` subcommand:
  `python -m worldsim simulate --seed N --ticks T [--report-interval K] [--db] [--no-save]`
- Tests: 41 total passing (16 new settlement/sim tests, sim persistence, CLI)

### Decisions

- **[DECISION] Food yields rescaled:** fertile 4, plains 2, forest 1, desert −1.
  The §3.1 table's "+/++" markers are qualitative; the original small ints
  (fertile 2 / plains 1) made the best 3×3 on any map yield only ~8 food/tick
  vs 10 workers eating 10/tick — growth was mathematically impossible. New
  values keep scarcity reachable (desert/water spawns still starve) while
  making good spawns viable.
- **[DECISION] Consumption = 1 food/worker/tick** (docs never specify).
- **[DECISION] Growth condition is `food_stock > 0`** (per spec wording "if
  food is positive"), not net-rate > 0. A settlement with flat positive stock
  still grows every 24 ticks.
- **[DECISION] Single settlement per world** for Sprint 2; multi-spawn is
  Sprint 9.
- **[DECISION] Auto-expansion rule:** every 24 ticks, if last tick's net food
  rate > 0, claim one ring. Proves `claim_territory` before agents exist
  (Sprint 7); will be replaced by agent decisions later.
- **[DECISION] Death releases territory** back to unowned. Sprint 5 will turn
  collapsed settlements into neutral ruins instead.
- **[DECISION] Minimal headless `simulate` CLI now**; full time controls
  (pause/step/speed) stay in Sprint 6.

### Gotchas / bugs found & fixed

- `opensimplex.noise2array` takes 1-D vectors (outer grid), not meshgrid —
  hit again when refactoring spawn search.
- Off-by-one in spawn search: valid-mode convolution indexes window *top-left*
  corners; spawn center = index + 1.
- Row/col swap: `find_spawn_location` returns (row, col) but was unpacked as
  (x, y) — spawned settlements were mirrored across the diagonal. Caught by
  the neighborhood-yield test.

### Known issues / deferred

- Food stock grows unbounded (~578k after 500 ticks). Storage caps arrive with
  Granary in Sprint 3.
- Territory expansion is currently limited only by adjacency + surplus; there
  is no max radius or cost. Revisit when expansion becomes an agent decision.
- Simulation step is O(territory) per tick per settlement (numpy argwhere +
  python loop in `_claim_tiles`, `food_income` masks full grid each tick).
  Fine at this scale; profile before Sprint 9 (multi-settlement).

### Acceptance criteria status

- ✅ Spawns with 10 workers; +1 population per 24 ticks if food positive
- ✅ −1 population per 48 ticks when food exhausted; dies at pop 0
- ✅ Territory expands one ring on `claim_territory`
- ✅ Ownership stored in tile data (`world.ownership`) and persisted
- ✅ Bonus: deterministic `simulate` CLI with periodic status lines

### Next up (Sprint 3)

Buildings, roads & infrastructure: Farm/Sawmill/Mine/Granary, construction
costs, buildings occupy tiles, road networks (50% movement cost), contiguous
road validation, persistence.

---

## Session 1 — 2026-08-20 — Sprint 1: World Generation & Terrain

**Scope:** Phase 1 / Sprint 1 from `docs/detailed_sprint_plan.md` — seeded 256×256
world, terrain generation, movement costs + resource yields, SQLite schema,
`generate` CLI with stats and ASCII map.

### What was built

- Project scaffold: `pyproject.toml`, `src/worldsim/` layout (src-layout package)
- `tiles.py` — `TerrainType` enum, per-terrain movement cost / yield profiles
  (from `architecture_detailed.md` §3.1), ASCII glyphs
- `terrain.py` — seeded generation: two independent noise layers (elevation,
  moisture) via opensimplex, 2 octaves each, normalized to [0,1], thresholded
  into 6 biomes
- `world.py` — `World` dataclass holding NumPy arrays (`elevation`, `moisture`,
  `terrain`), derived movement-cost grid, terrain breakdown stats, aggregate
  resource yields, ASCII renderer
- `db.py` — plain-sqlite3 `WorldStore`; `worlds` + `snapshots` tables per §24.1;
  world state serialized as compressed JSON with base64 raw array bytes
  (exact round-trips)
- `cli.py` / `__main__.py` — `python -m worldsim generate --seed N [--size S]
  [--db PATH] [--no-save]`
- Tests: 19 passing (`tests/test_terrain.py`, `test_db.py`, `test_cli.py`)
  covering determinism, biome reachability, persistence round-trip, CLI output

### Decisions

- **[DECISION] opensimplex over the `noise` library.** Docs mentioned `noise`,
  but its C extension frequently fails to build on Windows/modern Python
  (we're on Python 3.14). opensimplex is pure-Python; vectorized sampling is a
  later optimization if needed.
- **[DECISION] Plain sqlite3 instead of SQLAlchemy for now.** Only two tables in
  Sprint 1; revisit when schema grows (Sprint 2+).
- **[DECISION] argparse for CLI.** stdlib, zero deps, sufficient for the
  planned subcommands (generate/save/load/simulate).
- **[DECISION] pytest from day one.** Sprint 1 acceptance criteria are
  essentially determinism tests anyway.
- **[DECISION] NumPy arrays per tile property** (not per-tile objects) — aligns
  with the perf guidance in `architecture_notes.md` and keeps the door open for
  Numba/vectorization later.
- **[DECISION] Seed derivation:** sub-seeds per noise layer = master seed +
  fixed offset (elevation +0, moisture +1,000,000), masked to 31 bits so each
  layer is independent but fully reproducible.

### Gotchas / known issues

- `opensimplex.noise2array()` takes **1-D coordinate vectors** (returns the
  outer grid), not meshgrid arrays — first implementation failed on this.
- Generation is currently pure-Python-loop inside opensimplex (~1–2 s per
  256×256 world). Acceptable now; profile before optimizing (Numba candidate).
- Global `pytest-asyncio` plugin emits unrelated warnings; harmless.

### Acceptance criteria status

- ✅ `python -m worldsim generate --seed 12345` produces a valid 256×256 world file
- ✅ Same seed twice → identical output (tested byte-level)
- ✅ Output includes terrain type breakdown (count + %)
- ✅ ASCII map viewable

### Next up (Sprint 2)

Settlements & basic population dynamics: Settlement entity, growth/starvation,
fixed spawn points, food consumption, territory claiming (3×3 + rings),
`settlements` table.
