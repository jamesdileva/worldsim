# WorldSim — Working Notes & Session History

Running log of work sessions and decisions. Newest sessions at the top.
Decisions worth remembering are marked **[DECISION]**.

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
