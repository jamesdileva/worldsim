# World Simulator — Detailed Sprint Plan

## Phase 1: Deterministic World (Sprints 1–6)
**Goal:** A self-contained, deterministic, persistent world where rule-based settlements build infrastructure, grow, trade, collapse, and recover. No ML yet — establish the full observe→decide→act loop as the foundation for RL.

---

### Sprint 1 — World Generation & Terrain

**Duration:** 2 weeks
**Deliverable:** A seeded 2D grid world with terrain generation that is fully reproducible.

**Tasks:**
- Implement world grid (256×256 tiles by default)
- Generate terrain using seeded Perlin/simplex noise (plains, forest, mountains, water, desert, fertile)
- Assign movement costs and base resource yields per tile
- Implement world seed → deterministic output
- Create initial SQLite schema for `worlds` and `snapshots` tables
- CLI to generate a world from a seed and print basic stats

**Acceptance Criteria:**
- Running `python -m worldsim generate --seed 12345` produces a valid 256×256 world file
- Running it twice with the same seed produces identical output
- Output includes terrain type breakdown (count per type)
- A simple text rendering of the world is viewable (ASCII map)

---

### Sprint 2 — Settlements & Basic Population Dynamics

**Duration:** 2 weeks
**Deliverable:** Settlements that grow, consume food, and die based on resource balance.

**Tasks:**
- Define Settlement entity (id, name, population, territory, resource_inventory, food_stock)
- Implement population growth based on food surplus
- Implement starvation/death when food is insufficient
- Settlements spawn at fixed coordinates with initial setup
- Implement basic resource consumption (workers consume food)
- Add territory claiming (3×3 initial, grows with action)
- Update SQLite schema with `settlements` table

**Acceptance Criteria:**
- A settlement spawns with 10 workers and grows to +1 population per 24 ticks if food is positive
- If food runs out, population decreases by 1 per 48 ticks
- If population reaches 0, the settlement dies
- Territory expands by one ring when a "claim_territory" action is executed
- Territory ownership is stored in tile data and persisted

**Dependencies:** Sprint 1 (world grid must exist)

---

### Sprint 3 — Buildings, Roads & Infrastructure

**Duration:** 2 weeks
**Deliverable:** Settlements can construct buildings and roads; infrastructure provides functional benefits.

**Tasks:**
- Define building types: Farm (+food), Sawmill (+wood), Mine (+stone/metal), Granary (food storage)
- Implement building construction with resource cost
- Buildings occupy tiles and provide ongoing output
- Implement road networks (connectivity improves worker efficiency)
- Roads reduce movement cost between owned tiles
- Road network enables trade route establishment (Sprint 4)
- Add building construction/destruction logic
- Persist buildings/roads in SQLite

**Acceptance Criteria:**
- A settlement can build a Farm (cost: 5 wood, 3 stone) on a Plains tile
- Farm produces +2 food per tick
- Buildings can be destroyed if the tile is lost
- Roads connect owned tiles; movement cost on roads is 50% of normal
- Road network is contiguous; disconnected roads are flagged
- Build queue is visible in UI inspector (future-ready)

**Dependencies:** Sprint 2 (settlements and resource inventory)

---

### Sprint 4 — Economy, Resource Production & Trade

**Duration:** 2 weeks
**Deliverable:** Resources flow between settlements via trade routes; economies can thrive or collapse.

**Tasks:**
- Implement resource production (buildings generate yields that go into settlement inventory)
- Implement resource consumption (population eats food, workers require tools)
- Implement inter-settlement trade: establish trade routes, transfer resources
- Trade value computed from production efficiency and distance
- Resource scarcity leads to economic slowdown/poverty
- Economic collapse: if inventory < 0 for 48 ticks → population loss
- Add SQLite tables: `resources`, `trade_routes`

**Acceptance Criteria:**
- A Farm produces 2 Food/tick and stores in settlement inventory
- A settlement with 0 Food for 48 ticks begins losing population
- Two settlements can establish a trade route if connected by roads
- Trade route transfers 1 resource/tick from source to destination
- Economic metrics are tracked and stored (resource surplus, production efficiency)

**Dependencies:** Sprint 3 (buildings and roads)

---

### Sprint 5 — Disasters, Death & Recovery

**Duration:** 2 weeks
**Deliverable:** Environmental disasters and civilizational collapse/recovery mechanics.

**Tasks:**
- Implement natural disasters: Drought (reduces farm yield), Fire (destroys forest tiles), Plague (kills population)
- Implement disaster frequency based on climate/seasonal cycles
- Implement civilization collapse: triggers when population < 1 OR happiness < 0.1 for 100 ticks
- Implement recovery path: a new settlement can form from ruins after 500 ticks (spontaneous generation)
- Add happiness/stability system: affected by food surplus, building quality, neighbor relations
- Implement post-disaster recovery mechanics: faster growth on reclaimed ruins

**Acceptance Criteria:**
- Drought reduces all farms' yield by 50% for 200 ticks
- Fire destroys a Forest tile and its Sawmill (if present)
- Plague kills 30% of population in affected settlements
- If a settlement collapses (population = 0), its territory becomes neutral ruins
- After 500 ticks of ruin, a new settlement may spawn (10% chance per 100 ticks)
- Happiness decays if food surplus is below 0 for >10 ticks
- Recovery rate is 2x on tiles adjacent to the former capital

**Dependencies:** Sprint 4 (economy must be stable)

---

### Sprint 6 — Persistence, Save/Load & Simulation Clock

**Duration:** 2 weeks
**Deliverable:** World can be saved at any point and reloaded; time control (pause, speed, step).

**Tasks:**
- Implement full world serialization to SQLite (`snapshots` table with compressed JSON state)
- Implement world load from snapshot: restores tile state, settlements, agents, resources
- Implement simulation clock: ticks, seasons, years (128 ticks = 1 season, 512 ticks = 1 year)
- Implement time controls: pause, step-forward (1 tick), accelerate (1x, 2x, 5x, 10x)
- Implement auto-save every 500 ticks (configurable)
- Implement God Mode action logging: record all interventions with before/after state
- Add CLI: `python -m worldsim simulate --seed 12345 --ticks 10000 --save-interval 500`

**Acceptance Criteria:**
- `python -m worldsim save --world-id abc123` writes the full state to SQLite
- `python -m worldsim load --world-id abc123` restores the world to exact saved state
- Simulation clock advances by tick; season/year updates correctly
- Pausing freezes the world; stepping advances by 1 tick
- God Mode actions are logged to `god_events` table with before/after states
- Auto-save works: run 500 ticks → world.db has a snapshot at tick 500

