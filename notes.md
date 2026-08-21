# WorldSim — Working Notes & Session History

Running log of work sessions and decisions. Newest sessions at the top.
Decisions worth remembering are marked **[DECISION]**.

---

## Session 8 — 2026-08-20 — Sprint 8: Rule-Based Baseline Competence

**Scope:** Phase 2 / Sprint 8 — urgency-ordered decision policy, personality
vectors, high-yield site selection, epsilon exploration, benchmark worlds
with logged performance metrics.

### What was built

- `settlement.py` — `personality` dict field (expansionism / industry /
  commerce / aggression in [0,1]), seeded `assign_personality()`, assigned at
  registration, persisted in snapshots
- `agents.py` — policy restructured into explicit urgency order:
  famine > food security > expansion/road/trade cadences > resource income >
  farm growth > idle; personality-biased cadences (`claim_interval` shrinks
  with expansionism, etc.) and stockpile floors (industry raises the
  wood/stone level at which income buildings stop being prioritized);
  EPSILON raised to spec's 10%
- `simulation.py` — `find_building_site()` now picks **highest-yield** tiles
  (farms maximize terrain food; sawmills/mines prefer dense forest/mountain
  3×3 neighborhoods) instead of first-row-major
- `db.py` — `benchmark_runs` table + `insert_benchmark_run()`
- CLI — `benchmark --first-seed 50000 --num-worlds N --ticks T`: runs the
  baseline on seeded worlds, logs survival/peak-pop/resource metrics per
  world, prints survival rate
- Tests: 145 passing (11 new)

### Decisions

- **[DECISION] Epsilon = 10% uniform over WIRED actions**, not all 60 —
  uniform-over-60 would spend ~85% of exploration on no-ops, teaching
  nothing. Documented deviation from spec letter, faithful to intent.
- **[DECISION] Personalities are per-settlement seeded vectors** stored on
  the Settlement (persisted), read by the agent at observe-time. Aggression
  is reserved until military exists (Sprint 9+).
- **[DECISION] Benchmark seeds start at 50000** (Sprint 17's A/B suite uses
  50000–50019; consistent prefix).
- **[DECISION] High-yield selection is vectorized scoring + first-max
  tie-break** (row-major) — keeps determinism while "actively seeking"
  better tiles.

### Gotchas / bugs found & fixed

- **float32 comparison bug**: obs stores counts normalized to float32;
  `obs[7] >= 0.02` fails because float32(0.02) == 0.01999... < 0.02. Fixed
  with tolerant comparisons (`>= threshold - 1e-6`). This will bite RL
  feature engineering too — worth remembering.
- Personality tests initially failed because `observe()` syncs the cadence
  counter from the world clock — static-clock test loops never advanced
  cadence. Tests now advance `sim.world.tick`.
- An editing mishap briefly duplicated the policy body and mangled
  settlement.py structure — caught immediately by import checks.

### Benchmark results (acceptance: survive 5000+ ticks in 90% of worlds)

```
Seeds 50000-50009, 3 settlements each, 5000 ticks:
  World survival rate: 10/10 (100%)  [criterion: >= 90%]
  Peak populations: 197-218 per world
  All settlements alive at end in every world
```

The baseline is arguably now *too* competent at survival — no collapses in
benchmark worlds. Competition pressure (Sprint 9 raids/territory contests)
will need to provide the failure signal that makes learning meaningful later.

### Known issues / deferred

- Scout-neighbor and defense branches remain reserved (no fog-of-war or
  military yet — Sprints 9+).
- Personality effects are modest by design (threshold bias only); visible
  strategy divergence (agricultural vs mining vs trading civilizations) is
  Sprint 11's emergence target, built on these vectors.
- Benchmark runs don't yet write per-settlement rows — aggregated per world
  is enough for now; refine when Sprint 17 needs A/B comparisons.

### Acceptance criteria status

- ✅ Survives 5,000+ ticks in 90% of benchmark worlds → **100% (10/10)**
- ✅ Actively claims high-yield tiles (best-yield site selection + ring claims)
- ✅ Establishes trade routes when neighbors exist (adjacency-driven cadence)
- ✅ Building mix proportional to availability (affordability-gated branches)
- ✅ Personality vectors produce visibly different strategies (unit-tested:
  expansionist claims more, industrial builds more income buildings)
- ✅ Performance metrics logged: `benchmark_runs` table

### Next up (Sprint 9)

Multiple settlements & competition: neighbor detection, contested tiles,
raiding, cooperation via trade, hostile/friendly/neutral relations — the
military/aggression personality dims finally get wired.

---

