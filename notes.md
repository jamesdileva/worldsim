# WorldSim — Working Notes & Session History

Running log of work sessions and decisions. Newest sessions at the top.
Decisions worth remembering are marked **[DECISION]**.

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