**Dependencies:** All previous sprints (world, settlements, buildings, economy, disasters)

---

### Phase 1 Final Deliverable: Living Ant Farm (Milestone 1)

By end of Sprint 6, the user can:
1. Generate a seeded, deterministic world
2. Watch settlements autonomously grow, trade, suffer disasters, collapse, and recover
3. Save/reload the world at any time
4. Use God Mode to interfere (spawn resources, cause disasters)
5. Observe the full lifecycle of civilizations over 10,000 simulated years

---

## Phase 2: Autonomous Settlements (Sprints 7–11)
**Goal:** Introduce the agent abstraction layer and rule-based decision-making that the RL policy will later plug into.

---

### Sprint 7 — Agent Abstraction & Observation/Action Space

**Duration:** 2 weeks
**Deliverable:** Settlements are controlled by agents that produce actions based on observation vectors.

**Tasks:**
- Implement `Agent` interface: `observe(world) → action_idx`
- Define observation vector (60-dimensional float list) per settlement
- Define discrete action space (60 actions) per settlement
- Implement rule-based agent that maps observations → actions
- Wire rule-based agent into the simulation loop
- Create `agent_history` SQLite table for experience logging
- Document the full observation and action space specifications

**Acceptance Criteria:**
- The simulation loop calls `agent.observe(world)` and `agent.decide(obs)` each tick
- Observation vector contains normalized settlement state (0.0–1.0 floats)
- Action IDs map to concrete behaviors (Action 1 = Build Farm on best tile, etc.)
- A rule-based agent makes reasonable decisions: if food deficit → build farm
- All agent experiences (obs, action, reward, next_obs) are logged to SQLite
- The agent abstraction is swappable: rule-based, then later RL models

**Dependencies:** Sprint 6 (must have full world state + persistence)

---

### Sprint 8 — Rule-Based Baseline Agent

**Duration:** 2 weeks
**Deliverable:** A competent rule-based agent that plays the game well enough to validate the loop.

**Tasks:**
- Implement decision trees for: resource gathering, building construction, expansion, trade, defense
- Prioritize actions based on urgency: famine > growth > expansion > economy > infrastructure
- Implement basic strategic behaviors: scout neighbors, claim high-yield tiles
- Add random exploration noise (epsilon-greedy style): 10% chance of a random action
- Add per-settlement personality vectors that bias decision thresholds
- Test against benchmark worlds to establish baseline performance metrics

**Acceptance Criteria:**
- Rule-based agent survives 5,000+ ticks in 90% of benchmark worlds
- Agent actively seeks out and claims high-yield tiles
- Agent establishes trade routes when neighbors exist
- Agent builds a mix of buildings proportional to resource availability
- Personality vectors produce visibly different strategies (aggressive vs. peaceful)
- Performance metrics logged: survival time, population peak, final resource balance

**Dependencies:** Sprint 7 (agent abstraction complete)

---

### Sprint 9 — Multiple Settlements & Competition

**Duration:** 2 weeks
**Deliverable:** 5+ settlements compete on the same map, leading to emergent conflict and cooperation.

**Tasks:**
- Spawn multiple settlements at game start (5–10 per world)
- Implement neighbor detection: settlements within radius become neighbors
- Implement territory overlap: overlapping claims become contested tiles
- Implement basic conflict: raiding neighbors' resource tiles
- Implement cooperation: trade routes between friendly neighbors
- Set neighbor relation state (hostile/friendly/neutral) based on recent actions
- Record inter-settlement interactions in events log

**Acceptance Criteria:**
- 5 settlements start on a 256×256 map without overlapping territory
- Neighbors are detected dynamically based on distance
- Raiding a neighbor's Farm reduces its output for 200 ticks
- Trade routes form naturally between neighbors with complementary resources
- Hostile relations decay over time unless re-triggered
- Events log captures: "Settlement A raided B", "Settlement C and D established trade"

**Dependencies:** Sprint 8 (competent single-agent baseline exists)

---

### Sprint 10 — Diplomacy & Trade Decisions

**Duration:** 1.5 weeks
**Deliverable:** Settlements make diplomatic choices that affect long-term outcomes.

**Tasks:**
- Implement alliance formation: mutual benefit trade agreements
- Implement conflict escalation: repeated raids → war declaration
- Implement peace treaties: stop hostilities in exchange for tribute
- Trade decisions are part of the action space (establish_route, accept_deal)
- Implement reputation system: neighbors remember past interactions
- Track diplomatic events: treaty signing, war declaration, peace talks

**Acceptance Criteria:**
- Settlements form alliances when trade is mutually beneficial for 3 consecutive trades
- War is declared if a neighbor is raided 3 times within 500 ticks
- Peace treaties require both parties to send "peace offer" actions
- Reputation decays by 0.1 per 100 ticks of non-interaction
- Diplomatic events are logged and visible in event feed

**Dependencies:** Sprint 9 (multi-settlement dynamics established)

---

### Sprint 11 — Emergent Specialization & Strategy Differentiation

**Duration:** 1.5 weeks
**Deliverable:** Settlements develop distinct strategies and specialized roles without being told to.

**Tasks:**
- Implement personality-driven strategy emergence: 5 preset archetypes (agricultural, mining, trading, military, balanced)
- Personalities bias action selection but don't fully constrain (randomness remains)
- Track strategy evolution: log which archetype is dominant at tick 1000, 5000, etc.
- Settlement identity includes a strategy label derived from its building mix and actions
- Implement a "strategy memory" that stores successful patterns per personality

**Acceptance Criteria:**
- Settlements with "trading" personality build more markets and trade routes
- Settlements with "mining" personality claim mountain tiles and build mines heavily
- Settlements with "military" personality train units and raid neighbors
- At least 3 distinct strategies emerge in 80% of benchmark worlds
- Strategy labels are visible in UI (future-ready) and logged

**Dependencies:** Sprint 10 (diplomacy enables specialization)

---

## Phase 3: Machine Learning (Sprints 12–18)
**Goal:** Train a reinforcement learning policy that demonstrably outperforms the rule-based baseline.

---

### Sprint 12 — ML Environment & State Representation

**Duration:** 2 weeks
**Deliverable:** A Gymnasium-compatible environment that wraps the simulation engine.