## Session 7 — 2026-08-20 — Sprint 7: Agent Abstraction & Observation/Action Space

**Scope:** Phase 2 / Sprint 7 — 60-action space, 60-dim observations,
rule-based agent replacing the auto-rules, experience logging to
`agent_history`, `docs/agent_spec.md` as the frozen RL contract.

### What was built

- `actions.py` — `Action` IntEnum with all **60 IDs** in §5.2 category order;
  `WIRED_ACTIONS` registry (11 wired today); unwired IDs are validated no-ops
- `agents.py` — `Agent` ABC (`observe`/`decide`), `observe_vector()` producing
  the 60-dim normalized float32 vector (~31 real features, rest reserved 0.0),
  `RuleBasedAgent` encoding the Sprint 2–4 heuristics, `placeholder_reward()`
- `simulation.py` — `execute_action()` dispatch; per-tick agent cycle
  (observe → decide → execute → transition buffered); auto-build/auto-road/
  auto-trade/claim rules **removed from the loop** (logic survives as policy
  helpers `_auto_road_rule`/`_auto_trade_rule` and mechanics); agents created
  per settlement at spawn/resettle
- `db.py` — `agent_history` table + batched `insert_agent_experiences`;
  CLI flushes the RAM buffer every 500 ticks and at final save
- CLI — `--agent rulebased`, end-of-run action distribution report
- `docs/agent_spec.md` — full observation/action tables (the RL contract)
- Tests: 134 passing (22 new)

### Decisions

- **[DECISION] Full 60-action space defined now**, unwired actions = no-ops.
  The RL contract never changes shape — renumbering later would invalidate
  trained policies.
- **[DECISION] Agents fully replace auto-rules in the loop.** Mechanics
  unchanged; only the decision path moved. This is the swap point where the
  RL policy plugs in at Sprint 12.
- **[DECISION] Placeholder reward:** 0.1·Δpop + 0.05·Δbuildings − 0.1·starving
  + 0.001 survival, clamped [−1,1]. Formal reward is Sprint 13.
- **[DECISION] Experiences buffered in RAM, flushed every 500 ticks** — per
  architecture_notes.md's "don't murder the SQLite write bus".
- **[DECISION] Rule-based agent made near-stateless**: epsilon/farm rolls are
  keyed by `(seed, tick)` via fresh `random.Random` instances, and the cadence
  counter syncs from the world clock on every `observe()`. Consequence: saved/
  resumed simulations continue identically **without serializing agent
  internals** — a property the RL pipeline will want too.
- **[DECISION] Cadence branches ordered BEFORE income branches** in the
  policy so an unbuildable sawmill can never block territorial expansion.

### Gotchas / bugs found & fixed (the policy debugging saga)

Three real deadlocks found by tests/debug runs while porting heuristics into
decide-per-tick form:
1. **Granary spam starvation** — policy chose BUILD_GRANARY every tick without
   affordability checks until stone ran out, then nothing could ever be built
   again → total collapse by tick ~1250. Fix: affordability gates on every
   build branch (mirroring the old `can_afford` checks).
2. **Normalized-unit confusion** — `farms < 40` on a /50-normalized dim means
   2000 farms. All count thresholds converted to normalized units
   (`granaries < 0.4` == 20 granaries).
3. **Sawmill blocking expansion** — an unbuildable sawmill choice (no forest
   tile) fired every tick and preempted the claim cadence forever. Fix:
   cadences before income branches + sub-cadence (%8) gating on builds.
Plus: resumed sims had **no agents at all** (`simulation_from_state` didn't
create them) — caught by the step-continuation determinism test.

### Known issues / deferred

- Rule-based agent still wastes some decisions on failing builds (e.g.,
  sawmills before forest is in range); bounded by sub-cadence gating. A
  proper fix is remembering site availability — deferred to the RL agent,
  which learns this.
- Observation building calls `buildings_of`/`territory_of` several times per
  settlement per tick (full-grid masks). Fine now; profile before Sprint 12's
  parallel training.
- `EMERGENCY_RESPONSE` remains a no-op effect (used as a policy signal only).
- Benchmark outcomes shifted vs Sprints 2–6 (expected — decisions are now
  per-tick agent choices, not interval timers).

### Acceptance criteria status

- ✅ Loop calls `agent.observe(world)` and `agent.decide(obs)` each tick
- ✅ Observation vector: 60 normalized floats in [0,1], shape frozen
- ✅ Action IDs map to concrete behaviors (Action 0 = Build Farm on best tile…)
- ✅ Rule-based agent: food deficit → builds farm (unit-tested)
- ✅ All experiences (obs, action, reward, next_obs) logged to SQLite
- ✅ Agent abstraction swappable (AlwaysWait test agent behind same interface)