**Tasks:**
- Define `WorldSimEnv(gym.Env)` class with `reset()`, `step(action)`
- Map observation vector to a numpy array (shape: (60,))
- Map action space to `gym.spaces.Discrete(60)`
- Implement reward function matching Section 6.4
- Implement episode termination (all settlements dead, 5000 ticks reached)
- Write headless simulation runner: `--headless --episodes 100`
- Unit tests for state vectorization and reward computation

**Acceptance Criteria:**
- `env.reset()` returns a valid observation vector + info dict
- `env.step(action_int)` returns `(obs, reward, done, truncated, info)` correctly
- Reward is in [-1.0, +1.0] range and normalized per tick
- Headless mode runs 100 episodes in under 60 seconds on dev machine
- Reward function tests verify correct values for known scenarios

**Dependencies:** Sprint 11 (stable simulation with rule-based agent as reference)

---

### Sprint 13 — Reward System & Experience Collection

**Duration:** 1.5 weeks
**Deliverable:** A robust reward system with logged experiences ready for training.

**Tasks:**
- Refine reward shaping: add penalties for redundant actions, bonuses for efficient combos
- Implement reward normalization: rolling average over last 1000 ticks
- Store experiences in RAM replay buffer (10,000 capacity)
- Add reward visualization: plot reward over time for benchmark episodes
- Add reward hacking detection: flag agents that exploit a single mechanic for reward
- Log reward breakdowns (positive vs negative components)

**Acceptance Criteria:**
- Reward breakdown is logged per tick: {"food_positive": 0.1, "construction_negative": -0.5}
- Replay buffer stores 10k (obs, action, reward) tuples per settlement
- Reward hacking detection triggers if any agent earns >80% of reward from one source
- Reward plot shows clear learning curve across 10 benchmark episodes

**Dependencies:** Sprint 12 (environment must be stable)

---

### Sprint 14 — First Learning Agent & Training Loop

**Duration:** 2 weeks
**Deliverable:** A PPO agent that trains on the headless environment and learns a policy.

**Tasks:**
- Integrate Stable-Baselines3 PPO with the Gymnasium environment
- Implement training script: `python -m worldsim rl train --episodes 500`
- Log training metrics: episode reward, policy loss, entropy
- Save first checkpoint at episode 500 → `policies/policy_gen1.pt`
- Implement basic evaluation: run policy on 10 benchmark worlds, log survival metrics
- Compare trained agent vs rule-based baseline on identical worlds

**Acceptance Criteria:**
- PPO agent trains for 500 episodes without crashing
- First policy checkpoint is saved to SQLite + .pt file
- Trained agent achieves non-negative average reward on benchmark worlds
- Trained agent outperforms rule-based baseline in 60%+ of benchmark worlds on survival time

**Dependencies:** Sprint 13 (reward system validated)

---

### Sprint 15 — Parallel Simulation Training

**Duration:** 2 weeks
**Deliverable:** Training scales to multiple parallel environments for faster iteration.

**Tasks:**
- Implement vectorized environments: `VecEnv` with 4 parallel worlds
- Use `multiprocessing.Pool` for CPU-bound simulation workers
- Implement batching: all 4 environments step simultaneously
- Track which CPU cores are utilized for training
- Log total wall-clock time per 100 episodes

**Acceptance Criteria:**
- 4 parallel environments run without inter-process crashes
- Training time is cut by ~75% compared to sequential training
- CPU utilization stays under 80% on all cores (no overheating)
- Checkpointing works correctly with parallel environments

**Dependencies:** Sprint 14 (single-agent training works)

---

### Sprint 16 — Policy Checkpoints & Model Versioning

**Duration:** 1.5 weeks
**Deliverable:** Policies are versioned with metadata and can be loaded for evaluation.

**Tasks:**
- Define policy metadata schema: generation, training_episodes, avg_reward, hyperparams
- Store policy checksums to detect corruption
- Implement `worldsim rl evaluate --policy-id gen3 --world-seeds 10000-10010`
- Store evaluation results in `training_runs` table
- Implement policy rollback: load any previous checkpoint
- Log policy comparison results (gen N vs gen N-1)

**Acceptance Criteria:**
- Policies are saved with full metadata in SQLite
- Any checkpoint can be loaded and evaluated on demand
- Evaluation results are logged per-world with survival/population metrics
- Rollback to gen1 works and produces identical results to original gen1 run

**Dependencies:** Sprint 15 (parallel training operational)

---

### Sprint 17 — Compare Trained Agent vs Baseline

**Duration:** 1.5 weeks
**Deliverable:** A rigorous A/B comparison showing RL agent performance.

**Tasks:**
- Define 20 fixed benchmark worlds (seeds 50000–50019)
- Run both rule-based and RL agent (gen3) on all 20 worlds
- Collect metrics: avg survival time, max population, final territory, resource efficiency
- Generate comparison report (bar charts, win/loss summary)
- Log results to SQLite: `training_runs` table with `agent_type` column

**Acceptance Criteria:**
- All 20 benchmark worlds run for both agents without errors
- RL agent achieves higher avg reward than baseline on 15+ benchmark worlds
- Report shows statistically significant improvement (p < 0.05)
- Results are saved and queryable via CLI

**Dependencies:** Sprint 16 (policy versioning in place)

---

### Sprint 18 — Measure Learning Progress

**Duration:** 1 week
**Deliverable:** A definitive answer to "Are agents actually getting better?"

**Tasks:**
- Train 3 generations of policy: gen1 (500 ep), gen2 (1000 ep), gen3 (2000 ep)
- Run all 3 on the 20 benchmark worlds
- Plot learning curve: avg survival time per generation
- Check for regression: does gen3 ever do worse than gen1 on the same seed?
- Create metrics dashboard query interface

**Acceptance Criteria:**
- gen3 > gen2 > gen1 on avg survival time (monotonic improvement)
- No regression detected on any benchmark world (gen3 does not lose to gen1)
- Learning dashboard can be queried: `--metric survival --gen 1..3 --bench 50000`
- Improvement is >20% from gen1 to gen3 on avg

**Dependencies:** Sprint 17 (comparison framework complete)

---

## Phase 4: Evolution (Sprints 19–24)
**Goal:** Move from single-policy training to populations of policies that
are selected, mutated, and evolved across generations — with self-play
competition and hardened anti-reward-hacking defenses.

> **Status note (written after Phase 3):** several roadmap items landed
> early. Strategy memory exists since Sprint 11; reward-hacking *detection*
> since Sprint 13; multi-generation dashboards and regression detection since
> Sprint 18. Phase 4 builds the missing pieces on top of those foundations.

---

### Sprint 19 — Populations & Generational Training

**Duration:** 1 week
**Deliverable:** A population manager that trains N policies per generation
across disjoint world-seed sets, tracks lineage in the registry, and selects
the generation champion.

**Tasks:**
- `PopulationManager`: N candidate policies per generation, each trained on a
  distinct seed subset (parallel pipeline reused)
- Lineage tracking: parent generation + seed assignment stored per candidate
- Champion selection by mean evaluation return across the population's eval
  seeds; champion registered as the generation's official checkpoint
- CLI: `rl evolve --population 4 --generations 3 --timesteps-per-candidate T`

**Acceptance criteria:**
- Population of 4 candidates × 2+ generations trains without crashes
- Registry records parent/lineage per candidate
- Champion selection is deterministic given identical results

---

### Sprint 20 — Selection, Mutation & Strategy Evolution

**Duration:** 1–2 weeks
**Deliverable:** Evolutionary pressure — candidates derived from the previous
champion via parameter-space mutation, plus strategy-level evolution.

**Tasks:**
- Parameter mutation: Gaussian noise injection into policy weights (strength
  configurable), preserving network topology
- Elitism: champion always survives to the next generation unchanged
- Mutation lineages recorded (parent checksum → child checksum)
- Strategy-evolution report: how archetype behavior mixes shift across
  generations (uses Sprint 11 labels)

**Acceptance criteria:**
- Mutated children load and run identically-shaped networks
- Elite never regresses below its recorded score within a generation
- Lineage chains are queryable end-to-end (root → … → current)

---

### Sprint 21 — Cross-Generation Learning

**Duration:** 1 week
**Deliverable:** Knowledge transfer between generations beyond raw weights.

**Tasks:**
- Strategy memory aggregation: merge per-generation EMA action-reward tables
  into a population-level prior consumed at reset
- Curriculum seeding: later generations train on world-seed distributions
  weighted toward earlier failures (regression seeds first)

**Acceptance criteria:**
- Aggregated priors demonstrably change early-episode behavior
- Failure-weighted curricula measurably reduce regressions on previously
  failing seeds vs uniform sampling

---

### Sprint 22 — Self-Play / Civilization Competition

**Duration:** 2 weeks
**Deliverable:** Two or more learned policies competing inside one world.

**Tasks:**
- Multi-controller env support: k settlements driven by k distinct policies
- Head-to-head evaluation mode (policy A vs policy B, paired worlds)
- Competitive metrics: relative survival, territory share, resource share
- Update `rl compare` to use true head-to-head instead of baseline deltas

**Acceptance criteria:**
- Two policies co-exist in one world without controller interference
- Head-to-head results are deterministic under fixed seeds
- `training_runs` records head-to-head matches

---

### Sprint 23 — Strategy Discovery

**Duration:** 1–2 weeks
**Deliverable:** The system surfaces strategies nobody scripted.

**Tasks:**
- Behavioral clustering over rollout trajectories (building mix, action
  histograms) to detect emergent play styles
- Novelty detection: flag behaviors not matching any known label/archetype
- Discovery log: named strategies persisted with exemplar seeds/checkpoints
- Integration with Sprint 11 labels as weak supervision

**Acceptance criteria:**
- At least one reproducible non-scripted strategy identified and documented
- Discovered strategies re-instantiable from stored exemplars

---

### Sprint 24 — Anti-Reward-Hacking Systems

**Duration:** 1 week
**Deliverable:** Beyond Sprint 13's detection: automated response.

**Tasks:**
- Automated response ladder: warn → penalize (reward scaling) → quarantine
  (reject candidate from selection)
- Exploit regression suite: known exploits replayed as tests
- Hacking telemetry added to dashboard output

**Acceptance criteria:**
- A seeded synthetic exploiter is automatically quarantined
- Known-exploit replays fail loudly in CI (slow tier)

---

## Phase 5: AI Reasoning (Sprints 25–30)
**Goal:** Optional Ollama-backed strategic reasoning layered ON TOP of ML —
LLMs advise; deterministic simulation and validated actions remain law.

**Phase principles (roadmap §19):**
- LLMs are optional strategic reasoning, never the physics engine.
- The LLM may never secretly modify simulation state (roadmap §23.5).
- Inference is O(slow) — strategic reasoning only, never per-decision.
- Responsibility split: deterministic rules → mechanics; ML → learned
  policy; LLM → advisory intent (validated in S28); user → God Mode.
- Every LLM touchpoint degrades gracefully: the simulation and all training/
  benchmark paths never block or crash on LLM unavailability.

---

### Sprint 25 — Ollama Integration

**Duration:** 1 week
**Deliverable:** A zero-dependency Ollama client with graceful degradation,
config file + CLI flag overrides, availability probing, and manual prompt
tooling.

**Tasks:**
- `llm.py` — `LLMConfig` dataclass (host, model, temperature, timeout_s,
  num_predict) loaded from `data/world_sim/llm_config.json`; CLI flags
  override file values
- `OllamaClient`: `generate(prompt)` / `chat(messages)` against
  `POST /api/generate` and `/api/chat`; `is_available()` health probe via
  `GET /api/tags`; `list_models()`
- Graceful-degradation contract: every method returns an `LLMResult`
  (`ok`, `text`, `error`, `elapsed_s`) — never raises into sim/training code
- stdlib `urllib.request` client (zero new dependencies)
- CLI: `worldsim llm status` (server reachability, installed models,
  config echo), `worldsim llm ask --prompt "..."` for manual probing

**Acceptance criteria:**
- `llm status` reports server state + models against a running Ollama
- `llm ask` returns real model output end-to-end
- Unavailable-server case degrades to a clean error result (no traceback)
- Config precedence: flags > JSON file > defaults (unit-tested)
- All tests pass without Ollama installed (mocked HTTP)

---

### Sprint 26 — Settlement State Summarization

**Duration:** 1 week
**Deliverable:** Compact, token-budgeted world/settlement summaries suitable
for LLM prompts.

**Tasks:**
- `summaries.py`: settlement summary builder (population, food, buildings
  by type, territory, relations, recent events, strategy label) at multiple
  verbosity tiers (tiny ≤200 tokens / full)
- World summary: top-level stats + per-settlement one-liners
- Deterministic formatting (same state → byte-identical summary; pure
  function of `(state, tick)`)