### Next up (Sprint 8)

Rule-based baseline competence: decision-tree priorities, urgency ordering,
scouting/high-yield tile claiming, personality vectors biasing thresholds,
benchmark-world performance metrics (survival time, population peak).

---

## Session 6 — 2026-08-20 — Sprint 6: Persistence, Save/Load & Simulation Clock (Milestone 1!)

**Scope:** Phase 1 / Sprint 6 — formal clock, save/load by world id, step
control, auto-save, God Mode action logging. **Completes Phase 1 and
Milestone 1 (Living Ant Farm).**

### What was built

- `clock.py` — 128 ticks/season, 512 ticks/year; `year_of`, `season_name`,
  `describe(tick)`; disasters.py now imports clock constants (no duplication)
- `db.py` — `god_events` table; `save_world_with_id()` (caller-chosen id,
  upsert); `update_world()` (new snapshot + last_tick bump); `log_god_event()`
  / `get_god_events()`; `_decode_array` now `.copy()`s (frombuffer views are
  read-only)
- `simulation.py` — God Mode actions (`god_smite`, `god_bless_resources`,
  `god_destroy_improvement`) each returning before/after dicts;
  `simulation_from_state()` factory to resume from a snapshot
- CLI additions:
  - `simulate --world-id X --save-interval N` (auto-save every N ticks)
  - `save --seed S --ticks T --world-id X` (deterministic re-run → fixed id)
  - `load --world-id X` (restore + state summary incl. clock)
  - `step --world-id X --ticks N` (advance saved world; pause = don't step,
    accelerate = bigger N)
  - `god --world-id X --action {smite,bless_food,bless_wood,bless_stone,destroy}`
  - `events --world-id X` (God event history with before/after)
- Tests: 112 passing (15 new)

### Decisions

- **[DECISION] Time controls are headless for now:** pause = not stepping a
  saved world, step = `step --ticks 1`, accelerate = larger N. Real-time
  controls belong to the future UI layer.
- **[DECISION] `save` regenerates deterministically from seed** rather than
  requiring a live process — the sim is pure `(seed, tick)`, so save-by-id is
  just re-run + upsert. This keeps the CLI stateless.
- **[DECISION] Auto-save writes snapshot rows under one world id** (history
  preserved per tick); final state always saved unless `--no-save`.
- **[DECISION] God actions mutate through the same validated paths as the
  sim** where possible (`destroy_building`), log before/after JSON to
  `god_events`, then persist via `update_world`.
- **[DECISION] Deserialized arrays are copied** on load so resumed sims can
  write to them (np.frombuffer returns read-only views).

### Gotchas / bugs found & fixed

- Inserting module-level `simulation_from_state` mid-file silently ended the
  class body, orphaning `status_line` as a bare function — caught by
  AttributeError in CLI tests. Moved to end of file.
- Read-only numpy arrays after load broke resumed sims (`assignment
  destination is read-only`).
- `_autosave` called `update_world` before the world row existed — switched
  to `save_world_with_id` (upsert semantics).
- argv values passed to `main([...])` must be strings even for int options.

### Acceptance criteria status

- ✅ `save --world-id abc123` writes full state to SQLite
- ✅ `load --world-id abc123` restores exact state (verified vs fresh run)
- ✅ Clock advances; season/year correct (1000 ticks = year 1, winter)
- ✅ Pause freezes (saved state); step advances exactly 1 tick
- ✅ God actions logged to `god_events` with before/after states
- ✅ Auto-save: run 1000 ticks → snapshots at 500 and 1000

### Milestone 1 reached

Phase 1 deliverable per detailed_sprint_plan.md: seeded deterministic world,
autonomous settlements that grow/trade/suffer disasters/collapse/recover,
full save/load, God Mode interventions, all observable via CLI.

### Next up

Phase 2 (Sprint 7): agent abstraction — observation/action spaces, rule-based
agent wired into the loop, experience logging. Also queued for discussion:
Electron/UI shell timing (stack docs specify Electron+React+PixiJS but no
sprint schedules it; visualization currently starts Phase 8).

---

## Session 5 — 2026-08-20 — Sprint 5: Disasters, Death & Recovery

**Scope:** Phase 1 / Sprint 5 — Drought/Fire/Plague, season-weighted regional
events, happiness/stability with misery-collapse, ruins + spontaneous
re-settlement, 2x growth near former capitals.

### What was built