- Unit tests pinning exact formats

**Acceptance criteria:**
- Summary fits stated token budget on tiny worlds
- Byte-identical across runs for identical states
- Missing/None fields render as explicit placeholders, never crash

---

### Sprint 27 — Strategic Reasoning

**Duration:** 1 week
**Deliverable:** Advice generation: summaries in → structured strategic
priorities out.

**Tasks:**
- Prompt templates (system + user) requesting advice in parseable form
- `advise(settlement_summary)` → parsed advice object (priorities list,
  rationale); malformed output degrades to "no advice"
- Advice is advisory-only this sprint: logged + surfaced, never executed

**Acceptance criteria:**
- Round-trip on live model produces parseable advice for ≥90% of prompts
  (live-gated test)
- Malformed/garbage model output never crashes or executes

---

### Sprint 28 — LLM → Agent Intent → Validated Actions

**Duration:** 1–2 weeks
**Deliverable:** Advice maps onto the frozen 62-action space behind
mandatory validation.

**Tasks:**
- Intent schema: advice phrases → candidate action IDs (+ arguments)
- Validation layer: every candidate checked against affordability, terrain,
  ownership, cooldowns (reuses existing mechanic validators); invalid
  intents dropped with telemetry
- `LLMDrivenAgent`: Agent implementation whose decisions come from LLM
  intent when available, rule-based fallback otherwise

**Acceptance criteria:**
- No LLM output can execute an action that violates world rules
- LLMDrivenAgent survives full episodes with LLM down (pure fallback)
- Intent→action mapping unit-tested against the frozen action space

---

### Sprint 29 — Periodic AI Reasoning

**Duration:** 1 week
**Deliverable:** Budget-aware scheduling so slow inference never dominates.

**Tasks:**
- Scheduler: reason every N ticks / on important events / only for
  struggling settlements (all three modes configurable)
- Concurrency guard: at most one in-flight LLM call per world; sim loop
  never blocks (advice applied next decision cycle)

**Acceptance criteria:**
- Tick rate unaffected while LLM runs in background
- Struggling-settlement mode demonstrably targets low-happiness/food
  settlements first

---

### Sprint 30 — ML-only vs ML + LLM Comparison

**Duration:** 1 week
**Deliverable:** Paired comparison answering "does advice help?"

**Tasks:**
- Reuse paired-per-seed methodology: same worlds, ML-only champion vs
  LLMDrivenAgent variant
- Metrics: survival, peak population, territory share, reward; significance
  via existing permutation tests
- Report generation like Sprint 17

**Acceptance criteria:**
- Full pipeline runs on ≥10 paired worlds without errors
- Honest verdict either way, recorded in training_runs

---

## Phase 6: Civilization Simulation (Sprints 31–37)
**Goal:** Depth — technology/eras, advanced economies, warfare, long-horizon
history. Several items extend existing systems (diplomacy, collapse).

| Sprint | Theme | Notes |
|---|---|---|
| 31 | Technology & civilization eras | Tech tree, era gates on buildings/actions |
| 32 | Advanced economies | Markets/prices beyond fixed trade units |
| 33 | Large-scale infrastructure | Project-scale construction, road networks spanning worlds |
| 34 | Advanced diplomacy | Treaties with clauses, federations (extends S10) |
| 35 | Warfare proper | Units, battles, sieges (raids exist since S9) |
| 36 | Collapse/recovery depth | Extends S5 ruins/happiness with era mechanics |
| 37 | Long-term historical simulation | Stability + performance at 100k+ ticks |

### Sprint 38 — God Controls Polish (detailed)

**Duration:** 1 week
**Deliverable:** §16 surface audit completed for controls; every
intervention audited; freeze protection; catastrophic-action guard.

**Tasks:**
- New controls: `god_spawn_settlement` (free tile near target, rule
  agent auto-registered, deterministic/generated names),
  `god_toggle_freeze` (time stops entirely for a settlement — no
  decisions, production, growth, decay, or scarcity death),
  `god_bless_happiness` (happiness→1.0, misery counters cleared)
- Audit trail (§16.3): EVERY god action logs a "divine" event
  prefixed "GOD:" alongside its before/after dicts
- Safety (§16.4): CLI `--force` required for smite ≥ 25 population
- Persistence: `Settlement.frozen` flag round-trips
- Bug fixed en route: `cmd_step`/`cmd_god` silently dropped highways +
  treaties on load-step-save (the S33/S34 `*_,` unpack cost)

**Acceptance criteria:**
- Frozen settlements survive anything and change not at all
- Every intervention leaves exactly one divine event
- Catastrophic smite refused without --force; frozen contract untouched

### Sprint 39 — Disaster Toolkit (detailed)

**Duration:** 1 week
**Deliverable:** Manual disaster authoring beyond random events.

**Tasks:**
- `god_trigger_disaster(type, x, y, radius, duration)`: drought / fire /
  flood / earthquake on demand with full parameter control
- Zone preview in before/after (affected settlements + tiles at risk)
- Divine audit integration + CLI wiring (`--action trigger_disaster`)
- Deterministic application identical to random-event paths (reuses the
  Sprint 5 `_apply_*` mechanics)

**Acceptance criteria:** authored disasters behave identically to random
ones; affected-settlement lists deterministic; audited.

### Sprint 40 — Resource Manipulation Depth (detailed)

**Duration:** 1 week
**Deliverable:** Spawn/remove/bless at scale with region targeting.

**Tasks:**
- Region-targeted operations: circle/rectangle selectors over tiles
- Mass bless/strip across settlements matching a filter (alive/archetype)
- Per-tile resource yield overrides (blessed land) persisted on World

**Acceptance criteria:** region math deterministic; overrides round-trip;
audited; frozen contract untouched.

### Sprint 41 — Terrain Manipulation (detailed)

**Duration:** 1 week
**Deliverable:** Terraform tiles.

**Tasks:**
- `god_terraform(x, y, terrain)` with region variant; invalidates all
  derived caches (food yields, movement); buildings on invalid terrain
  handled (decay or removal policy documented)
- Persistence via terrain array (already serialized)

**Acceptance criteria:** cache invalidation proven (no stale yields);
audited; determinism preserved.

### Sprint 42 — Nuclear Events (detailed)

**Duration:** 1 week
**Deliverable:** Mass destruction with lasting contamination.

**Tasks:**
- `god_nuke(x, y)`: destroys improvements/population in radius, leaves a
  contamination zone (decades-long debuff on yields + happiness)
- Fallout decays deterministically; contamination visible in summaries
- Requires --force twice (§16.4)

**Acceptance criteria:** contamination persists then clears on schedule;
survivors migrate per Sprint 36 rules; audited.

### Sprint 43 — Timeline Branching / Undo (detailed)

**Duration:** 1 week
**Deliverable:** Reverse/replay simulation (§16.2).

**Tasks:**
- Auto-snapshot before every intervention (§16.4) into the store
- `worldsim undo --world-id` reverts to the pre-intervention snapshot
- Branch ids so alternate timelines coexist

**Acceptance criteria:** undo restores exact prior state (byte-equal
summary); branches replay independently from the same seed.

**Duration:** 1 week
**Deliverable:** Stability + performance at 100k+ ticks: bounded memory,
epoch history, memoized hot paths, soak-tested.

**Tasks:**
- Bounded structures: event log caps at EVENT_LOG_MAX=20k (oldest
  dropped; events are advisory/log-only so physics never depended on
  them); experience buffer capped at 50k rows
- Epoch history: every HISTORY_INTERVAL_TICKS=500 the sim records a
  compact record {tick, settlements_alive, total_population,
  wars_active, routes_active, mean_happiness, prices} — ~200 records
  per 100k ticks
- Performance: food_income/food_capacity memoized per tick via the
  existing _cached machinery
- Slow-tier soak tests: 10k ticks within a time budget, traced memory
  growth bounded over warm ticks, total-collapse runs keep ticking,
  byte-determinism at horizon

**Acceptance criteria:**
- ≥150 ticks/s on a 3-settlement size-64 world (100k ticks ≈ minutes)
- Memory growth roughly flat once warm (<100 MB per 2k traced ticks)
- Determinism preserved at horizon; frozen contract untouched

### Sprint 44 — Advanced Visualization (detailed)

**Duration:** 1 week
**Deliverable:** §17 world map: ASCII rendering with overlays + PNG
export, settlement panels.

**Tasks:**
- `visualization.py`: `render_ascii_map` with deterministic glyph
  priority (contamination ! > settlement initial > ruin X > road # >
  building b > terrain glyph), optional crop window, header with
  tick/seed; `render_settlement_panel`; `export_map_png` (fixed
  terrain palette, population-scaled settlement markers, roads/
  buildings/ruins marks)
- CLI `worldsim map --world-id [--x0 --y0 --x1 --y1] [--png] [--panels]`
- Determinism: byte-identical maps AND byte-identical PNG bytes for
  identical worlds

**Acceptance criteria:**
- Overlays proven (road/building/ruin/contamination/settlement glyphs)
- PNG round-trips byte-exact across exports of the same world
- Crop windows clamp to the grid gracefully

### Sprint 45 — Civilization Histories (detailed)

**Duration:** 1 week
**Deliverable:** Per-civilization sagas reconstructed from the event
log + population curves per epoch.

**Tasks:**
- `histories.py`: `build_chronicle` — chronological story lines from
  founding through every event naming the settlement (techs, eras,
  wars, battles, treaties, disasters, divine interventions, migration,
  recovery), with fall/rebirth cross-references; `civilizations_summary`
  one-liners (alive/fallen, era, techs, battles); `population_curves`
  from S37 epoch history
- Epoch history extended with a per-settlement `populations` map
- Divine events now carry actor_ids so god interventions appear in the
  affected settlements' chronicles
- CLI `worldsim chronicle --world-id [--settlement-index] [--png]`
  (matplotlib population chart export)

**Acceptance criteria:**
- Chronicles deterministic across identical seeds; work across
  save/load for persisted events (curves are RAM-only this sprint)
- Population curves pad mid-run births correctly; frozen contract
  untouched

### Sprint 46 — Event Timeline (detailed)

**Duration:** 1 week
**Deliverable:** Whole-world chronological timeline over the persisted
event log, with category filters and frequency histograms.

**Tasks:**
- `timeline.py`: fixed category taxonomy (warfare/diplomacy/
  civilization/trade/divine/disasters/other); `build_timeline` with
  AND-combined type/category/actor/since filters and oldest-first
  limit; `render_timeline` with human date stamps;
  `category_histogram` — zero-filled per-window counts
- `export_event_histogram`: stacked bars per category per window,
  fixed colors, byte-deterministic
- CLI `worldsim timeline --world-id [--types] [--categories] [--since]
  [--limit] [--no-dates] [--png --window]`

**Acceptance criteria:**
- Deterministic filtering/ordering/histograms across identical seeds
- Works across save/load (event log persisted)
- Chart exports byte-identical for identical worlds

### Sprint 47 — Learning Dashboard (detailed)

**Duration:** 1 week
**Deliverable:** Consolidated offline training-health dashboard across
policy generations.

**Tasks:**
- `build_learning_dashboard(store, gens)`: reads ONLY stored artifacts
  (latest checkpoint per generation + its train-time `_summary.json`) —
  no models loaded, no rollouts; per-generation return trend vs previous,
  entropy-collapse detection (threshold 0.1), explained-variance bands
  (good ≥0.7 / ok ≥0.4 / poor), KL warning (>0.03)
- Overall status aggregation: regressed > watch > healthy; missing
  generations reported without crashing
- `generate_health_dashboard_plot`: 2×2 panels (return, final entropy
  with collapse line, explained variance with good line, ticks/second)
- `render_health_dashboard_markdown` + CLI `rl health --gens --png
  --markdown`

**Acceptance criteria:**
- Flags proven against synthetic summaries (collapse/regression/KL/EV);
  overall status stays "regressed" even after later recovery
- Fast: offline by construction; works for any generations present;
  frozen contract untouched

### Sprint 48 — Replay System (detailed)

**Duration:** 1 week
**Deliverable:** Reconstruct past worlds from the persisted snapshot
trail (§18 offline catch-up / step-by-step).

**Tasks:**
- `timewalk.py` (named around the existing RL `replay.py`): ordered
  frame listing, exact-tick frame loading via
  `Simulation.from_state_json`, lazy stride iteration, animated GIF
  export through recorded history
- CLI `worldsim replay --world-id [--at tick] [--gif --fps --stride]
  [--list]`
- Frames are independent simulations: mutating one never touches stored
  state or other frames

**Acceptance criteria:**
- Loading tick T reproduces exactly the world saved at T; replayed
  frames mutate freely without side effects; GIF exports bounded on
  long runs (stride + max_frames); frozen contract untouched