- `disasters.py` — `DisasterType`/`DisasterEvent`, minimal season counter
  (`(tick // 128) % 4`), season-weighted event rolls every 50 ticks (~10%
  chance → ~2–4 events/1000 ticks), deterministic event ids (`ev-{seed}-{tick}`)
- `simulation.py` — regional disaster application (spawn-distance +
  margin approximation of zone overlap); Fire clears improvements on forest
  tiles in zone; Plague ×0.7 population; Drought halves farm output while
  active via `_drought_multiplier()` in the food-income path
- `RuinSite` records on death (id, name, spawn, collapse tick); ruins live on
  Simulation and serialize into snapshots
- Spontaneous re-settlement: ruin age ≥500, 10% chance per 100-tick window
  (seeded RNG keyed by crc32(ruin.id) — NOT Python `hash()`, see gotchas);
  new settlement founded at nearest free land tile to the old capital with
  `ruin_origin` set
- 2× growth: `step_population(growth_multiplier)` — ruin-origin settlements
  accumulate double growth progress while any owned tile is within 2 tiles
  of the former capital
- Happiness: decays 0.01/tick after >10 consecutive negative-net-food ticks,
  recovers 0.005 + tiny building-quality bonus; happiness < 0.1 sustained
  100 ticks → collapse
- `_kill()` now zeroes population, records the ruin, releases territory,
  deactivates trade routes — single death path for starvation, plague,
  economic and misery collapse
- Tests: 97 passing (17 new)

### Decisions

- **[DECISION] Minimal season counter now** (`128 ticks/season`): disaster
  weighting needs it; full clock (years, time controls) stays in Sprint 6.
- **[DECISION] Regional disasters** (center + radius 24) per user choice —
  enables "one civilization droughts while another thrives" emergent stories.
  Zone overlap approximated by spawn distance ≤ radius + 16 to avoid per-tick
  full-grid scans.
- **[DECISION] Seeded event-RNG** (`seed ^ offset + tick * 7919`): entropy is
  a pure function of seed+tick so A1 determinism holds. Event ids are
  deterministic strings, not UUIDs, for byte-level reproducibility.
- **[DECISION] Happiness affects only the collapse trigger this sprint**
  (user choice); growth pauses etc. deferred.
- **[DECISION] Ruins are in-sim records**, no SQLite table (user choice);
  serialized in snapshot JSON.
- **[DECISION] Plague can drive population to 0** → routes through the normal
  death path (`_kill`).
- **[DECISION] Re-settle placement:** nearest free non-water tile to the old
  capital (expanding ring search), not the exact old spawn (may be gone).

### Gotchas / bugs found & fixed

- **`hash()` on strings is process-randomized in Python** — using it in the
  re-settlement RNG would break cross-process reproducibility. Replaced with
  `zlib.crc32(ruin.id)`.
- **`_kill()` didn't zero population**: settlements killed by misery-collapse
  stayed `is_alive` and were re-killed every tick (ruins piling up). Found by
  the collapse test hanging at progress 386 ≥ 100 without dying.
- DisasterEvent's default uuid id made two identical rolls compare unequal —
  fixed with deterministic ids.
- Happiness *recovers* during the first 10 ticks of famine (streak hasn't
  tripped decay yet), so misery-collapse lands ~25 ticks later than naive
  math suggests. Test window widened accordingly.

### Known issues / deferred

- Zone-overlap approximation (spawn distance) misclassifies huge territories
  at zone edges; revisit if it matters gameplay-wise.
- No event log/narrative yet — events are data only (Phase 8 concern).
- Neighbor-relation term of happiness undefined until relations exist.
- Natural settlement deaths are rare post-Sprint 4 (auto-build + trade are
  competent); ruins/resettle mostly exercised via forced deaths in tests.

### Acceptance criteria status

- ✅ Drought reduces all farms' yield by 50% for 200 ticks
- ✅ Fire destroys forest-tile improvements (Sawmill included)
- ✅ Plague kills 30% of affected populations
- ✅ Collapsed settlement → neutral ruins (RuinSite recorded, territory freed)
- ✅ After 500 ticks of ruin, 10% chance per 100 ticks to re-settle
  (demonstrated: died tick 548 → refounded tick 1648 near old capital)
- ✅ Happiness decays when net food < 0 for >10 ticks
- ✅ Recovery rate 2x on tiles adjacent to former capital

### Next up (Sprint 6)

Persistence, save/load & simulation clock: full serialization round-trip CLI
(`save`/`load`), formal clock (512 ticks/year), time controls, auto-save every
500 ticks, God Mode action logging. Most plumbing already exists — Sprint 6 is
largely consolidation.

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