### Sprint 49 — World Comparison (detailed)

**Duration:** 1 week
**Deliverable:** Structural diff between two saved worlds — the natural
companion to Sprint 43 branch timelines.

**Tasks:**
- `worldcompare.py`: `compare_worlds(sim_a, sim_b)` — per-settlement
  field-level diff (population/era/techs/army/territory/resources),
  world counters (wars/alliances/treaties/highways/zones/routes/ruins),
  event divergence keyed by (tick, type, description); `identical` flag;
  `render_compare_markdown` table report; `export_compare_chart` grouped
  bars per shared settlement (byte-deterministic)
- CLI `worldsim compare --a W1 --b W2 [--png] [--markdown]`

**Acceptance criteria:**
- Same-seed same-tick worlds compare identical; god interventions and
  diverged histories produce precise field/event diffs; deterministic
  outputs; frozen contract untouched

### Sprint 50 — Long-Running Autonomous World (detailed)

**Duration:** 1 week
**Deliverable:** Launch a world and let it live: continuous autonomous
simulation with periodic status, autosaves, and a living-world report
bundle at the end (roadmap finish line).

**Tasks:**
- `simulate --report-dir DIR`: after any run, export the full living-
  world bundle — world map PNG, population curves (epoch history),
  event histogram, chronicle.md (civilization summaries + per-
  settlement sagas); curves skipped gracefully pre-first-epoch
- Visual verification loop built from prior Phase 8 sprints:
  autosaved snapshots feed `worldsim replay --gif` for a watch-it-live
  animation of the whole run
- Long-run acceptance reuses S37 soak guarantees (bounded logs/memory,
  determinism)

**Acceptance criteria:**
- A 20k+ tick autonomous run completes with bounded memory and
  produces all bundle artifacts; same seed → byte-identical chronicles;
  replay GIF reconstructs the run from snapshots; frozen contract
  untouched

### Sprint 37 — Long-Term Historical Simulation (detailed)

**Duration:** 1 week
**Deliverable:** Stability + performance at 100k+ ticks: bounded memory,
epoch history, memoized hot paths, soak-tested.

**Tasks:**
- Bounded structures: event log caps at EVENT_LOG_MAX=20k (oldest
  dropped; events are advisory/log-only so physics never depended on
  them); experience buffer capped at 50k rows
- Epoch history: every HISTORY_INTERVAL_TICKS=500 the sim records a
  compact record {tick, settlements_alive, total_population,
  wars_active, routes_active, mean_happiness, prices} — ~200 records
  per 100k ticks
- Performance: food_income/food_capacity memoized per tick via the
  existing _cached machinery
- Slow-tier soak tests: 10k ticks within a time budget, traced memory
  growth bounded over warm ticks, total-collapse runs keep ticking,
  byte-determinism at horizon

**Acceptance criteria:**
- ≥150 ticks/s on a 3-settlement size-64 world (100k ticks ≈ minutes)
- Memory growth roughly flat once warm (<100 MB per 2k traced ticks)
- Determinism preserved at horizon; frozen contract untouched

### Sprint 36 — Collapse/Recovery Depth (detailed)

**Duration:** 1 week
**Deliverable:** Era-aware collapse (enriched ruins, refugee migration,
building decay) and knowledge recovery on re-settlement.

**Tasks:**
- `recovery.py`: `migrate_refugees` — up to half the dying population
  flees to allies/federation members (federation first, deterministic
  order, ≤3 pop absorbed per recipient), logged as "migration";
  `salvage_from` — half of non-negative stockpiles; `decay_building` —
  each scarcity-collapse event also strips one building (lowest-yield
  deterministic target)
- RuinSite enriched: records era, technologies, salvage; persisted via
  encode/decode with backward-compatible defaults
- Knowledge recovery: re-settlers inherit the ruin's technologies and
  salvage, logged as a "recovery" event

**Acceptance criteria:**
- Deterministic migration/salvage/decay across identical seeds
- Re-settled civilizations inherit knowledge + salvage (rebound faster
  than they rose)
- No migration without allies/federation; decay only under sustained
  scarcity; frozen contract untouched

### Sprint 35 — Warfare Proper (detailed)

**Duration:** 1 week
**Deliverable:** Armies, deterministic field battles between warring
pairs, sieges imposing victor terms, war exhaustion.

**Tasks:**
- `warfare.py`: Settlement gains `army` / `fort_level` /
  `siege_progress` (persisted); army upkeep consumes food, starving
  armies melt
- Three reserved military action slots WIRED (frozen IDs): TRAIN_RAIDER
  (food→army), TRAIN_DEFENDER (food+wood→army+fort), FORTIFY_BORDER
  (stone→fort, capped 3); affordability enforced in validate_action
- Battles every BATTLE_INTERVAL_TICKS per war: strength = army, defense
  × home-ground × forts; seeded rng roll; winner −15% army, loser −30%;
  attacker wins raise siege_progress, defender wins reset it
- SIEGE at 3 attacker wins: war ends, victor IMPOSES tribute-only
  treaty (fulfills Sprint 34's reserved clause), loser loses half its
  remaining army; WAR_EXHAUSTION_TICKS ends stale wars with white peace
- Summaries expose mil(army/fort/siege) tags

**Acceptance criteria:**
- Deterministic battles: same seed/tick/state → identical outcomes
- Sieges produce victor-imposed tribute treaties ending wars
- Wired actions validated at both layers; DISBAND_MILITARY etc. remain
  no-ops; frozen contract untouched (62 actions / 60 dims)
- Military fields round-trip through save/load

### Sprint 34 — Advanced Diplomacy (detailed)

**Duration:** 1 week
**Deliverable:** Formal treaties carrying clauses; federations as
derived objects over the alliance graph.

**Tasks:**
- `treaties.py`: `Treaty` dataclass (uuid5 ids, clauses list, expiry);
  three clause types — non_aggression (raids blocked between parties,
  enforced in `_raidable_targets` AND the intent validator), trade_pact
  (+25% shipments), tribute (wealthier party pays the poorer every 100
  ticks); deterministic acceptance predicate (alive, not at war,
  relation ≥ friendly threshold, no existing treaty); tribute-ONLY
  treaties reserved for victor-imposed terms
- Federations DERIVED: connected components of the alliance graph with
  ≥3 members — pure function of state, nothing persisted; members ship
  +15% to each other
- Rule hook: cadence-gated `maybe_propose_treaties` per settlement
  (Era II+, best-scoring friendly neighbor without a treaty)
- Persistence: sim.treaties serialized (13th snapshot field); summaries
  gain "Treaties:" and "Federations:" lines

**Acceptance criteria:**
- Deterministic ids/acceptance/expiry across identical seeds
- Non-aggression blocks raids at BOTH legality layers with distinct
  telemetry reason
- Trade pacts and federations measurably boost shipments; tribute moves
  resources richer→poorer on period boundaries
- Save/load round-trips treaties exactly; frozen contract untouched

### Sprint 33 — Large-Scale Infrastructure (detailed)

**Duration:** 1 week
**Deliverable:** Multi-tick inter-settlement highway projects bridging
unowned land between adjacent territories.

**Tasks:**
- `infrastructure.py`: `HighwayProject` (uuid5 ids, L-shaped deterministic
  paths from spawn to spawn, water/existing-road tiles skipped);
  `can_start_highway` legality (Era II masonry, territory adjacency,
  one project per pair); pay-as-you-go construction — sponsors spend
  stone per segment per tick, projects PAUSE when funds run dry, roads
  appear progressively; Era III sponsors lay 2 segments/tick
- Simulation: `highway_projects` field; `advance_projects()` each tick;
  `_auto_road_rule` falls through to `maybe_start_highways` when own
  network saturates (rule agents + LLM agents benefit without new
  actions)
- Effect: trade routes between completed-highway endpoints ship +30%
- Persistence: serialize/deserialize projects; summaries gain a
  "Highways: N operational, M under construction" line

**Acceptance criteria:**
- Deterministic ids/paths/progress across identical seeds
- Paused (not cancelled) when sponsor lacks stone; resumes when funded
- Highway-connected routes measurably out-ship unconnected ones
- Save/load round-trips projects exactly; frozen contract untouched

### Sprint 32 — Advanced Economies (detailed)

**Duration:** 1 week
**Deliverable:** Derived market prices; trade flows driven by valuation
gaps instead of fixed unit transfers.

**Tasks:**
- `markets.py`: prices DERIVED per resource from mean per-capita
  availability across living settlements (base × reference/(reference +
  4·avail), clamped to [0.25, 8.0]; metal base 2×). Pure function of
  state — nothing persisted, cannot desynchronize.
- Valuation gaps: donor-surplus vs receiver-scarcity ratio, deficits
  clamped at zero (collapse inventories must not divide by ~zero)
- `_trade_tick` rewrite: direction by largest valuation gap (either
  route end); shipment size = linear-in-gap up to 4 units, Era III
  donors +25% on top; shipments clamped to donor stock; dust (<0.5
  units) skipped
- Summaries: full world tier gains a "Market prices per unit" line so
  LLM advisors can reason about prices

**Acceptance criteria:**
- Determinism preserved (byte-identical runs incl. trade outcomes)
- Frozen RL contract untouched (62 actions / 60 obs dims)
- Prices respond monotonically to scarcity/abundance within bounds
- Desperate receivers get larger shipments than well-supplied partners;
  donor stock always respected

### Sprint 31 — Technology & Civilization Eras (detailed)

**Duration:** 1 week
**Deliverable:** Deterministic research accumulation, four named
technologies in fixed order, derived eras gating existing mechanics.

**Tasks:**
- `tech.py`: TECHNOLOGIES (agriculture → masonry → engineering →
  administration) with research costs; ERA_TECH_REQUIREMENTS (Era II =
  agriculture+masonry, Era III = engineering+administration);
  BUILDING_ERA_REQUIREMENTS (Mine/Granary need Era II); Era III bonuses
  (+15% farm output, +25% route transfer size)
- Settlement fields: `research_points`, `technologies`; `era` property
  derived from technologies (pure function — nothing extra to persist
  beyond the two fields)
- Simulation: `_advance_research()` per living settlement per tick
  (rate = 0.05 × population × era multiplier); "technology" and "era"
  events logged (event-triggered LLM advice reacts automatically)
- Gate enforcement at BOTH legality layers: `build_at` returns False
  below required era; `intents.validate_action` drops with
  `missing_technology_*` reason
- Persistence: encode/decode both new Settlement fields; summaries show
  era + technologies (tiny tag `eraN`, full line)

**Acceptance criteria:**
- Frozen RL contract untouched (NUM_ACTIONS == 62, OBSERVATION_DIM == 60)
- Identical seeds → identical tech timelines (byte-equal research state)
- Era gates enforced by sim handler AND intent validator
- Save/load round-trips research_points/technologies exactly

## Phase 7: God Mode Expansion (Sprints 38–43)
**Status:** Core God Mode shipped in Sprint 6 (controls, disasters,
resource manipulation, event logging). Remaining sprints are expansions:

| Sprint | Theme | Notes |
|---|---|---|
| 38 | God controls polish | Full surface area audit vs §16 of architecture doc |
| 39 | Disaster toolkit | Manual disaster authoring (beyond random events) |
| 40 | Resource manipulation depth | Spawn/remove/bless at scale, region targeting |
| 41 | Terrain manipulation | Terraform tiles (terrain is currently static) |
| 42 | Nuclear events | Mass destruction with lasting contamination |
| 43 | Timeline branching / undo | Branch snapshots into independent timelines |

## Phase 8: Living World (Sprints 44–50)
Visualization and observability: PixiJS/WebGL frontend (architecture_notes
stack), civilization histories, event timeline UI, learning-dashboard UI,
replay system, world comparison UIs, long-running autonomous world service.
*The Electron/React shell decision is deferred until this phase begins;
CLI remains the primary interface until then (decided Session 12).*

## Phase 9: Advanced Intelligence (Sprints 51–56)
Population-based training at scale, meta-learning across worlds, strategy
transfer between civilizations, deeper agent specialization, multi-level
agents (civilization ↔ settlement hierarchies), self-improving strategies.
*Depends on Phase 4's evolution infrastructure being proven.*

## Phase 10: Experimental / Future (Sprint 57+)
Procedural 3D worlds, AI-generated assets, massive parallel worlds,
distributed training, community worlds/shared experiments. Explicitly
non-goal until everything above stabilizes.

---

## Next Steps
1. ~~Start Sprint 1~~ ✅ Phases 1–3 complete (Sprints 1–18 + remediation)
2. Proceed sprint-by-sprint through Phase 4 starting at Sprint 19
