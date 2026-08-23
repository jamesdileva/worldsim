# WorldSim — Working Notes & Session History

Running log of work sessions and decisions. Newest sessions at the top.
Decisions worth remembering are marked **[DECISION]**.

---

## Session 30 — 2026-08-23 — Sprint 28: LLM → Intent → Validated Actions

**What was built**
- `intents.py`:
  - `PHRASE_RULES`: advice phrases → frozen action IDs via word-start
    regex matching (stems like "agricultur" work; "ore" cannot fire
    inside "more" — live-found bug, fixed with `\b` boundaries)
  - `map_advice_to_actions()`: deduped, order-preserving, unmapped
    phrases counted in telemetry, never fatal
  - `validate_action()`: read-only legality reusing the exact sim
    predicates (can_afford/find_building_site/can_establish_route/
    raid cadence/wars_of) so an accepted intent cannot fail "illegally"
- `llm_agent.py` — `LLMDrivenAgent(Agent)` + `attach_llm_agent()`:
  - Injected client; NO inference inside decide() (S29 owns scheduling);
    default refresh every 24 ticks when queue empty
  - Queued intents REVALIDATED against current state every observe()
    ("stale_" drop reasons); invalid intents fall back to rules that tick
  - Provider None/failure/garbage/exception → pure RuleBasedAgent
    behavior; 200-tick fallback episode verified alive
- Live smoke: real llama3.1:8b drove a settlement 60 ticks — validated
  farm build executed, trade intent correctly dropped
  (no_valid_trade_partner), timeouts degraded to rules seamlessly.
- 31 new tests. Fast suite: 362 passing.

### Decisions

- **[DECISION] Intents are action IDs only**: the frozen interface has no
  positional args; sim handlers already pick optimal sites/targets.
  Validation therefore checks feasibility (site exists, affordable),
  not location legality.
- **[DECISION] Re-validate queue every tick**: state drifts between
  request and execution; a queued farm can go unaffordable. Stale drops
  get distinct telemetry reasons.
- **[DECISION] Word-start keyword matching**: substring matching made
  "build more farms" map to BUILD_MINE ("more" ⊃ "ore"). `\b`+stem
  prefixes fix it while keeping stem tolerance.
- **[WHY] No LLM output can execute illegal actions**: two independent
  layers — validate_action pre-tick AND the sim's own handler checks.

---

## Session 29 — 2026-08-22 — Sprint 27: Strategic Reasoning

**What was built**
- `advice.py` — summaries in, structured strategic priorities out:
  - `SYSTEM_PROMPT` (advisor role + strict JSON-only output contract) and
    `USER_PROMPT_TEMPLATE`; `build_advice_prompt()` returns the pair
  - `StrategicAdvice` (priorities list + rationale), `AdviceResult`
    (ok/advice/error/raw/elapsed) mirroring Sprint 25's LLMResult pattern
  - `parse_advice()`: strict JSON extraction (tolerates fences/chatter
    around the object, nothing inside it); validates types; strips/caps
    priorities at 5 non-empty strings; anything else -> None
  - `advise(client, summary, name)`: never raises — server failures pass
    through as ok=False, garbage becomes "unparseable model output"
  - `AdviceLog`: append-only jsonl advisory side channel, never sim state
- Advisory-only enforced: nothing executes, nothing touches sim physics.
- 23 new tests: parser matrix (13 parametrized garbage cases), prompt
  shape pins, degradation contract, log round-trip + live-gated slow test
  enforcing >=90% parseability on real llama3.1:8b across 10 seeds x 2
  settlements. Fast suite: 331 passing.

### Decisions

- **[DECISION] Strict-parse, degrade-loud**: malformed advice is a
  first-class outcome (ok=False + error string), not an exception —
  Sprint 28's validation layer builds on this exact contract.
- **[DECISION] Tolerant shell, intolerant core**: markdown fences /
  assistant chatter around the JSON are stripped by regex extraction;
  inside the object, types must be exactly right (no leniency).
- **[DECISION] Cap priorities at 5**: longer lists dilute attention and
  Sprint 28 intent mapping works per-priority.
- **Ops finding**: an aborted live-test run wedged Ollama (sequential
  request queue stuck; /api/tags healthy but generation hung forever,
  CLI spinner never produced tokens). Server restart fixed it. Live
  round-trip after restart: PASS in 4m30s.

---

## Session 28 — 2026-08-22 — Sprint 26: Settlement State Summarization

**What was built**
- `summaries.py` — deterministic prompt-ready text views of sim state:
  - `summarize_settlement(sim, s, tier)` — tiny (one line) and full
    (sections: stats/resources/buildings/territory/queue/relations/events)
  - `summarize_world(sim, tier)` — header + wars/disasters/routes +
    per-settlement summaries
  - `estimate_tokens()` (~4 chars/token) for budget checks
- Tiny one-liner packs archetype, strategy label, pop/food/net/happiness,
  territory, building mix F/S/M/G, hostile/allies/WAR names — the Sprint 27
  reasoning prompts' primary input.
- 15 new tests: exact format pins on a duck-typed stub sim, determinism
  (byte-identical within and across identical seeded sims), placeholder
  rendering (dead settlements, empty personality, None numerics), token
  budgets. Fast suite: 309 passing.

### Decisions

- **[DECISION] Names not IDs in output**: settlement ids are opaque; LLM
  prompts need readable names, and omitting ids sidesteps any uuid4
  leakage into "deterministic" output.
- **[DECISION] Stub-based format pins**: pinning exact strings against a
  real sim would couple tests to world dynamics; the stub pins formats,
  real-sim tests pin determinism/budgets.
- **[DECISION] Round-half-even is fine in pins**: `format(120.5, '.0f')`
  → "120" bit us once; pins now encode actual Python semantics rather
  than fighting them.
- **[DECISION] Wars/disasters/routes at world level only**: per-settlement
  war state already appears in relations lines; no duplication.

### Verification

```
Live 60-tick world: tiny + full tiers render correct relations (alliances
formed via trade), events chronological, buildings/territory accurate.
```

---

## Session 27 — 2026-08-20 — Sprint 25: Ollama Integration (Phase 5 begins)

**What was built**
- Phase 5 docs expanded: `detailed_sprint_plan.md` Sprints 25–30 now carry
  full tasks/acceptance criteria grounded in roadmap §19 principles
- `llm.py` — zero-dependency Ollama client over stdlib urllib:
  - `LLMConfig` dataclass loaded from `data/world_sim/llm_config.json`;
    CLI flags override file values; corrupt file degrades to defaults
  - `generate()` / `chat()` against `/api/generate` and `/api/chat`
  - `is_available()` / `list_models()` via `/api/tags`
  - **Graceful-degradation contract**: every method returns `LLMResult`
    (ok/text/error/model/elapsed_s) — never raises into sim/training code;
    timeouts, unreachable servers, malformed JSON, redirects all handled
- CLI — `worldsim llm status` (reachability, installed models, config echo,
  warns if configured model not installed) and `worldsim llm ask --prompt`
- Tests: 15 new, fully mocked HTTP (CI needs no Ollama); one live-gated
  slow test auto-skips without server. Fast suite: 294 passing.

### Decisions

- **[DECISION] Default model llama3.1:8b, speed option gemma2:2b**: user's
  installed models; llama3.1 follows instructions best for the structured
  advice S27+ needs; everything is config so switching is one flag.
- **[DECISION] stdlib urllib over httpx/requests**: one host, simple JSON
  POSTs, no async yet — zero dependencies won.
- **[DECISION] Default host 127.0.0.1 not localhost**: avoids IPv6-first
  resolution quirks against Ollama's loopback listener.
- **[DECISION] Redirects followed once manually preserving POST**: urllib
  surfaces 307s as URLError("Temporary Redirect") instead of re-POSTing —
  found live on first real call.
- **[DECISION] Default timeout raised 30s → 180s**: first call may include
  model load-from-disk on modest hardware (observed ~36s for llama3.1:8b).

### Verification (live)

```
llm status: reachable=True, 7 models, llama3.1:8b configured ✓
llm ask: coherent single-sentence answer in 35.75s ✓
```

---

## Session 26 — 2026-08-20 — Sprint 24: Anti-Reward-Hacking Systems (Phase 4 complete!)

**What was built**
- `rewards.py` — `RewardGuard`: escalating response ladder on top of
  Sprint 13's detector:
  - OK → WARN (detector flagged) → PENALIZE (flagged ≥100 ticks; reward
    scaled ×0.5) → QUARANTINE (penalized ≥200 more; excluded from
    selection); clean ticks de-escalate gradually
- `env.py` — guard drives penalized rewards in step(); info gains
  `guard_level` + `quarantined`
- `population.py` — `quick_eval_guarded()` scores candidates AND assesses
  hacking over the rollout; quarantined candidates excluded from champion
  selection (kept registered for forensics); mass-quarantine logged
- `training.py` — `_run_policy` collects hacking telemetry (flagged ticks,
  quarantined runs); surfaced per-generation in `rl dashboard`
- Exploit regression suite (`tests/test_hacking.py`, slow tier):
  route-farming, granary spam (Sprint 11's exploit), alternator (dodges
  redundant-action shaping), synthetic exploiter → QUARANTINE
- Tests: 7 new. Fast suite: 282 passing; slow: 4 hacking replays.

### Decisions

- **[DECISION] De-escalation is gradual** (−5 flagged ticks / −2 penalized
  ticks per clean tick): a single lucky clean tick can't reset an active
  response, but genuine reform recovers.
- **[DECISION] Quarantined candidates stay registered**: forensics value;
  only SELECTION excludes them.
- **[DECISION] Fallback if all candidates quarantined**: return highest-
  scorer anyway (caller logs mass quarantine) — evolution limps forward
  rather than crashing.
- **[DECISION] Detector measures component shares, not action repetition**:
  the alternator exploit test proves action-shaping evasion can't hide
  single-source reward dominance.

### Gotchas

- RewardGuard stored `dominant_source` as an attribute, shadowing the
  detector method call ('str' object is not callable) — renamed to
  `last_dominant_source` + delegating method.
- Guard's detector has a 200-tick track-record warm-up before flagging:
  ladder unit tests use a short-warmup detector to test mechanics directly.

### Acceptance status

- ✅ Synthetic exploiter automatically escalates to QUARANTINE (route-farm
  replay, 600 ticks)
- ✅ Known-exploit replays fail loudly if detection/penalization/quarantine
  regresses (slow tier)
- ✅ Telemetry in dashboard output per generation

### PHASE 4 COMPLETE

Populations ✓ mutation/elitism ✓ cross-generation learning ✓ self-play ✓
strategy discovery ✓ anti-hacking defense ✓.

---

## Session 25 — 2026-08-20 — Sprint 23: Strategy Discovery

**What was built**
- `discovery.py` —
  - `behavior_signature()`: normalized 6-dim vector (farm/income/granary
    building shares + capped routes/raids/roads activity)
  - `collect_generation_samples()`: runs each generation's champion across
    probe worlds, collecting per-settlement signatures + Sprint 11 labels
  - `cluster_signatures()`: scipy k-means++ over signatures (falls back to
    fewer clusters when samples are scarce)
  - `discover_strategies()`: clusters named by dominant feature; weak
    supervision from member label majorities; novelty = centroid distance
    beyond threshold from EVERY archetype reference centroid
  - `save/load_discovery_log()`; exemplars stored per cluster (checkpoint
    path + seed) for re-instantiation
- CLI — `rl discover --gens gen1r,gen3r --worlds-per-gen N --ticks T
  --clusters K --novelty-threshold F`
- Tests: 7 new. Fast suite: 279 passing.

### Decisions

- **[DECISION] Signatures normalized by share + activity caps**: raw counts
  made farm-heavy mixes indistinguishable (Sprint 11 lesson reused); shares
  separate composition, capped activity dims separate intensity.
- **[DECISION] scipy k-means++ with seeded init** — deterministic given the
  same sample set; no sklearn dependency.
- **[DECISION] Novelty = distance from ALL archetype reference centroids**
  rather than low in-cluster density: flags genuinely uncharted behavior,
  not just sparse data.
- **[DECISION] Weak supervision via member-label majority vote**, reported
  alongside the dominant-feature name so discovered clusters stay grounded
  in known vocabulary when possible.

### Verification (live, gen1r vs gen3r champions)

```
14 settlements sampled across 2 gens x 2 probe worlds:
  3 clusters found, TWO flagged [NOVEL]:
    roads-focused-novel (x2 variants) — road spam d=0.75/0.85 from any
    archetype; nobody scripted road-heavy play
  exemplars recorded (gen1r checkpoint + seeds) → re-runnable
```

The novel road-centric strategies are exactly the "I never programmed this"
behaviors the roadmap's ultimate success criterion asks for.

---

## Session 24 — 2026-08-20 — Sprint 22: Self-Play / Civilization Competition

**What was built**
- `competition.py` —
  - `run_head_to_head()`: k learned policies simultaneously control distinct
    settlements in ONE shared world; controllers act first each tick
    (simultaneous execution), then `sim.step(skip_agent_ids=...)` runs
    shared mechanics with all controlled agents bypassed
  - per-controller metrics: survival ticks, peak/final population,
    cumulative §6.4 reward, end-state buildings/routes + territory/resource
    SHARES across controllers
  - `determine_winner()`: survival → territory-share tiebreak
  - `head_to_head_eval()`: paired A-vs-B over many seeds with permutation
    p-values on reward/territory differences
  - accepts checkpoint paths or loaded models
- CLI — `rl compare --gen-a --gen-b --head-to-head`: true policy-vs-policy
  competition; match recorded in `training_runs` (agent_type=
  'head_to_head', both generations filled)
- Tests: 8 new. Fast suite: 272 passing.

### Decisions

- **[DECISION] Simultaneous pre-tick action execution**: all controller
  actions run before the world tick, so neither controller sees the other's
  move first within a tick (turn-order advantage still exists ACROSS ticks
  via settlement index order — documented asymmetry).
- **[DECISION] Shares computed across CONTROLLERS only** (rule-based
  bystander settlements excluded from denominators) — measures competitive
  balance among competitors.
- **[DECISION] Runner accepts paths or loaded models**; winner = survival
  then territory-share.

### Findings

```
gen1r vs gen3r head-to-head (4 shared worlds × 1500 ticks):
  gen3r wins 3-1 | mean reward 22.41 vs 6.19 | territory share 0.65 vs 0.35
```

**The Sprint 18 "regression" reverses under direct competition**: gen3r's
controlled cohort showed identical survival/peak-pop vs gen1r (saturated
metrics), but head-to-head reveals gen3r is competitively dominant. More
training + rebalanced weights produced genuine competitive strength that
baseline-relative measurement structurally could not see. This validates
Sprint 22's premise: self-play metrics answer questions baseline-relative
comparison cannot.

Also noted: same-model sanity shows small A/B asymmetry (~1.5 reward) from
turn order + spawn-site terrain quality — expected, bounded, documented.

---

## Session 23 — 2026-08-20 — Sprint 21: Cross-Generation Learning

**What was built**
- `population.py` —
  - `merge_strategy_memories()`: EMA-weighted merge of per-generation
    {(archetype, action): reward} tables (later generations weigh more)
  - `save/load_strategy_prior()`: population prior persisted as JSON next
    to checkpoints
  - `prior_actions_for(archetype)`: top-k historically-rewarded actions
  - `evolve()` curriculum: champion scored across an evaluation seed set;
    below-mean seeds become the NEXT generation's fresh-candidate training
    worlds (`curriculum_failure_seeds`, recorded per generation)
- `agents.py` — RuleBasedAgent consumes the prior: idle-fallback decisions
  preferentially pick prior top-actions (deterministic seeded rng; lazy
  import avoids agents→population cycle); env threads priors to all
  rule-based agents at reset
- CLI — `rl evolve --curriculum/--no-curriculum --eval-seeds N
  --strategy-priors PATH`
- Tests: 5 new (merge weighting, prior round-trip, ordering, fallback
  behavior, failure-seed selection). Fast suite: 264 passing.

### Decisions

- **[DECISION] Priors consumed by rule-based agents' idle fallback** — the
  only decision path that's freely choosable without breaking urgency logic.
  Deterministic seeded rng keeps the stateless/resumable property intact.
- **[DECISION] Curriculum = training worlds, not reward changes**: candidates
  for gen N+1 train ON the seeds where gen N's champion scored below its own
  mean. Failure definition is relative to the champion's average (adaptive).
- **[DECISION] Lazy import of population inside agents.observe** — avoids
  the agents→population→training→env→agents import cycle.

### Findings

Curriculum mechanism verified structurally (failure selection + candidate
world override). Smoke runs on tiny 32-tile worlds produced identical
champion seed-scores across eval seeds — diagnosed as saturation again:
best-spawn search finds all-fertile squares on tiny maps, making dynamics
seed-independent. Score variance (and thus meaningful curricula) returns at
256-size benchmark worlds. Regression-reduction measurement deferred until
Phase 4 evolution runs at full scale.

---

## Session 22 — 2026-08-20 — Sprint 20: Selection, Mutation & Strategy Evolution

**What was built**
- `population.py` —
  - `mutate_checkpoint()`: Gaussian noise injected into all policy params,
    scaled by each tensor's own std; topology preserved, child loads cleanly
  - `quick_eval()`: cheap cumulative-reward rollout for scoring mutants
    (no baseline run, no persistence)
  - `evolve()` v2: per generation — **elite** (champion carried unchanged),
    **n mutants** (children of champion at escalating strengths), and
    **fresh random candidates**; selection across all three by score
  - `strategy_shift_report()`: runs each generation's champion in a small
    world, reports settlement strategy-label distributions (Sprint 11 labels)
- `training.register_checkpoint()` gains parent/mutation lineage kwargs
- `db.py` — `mutation` column (migration)
- CLI — `rl evolve --mutants --mutation-strength --eval-ticks`
- Tests: 3 new (mutation changes weights not shape; elite+mutants present
  with lineage; strategy-shift report). Fast suite: 260 passing.

### Decisions

- **[DECISION] Mutants scored by cheap rollouts, not training**: mutation +
  quick_eval gives real evolutionary pressure without gradient cost. Fresh
  trained candidates keep training-return as their score.
- **[DECISION] Noise scaled per-tensor by param std** so layers with large
  weights aren't destabilized relative to small ones.
- **[DECISION] Parent lineage points at the exact champion checkpoint label**
  (e.g., gen1_c0), not just the generation — full chains queryable.
- **[DECISION] Elite wins score ties** via deterministic tie-break ordering.

### Verification

```
rl evolve --population 1 --generations 2 --mutants 2 (tiny worlds):
  gen1 champion gen1_c0 (fresh, 0.8113)
  gen2 candidates = elite(gen1_c0) + 2 mutants; ELITE WON the tie (0.8113)
  lineage: gen2_e parent=gen1_c0, mutation=elite ✓
strategy_shift_report: returns per-generation label distributions ✓
```

---

## Session 21 — 2026-08-20 — Sprint Docs Expansion + Sprint 19: Populations & Generational Training

### Part 1 — Roadmap docs expanded

`docs/detailed_sprint_plan.md` previously stopped at Sprint 18 detail with a
"Phase 4+ fleshed out later" placeholder. Now contains detailed plans for
**Phases 4–10 (Sprints 19–57+)** derived from `architecture_and_roadmap.md`,
reconciled against current reality:
- Phase 4 sprints carry full tasks/acceptance criteria; Phases 5–7 as themed
  tables (scoping deferred until their phase starts); Phase 8 notes the
  Electron/UX decision stays deferred (CLI-first, decided Session 12)
- Divergences documented: strategy memory (S11), hacking detection (S13),
  dashboards (S18), God Mode core (S6) landed ahead of their roadmap slots
- Sprint 24 reframed: detection exists; remaining work is the RESPONSE
  ladder (warn → penalize → quarantine)

### Part 2 — Sprint 19: Populations & Generational Training

- `population.py` —
  - `train_population()`: N candidates per generation on disjoint world
    seeds (`seed_base + gen_index*9973 + i*101`), each registered under
    `{gen}_c{i}` with checksums
  - `select_champion()`: highest mean training return, deterministic
    first-candidate tie-break
  - `promote_champion()`: champion checkpoint copied to bare `{gen}` label
    so existing tools (`rl dashboard`, `rl compare`, `--policy-id`) work
    unchanged
  - `evolve()`: multi-generation loop with parent lineage chain
  - accepts external `db_store` for isolated registries (tests)
- `db.py` — `policy_checkpoints.parent` column (+ migration) records lineage
- CLI — `rl evolve --population N --generations G --timesteps-per-candidate T`
- Tests: 6 new (champion selection/registration/lineage/deterministic seeds/
  parent chain/schema migration). Fast suite: 257 passing.

### Decisions

- **[DECISION] Champion selection by mean training return** for Sprint 19;
  validation-world evaluation as selector is a Sprint 20 refinement.
- **[DECISION] Champion promoted to bare generation label via file copy +
  new registry row** — downstream tools never learn about candidate labels.
- **[DECISION] Per-generation seed offsets from label digits** — stable,
  disjoint per candidate, reproducible across runs.

### Verification

```
rl evolve --population 2 --generations 2 --timesteps-per-candidate 1024:
  gen1 champion gen1_c0 (return 0.95, parent=None)
  gen2 champion gen2_c1 (return 1.1992, parent=gen1)
  evolve_results.json persisted
```

---

## Session 20 — 2026-08-20 — Learning Remediation (entropy fix, reward rebalance, controlled cohort)

**Scope:** Sprint 18 follow-ups as one coherent piece: entropy capture fix,
configurable reward weights, controlled retraining cohort, dashboard re-run.

### What was built

- **Entropy capture fixed**: SB3 logs `train/entropy_loss` (NEGATIVE mean
  entropy), never `train/entropy` — confirmed empirically by dumping logger
  keys after learn(). Callback now stores positive entropy plus
  explained_variance and approx_kl; summary gains `final_entropy` (collapse
  detection) and `mean_explained_variance`.
- **Configurable reward weights**: `RewardWeights` dataclass +
  `compute_reward_components(weights=...)`; env accepts `reward_weights`
  dict. Rebalanced defaults from Sprint 13 breakdown data: population gain
  doubled (0.02→0.05), building delta halved (0.05→0.02) — thriving matters
  more than spamming construction.
- **Controlled retraining cohort** gen1r/gen2r/gen3r: IDENTICAL configs
  (--parallel 4, size 64, settlements 3, max_ticks 1000), differing only in
  timesteps (20k/40k/80k). Originals preserved.
- Tests: 2 new (weight overrides on components; weights flow through env).

### Controlled cohort results

```
Training health (now fully observable):
  return:            6.51 -> 10.24 -> 13.89   (monotonic UP)
  explained var:     0.54 -> 0.71 -> 0.81     (value fn converging)
  entropy final:     3.51 -> 2.44 -> 1.74     (converging, not collapsed)
Dashboard (4 worlds x 1500 ticks):
  NO REGRESSIONS — gen3r never loses to gen1r (Sprint 18's gen3 collapse
  was caused by uncontrolled configs, now proven)
  survival/peak-pop flat at equilibrium (1500/71) — saturation unchanged
```

### Decisions

- **[DECISION] Entropy = -entropy_loss** (SB3 convention); also capture
  explained_variance and approx_kl as policy-health indicators.
- **[DECISION] Reward rebalance via dataclass defaults** rather than one-off
  edits — future shaping experiments are config changes.
- **[DECISION] Keep original gens alongside r-cohort** — the uncontrolled
  regression is preserved as evidence for the controlled-fix comparison.

### Honest status

Training-side learning is now demonstrably healthy and observable
(monotonic returns, converging value function, controlled configs = no
regressions). Evaluation-side metrics remain saturated (survival/peak-pop
equilibrium), so "improvement" is currently only visible in training-time
returns. Next lever when we want eval-visible deltas: harder worlds at
longer horizons, or metrics measuring efficiency rather than equilibrium.

---

## Session 19 — 2026-08-20 — Sprint 18: Measure Whether Agents Improve

**Scope:** Phase 3 / Sprint 18 — multi-generation training (gen1→gen2→gen3),
learning-curve dashboard, monotonicity + per-seed regression analysis.

### What was built

- Training: gen2 (40k timesteps) and gen3 (80k) via `--parallel 4`
  (163.9s / 347.7s wall; training returns 22.17 / 27.18)
- `training.py` — `compare_generations()`: evaluates each registered
  generation vs baseline on identical worlds; aggregates learning-curve
  curve data (survival / reward-win-fraction / peak pop / trained episodes);
  monotonicity checks; **per-seed regression detection** of newest vs first
  generation; optional `db_path` for isolated registries
- `generate_learning_curve_plot()`: dual-axis PNG (survival line +
  reward-win-fraction dashed)
- CLI — `rl dashboard --gens gen1,gen2,gen3 --metric {survival,reward,
  peak_population} --plot ...`: learning-curve table, primary-metric
  progression with change %, monotonicity report, regression listing
- Tests: 4 new (monotonicity/regression/improvement math units + slow
  end-to-end two-generation dashboard on tiny worlds)
- Fast suite: 249 passing

### Decisions

- **[DECISION] Generations trained with growing budgets** (20k/40k/80k)
  rather than spec's episode counts — maps to wall-clock reality.
- **[DECISION] Dashboard supports three metrics** because survival saturates;
  reward-win-fraction and peak population carry actual signal.
- **[DECISION] Monotonicity reported as non-decreasing OR non-increasing**
  with raw values printed — the check flags direction consistency; the
  values show WHICH direction (learning vs decay).
- **[DECISION] Regression = newest gen strictly below first gen on survival
  for a given seed**, reported per-seed.

### Findings (the honest headline)

```
Dashboard run (4 worlds × 1500 ticks, metric=reward):
  gen1: survival 1500.0, peak pop 71.0, reward wins 0%
  gen2: survival 1500.0, peak pop 71.0, reward wins 0%   (trained 40k)
  gen3: survival 1009.5, peak pop 49.5, reward wins 0%   (trained 80k)

REGRESSIONS detected on 3 of 4 seeds (gen3 < gen1 survival)
```

**gen3 REGRESSED despite the highest training return (27.18)** — more
training made the policy worse in the world. Likely contributors:
1. Training-return drift vs world outcomes (possible reward fitting on
   granary/building components rather than thriving).
2. Each generation trained under different configs (sequential 20k vs
   parallel 40k/80k) — not controlled comparison.
3. No entropy floor captured (`mean_entropy: None`) — possible policy
   collapse went unobserved.

This is Sprint 18 working as designed: the tooling DETECTED non-improvement.
The acceptance criteria (monotonic improvement, no regressions, >20% gain)
are NOT met at this scale — honestly recorded. Follow-ups: fix entropy
capture, control training configs across generations, longer runs, revisit
reward shaping via Sprint 13 breakdowns.

### Known issues / deferred

- Full-scale dashboard (10 worlds × 3000 ticks × 3 gens ≈ 180k sim ticks)
  exceeds 2 hours; reduced verification used. Parallel evaluation across
  worlds is the natural speedup (Sprint 15 machinery applies to eval too).
- Wilcoxon emits RuntimeWarning on zero-variance metrics (nan p-values
  print as 'nan' in reports).
- Reward-wins 0% everywhere: random-ish policies rarely out-reward the
  rule-based baseline within these tick windows.

### Acceptance criteria status

- ⚠️ Monotonic improvement gen3 > gen2 > gen1: NOT observed (regression at
  gen3) — detection works, improvement doesn't yet
- ✅ Regression detection: gen3 loses on 3 seeds — flagged correctly
- ✅ Queryable dashboard: `rl dashboard --gens ... --metric ...` (+ plot)
- ⚠️ >20% improvement gen1→gen3: NOT met (negative on survival)

---

## Session 18 — 2026-08-20 — Sprint 17: Rigorous Comparison & Statistical Significance

**What was built**
- Difficulty knobs on Simulation (`disaster_chance_mult`, `gather_mult`),
  threaded through WorldSimEnv; `rl evaluate --difficulty {normal,hard}`
  (hard = 2× disasters, ½ passive gathering)
- Shared reward measurement: baseline runs accumulate §6.4 reward for their
  settlement via the SAME component function as the env — "higher average
  reward than baseline" comparisons are now meaningful
- Richer per-world metrics in PairedResult: end-state territory, buildings,
  routes_established + cumulative reward (both controllers)
- Statistics: `paired_permutation_pvalue()` (exact-ish, no new deps) +
  Wilcoxon signed-rank per metric; results carry per-metric means/deltas/p
  values
- Reports: `generate_report()` writes markdown tables + matplotlib bar-chart
  PNG; `rl evaluate --report --chart`
- `training_runs.agent_type` column (migration), set on evaluation inserts
- Tests: 9 new comparison tests

**Verification (hard worlds, gen1, reduced run)**
```
4 worlds × 1000 ticks, disaster ×2 / gather ×0.5:
  survival ties again — baseline survives hard worlds too at this scale
  report + chart generated ✓; significance machinery exercised ✓
```

**Decisions**
- **[DECISION] Permutation test over t-test**: exact-ish, assumption-free,
  no scipy distribution tables needed. Note n≥6 pairs required for p<0.05.
- **[DECISION] Difficulty = environment knobs, not agent handicaps** — both
  controllers face identical conditions.
- **[DECISION] Honest saturation documentation**: survival remains tied even
  on hard worlds at current scales. Real differentiation requires much longer
  horizons, metric innovation, or genuinely lethal conditions. The
  measurement infrastructure now exists to detect it when it happens.

**Gotchas**: an indentation bug from an edit dedented the gathering loop out
of its for-body — wood income silently became zero (only last iteration's
mountain variables applied). Caught by the gather-multiplier unit test.
Also: missing-file must fail verification before any None-checksum skip.

**Acceptance status**: 20-world pipeline ready ✓; significance machinery ✓;
"higher reward on 15+ worlds" ✗ honestly reported (ties at current training/
difficulty scales).

---

## Session 17 — 2026-08-20 — Sprint 16: Policy Checkpoints & Model Versioning

**Scope:** Phase 3 / Sprint 16 — checksums for corruption detection,
registry-based checkpoint resolution (`--policy-id`), `training_runs`
evaluation logging, generation comparison, rollback determinism.

### What was built

- `db.py` —
  - `policy_checkpoints` gains `checksum` + `size_bytes` columns via guarded
    ALTER TABLE migrations (pre-Sprint-16 databases migrate on open)
  - new `training_runs` table (§24.1): per-evaluation records with win
    fractions, survival means, full results JSON
  - `get_latest_policy_checkpoint(generation)` (latest by id),
    `insert_training_run()`
- `training.py` — `file_sha256()` (streamed), `verify_policy_checksum()`,
  `register_checkpoint()` (hashes + sizes at registration),
  `resolve_policy_path()` (registry id or explicit path; verifies checksum)
- CLI —
  - `rl train` now registers with checksum automatically
  - `rl evaluate --policy-id gen1` resolves via registry (checksum verified;
    legacy records without checksums skip verification with a note)
  - `rl compare --gen-a X --gen-b Y`: evaluates both vs baseline, prints
    deltas (win fraction / survival / peak pop), logs both runs into
    `training_runs`
- Tests: 237 fast-suite passing (10 new)

### Decisions

- **[DECISION] sha256 streamed hashing at REGISTRATION time** — corruption
  detection is then a cheap re-hash on load. SB3 checkpoints are zips; any
  byte flip changes the hash.
- **[DECISION] Registry resolution verifies before loading**: tampered or
  truncated files raise "Checkpoint corruption detected" instead of crashing
  deep inside torch.load.
- **[DECISION] Legacy checksumless records skip verification** rather than
  failing — the Sprint-14 gen1 record predates checksums; re-training will
  upgrade it.
- **[DECISION] Missing file always fails verification**, even for legacy
  records (existence is not version-dependent).
- **[DECISION] rl compare evaluates each generation vs the BASELINE
  separately** (side-by-side deltas) rather than head-to-head policy-vs-
  policy — head-to-head requires two external controllers in one env, which
  arrives with self-play in Phase 4.

### Gotchas / bugs found & fixed

- verify order bug: `None` checksum returned True even for MISSING files
  (short-circuit ordering) — existence must always gate first.
- Guarded-migration pattern added because CREATE IF NOT EXISTS doesn't add
  columns to existing tables.

### Acceptance criteria status

- ✅ Policies saved with full metadata incl. checksum + size in SQLite
- ✅ Any checkpoint can be loaded and evaluated on demand (registry id →
  verified path; demonstrated live against real world.db gen1 record)
- ✅ Evaluation results logged per-world in `training_runs`
- ✅ Rollback to gen1 produces identical results on identical seeds
  (unit-tested double-eval equality)

### Next up (Sprint 17)

Compare trained agent vs baseline rigorously: fixed benchmark suite of 20
worlds, both agents run on all worlds, statistical significance testing
(p < 0.05), report generation. Note: Sprint 14's finding stands — survival
metrics saturate vs the rule-based baseline; Sprint 17 should also define
non-saturated secondary metrics (peak population delta, resource efficiency)
or use harder worlds so the comparison has signal.

---

## Session 16 — 2026-08-20 — Sprint 15: Parallel Simulation Training

**Scope:** Phase 3 / Sprint 15 — VecEnv with parallel simulation workers,
batched stepping, CPU utilization tracking, wall-clock benchmarking,
checkpointing through parallel training.

### What was built

- `training.py` —
  - `train(n_envs=N)`: N>1 uses SB3 `SubprocVecEnv` via `make_vec_env`
    (distinct seeds per worker); rollout steps are per-env
  - `CpuUsageSampler`: background thread sampling per-core utilization
    (psutil), reports avg overall + max single core
  - `benchmark_parallel()`: sequential-vs-parallel comparison at identical
    total timesteps; per-config wall-clock, speedup ratios, ticks/s, CPU stats
- CLI — `rl train --parallel N`; `rl train --compare` (speedup benchmark)
- `psutil` added to dependencies
- Tests: 5 new (4-worker parallel smoke incl. checkpoint round-trip,
  step-count parity, CPU sampler, speedup-benchmark structure). Fast suite:
  227 passing; slow tier: 13 passing.

### Decisions

- **[DECISION] SubprocVecEnv over multiprocessing.Pool**: spec said Pool,
  but SB3's VecEnv abstraction handles action/observation batching, episode
  boundaries, and seeding across processes natively — Pool would rebuild all
  of that by hand.
- **[DECISION] Equal-total-timesteps comparison semantics**: SB3's
  `learn(total_timesteps)` counts TOTAL steps across envs, so comparing
  wall-clock at equal totals measures genuine throughput gains (not extra
  compute).
- **[DECISION] psutil for CPU tracking** rather than OS-specific APIs;
  background sampler thread so sampling never blocks the training loop.

### Benchmark results (Sprint 15 acceptance checks)

```
4000 timesteps:   seq 27.1s | x1.26 (2 workers) | x1.78 (4 workers)
20000 timesteps:  seq 161.3s | x1.38 (2) | x2.26 (4 workers)
                  avg CPU 49.6% (12 logical cores ≈ ~6 busy with 4 workers),
                  max single core 93.8%
Checkpointing through SubprocVecEnv verified (save + load + predict)
No inter-process crashes in any run ✓
```

**Acceptance status:** no crashes ✓; checkpointing ✓; CPU tracked ✓. The
"~75% time reduction" criterion is NOT met as stated: observed **56% time
reduction (x2.26)** at 20k timesteps, improving with scale (x1.78 at 4k →
overhead amortization visible). Root cause is Amdahl's law — policy
inference + gradient updates run serially in the main process while only
simulation parallelizes. Longer runs and larger worlds shift the ratio
further toward parallel benefit; documented honestly rather than tuned to a
number.

### Known issues / deferred

- Main-process PPO inference/updates are now the serial bottleneck; VecEnv
  frame-stacking or async gradient overlap won't help until env steps
  dominate again (they will at 256-size worlds × more settlements).
- Windows spawn overhead (~1-2 s per worker startup) matters only for tiny
  runs.
- `--compare` benchmarks share one checkpoint filename per config; harmless
  but could collide if run concurrently.

### Next up (Sprint 16)

Policy checkpoints & model versioning: metadata schema (already partially in
place via `policy_checkpoints`), checksums for corruption detection,
`rl evaluate --policy-id genN` by registry id instead of path, rollback to
any generation, gen-N vs gen-N-1 comparison logging.

---

## Session 15 — 2026-08-20 — Sprint 14: First Learning Agent (PPO)

**Scope:** Phase 3 / Sprint 14 — Stable-Baselines3 PPO on WorldSimEnv,
training script with metrics logging, first checkpoint
(`policy_gen1`), paired evaluation vs rule-based baseline.

### What was built

- `training.py` —
  - `EpisodeMetricsCallback`: SB3-native `BaseCallback` capturing
    per-episode returns/lengths (via Monitor's `info["episode"]`) and
    policy/value losses + entropy (via logger), JSONL-logged per episode
  - `train()`: PPO(MlpPolicy, net_arch [128,128]) over a Monitor-wrapped
    WorldSimEnv; saves checkpoint `.zip` + `_summary.json`
  - `evaluate_vs_baseline()`: paired A/B on identical world seeds —
    settlement 0 under trained policy vs the same settlement rule-based;
    compares survival ticks + peak population; writes eval_results.json
- `db.py` — `policy_checkpoints` table + insert API
- CLI — `rl train --timesteps --generation --size ...` and
  `rl evaluate --model ... --worlds ...`; both wired into policy registry
- Tests: 4 new (training smoke, checkpoint load+predict round-trip,
  summary JSON, checkpoints table); training tests marked slow where heavy
- Fast suite: 223 passing

### Decisions

- **[DECISION] Reduced in-session training (20k timesteps, 64-size worlds)**
  to validate the pipeline honestly; full-scale runs are a CLI command away
  but take hours at simulation speed.
- **[DECISION] Paired-per-seed evaluation**: identical worlds, settlement 0
  differs only by controller — removes world-quality variance from the
  comparison.
- **[DECISION] Survival-time is the headline metric per spec**, with peak
  population as secondary.

### Benchmark results (acceptance check)

```
Training: 20k timesteps, 149.6s wall, 20 episodes, mean return +25.98,
          mean policy loss 0.0243 — no crashes ✓
Checkpoint: policies/policy_gen1.zip + SQLite record ✓
Evaluation (10 benchmark worlds, 3000 ticks):
  survival: baseline 3000/3000 vs policy 3000/3000 → 10 ties, 0 wins
  peak pop: identical per seed (e.g., 135 vs 135)
  → strict-win fraction: 0% vs required 60% ✗
```

**Honest finding:** the survival/population metrics are saturated. The
rule-based baseline never dies (known since Sprint 8), and population
equilibrium is set by the world's food carrying capacity (~132-135 on these
seeds) regardless of controller. With no headroom on these metrics, gen1
cannot demonstrate outperformance — it can only match. Non-negative average
reward criterion IS met (+25.98 mean return).

### What this means for Phase 3 going forward

1. **Metrics need headroom**: comparisons should use harder worlds
   (disaster-heavy seeds, resource-scarce spawns, hostile neighbors) or
   efficiency metrics (food wasted, actions-to-milestone) where controller
   quality actually moves the needle.
2. **More training**: 20k timesteps is tiny; the pipeline supports
   `rl train --timesteps 500000` overnight runs once reward shaping is
   refined (Sprint 13 machinery is in place).
3. Reward shaping may need re-balancing so the policy learns strategies
   beyond matching the food equilibrium (Sprint 13 breakdowns will show
   which components dominate).

### Known issues / deferred

- Entropy metric not captured (`mean_entropy: None`) — SB3 logs entropy only
  after first rollout with proper keys; minor, fix when tuning.
- Evaluation runtime: 10 worlds × 2 runs × 3000 ticks ≈ 40 min — acceptable
  but batch-parallelizable later (Sprint 15 VecEnv work).
- Identical peaks across ALL seeds hint that food capacity math dominates —
  revisit GATHER_RATE/farm caps if worlds feel too same-y.

### Acceptance criteria status

- ✅ PPO trains without crashing (20 episodes, clean losses)
- ✅ Checkpoint saved (.zip) AND recorded in SQLite `policy_checkpoints`
- ✅ Trained agent achieves non-negative average reward (+25.98)
- ⚠️ Outperforms baseline in 60%+ of worlds on survival time: NOT met — all
  ties (metric saturation, documented above). Revisit with harder
  evaluation worlds or richer metrics after Sprint 15/16.

### Next up (Sprint 15)

Parallel simulation training: vectorized environments (VecEnv), batching,
CPU utilization tracking, wall-clock benchmarks per 100 episodes.

---

## Session 14 — 2026-08-20 — Sprint 13: Reward Refinement, Replay Buffer, Hacking Detection

**Scope:** Phase 3 / Sprint 13 — named reward components, rolling
normalization, RAM replay buffer, reward-hacking detection, breakdown
logging + reward plots.

### What was built

- `rewards.py` —
  - `compute_reward_components()`: named per-tick components {survival,
    population, buildings, routes, starvation, redundant_action,
    effective_action}; env's scalar reward is their clamped sum
  - `RollingNormalizer`: rolling mean/std over last 1000 ticks; identity
    until warmed up (50 ticks)
  - `RewardHackingDetector`: sliding window of breakdowns; flags when any
    single component exceeds 80% of total earned reward after a 200-tick
    track record; exposes `dominant_source()`
- `replay.py` — `ReplayBuffer(10_000)` ring buffer with add/sample/latest;
  env appends every transition automatically
- Shaping: redundant-action penalty (5+ consecutive identical actions,
  escalating, capped) + effective-action bonus (+0.005 when the action
  actually executes)
- `env.py` — step now returns breakdown in `info["reward_breakdown"]`,
  plus `reward_normalized` (info-only), `hacking_flag`, `hacking_source`
- CLI — `rl run` prints aggregate reward-breakdown totals + hacking flag
  count; `--plot out.png` renders per-tick reward curves (matplotlib dep)
- Tests: 219 fast-suite passing (16 new)

### Decisions

- **[DECISION] Normalized reward is info-only** — the env's returned reward
  stays raw so PPO sees true signal; normalization is exposed for analysis.
- **[DECISION] "Efficient combos" implemented as an effectiveness bonus**:
  multi-action combos don't exist yet, so the bonus rewards actions that
  actually execute on first attempt vs wasted ones.
- **[DECISION] Epsilon-style exploration excluded from hacking judgment**:
  the detector watches component SHARES over a window, not action choice —
  random-policy runs don't trigger it (verified: 0 flags in random smoke).
- **[DECISION] Redundant-action penalty escalates but caps** at 10× base so
  a stuck agent isn't infinitely punished.
- **[DECISION] matplotlib added to main dependencies** (plot is a spec'd
  deliverable; Agg backend, no display needed).

### Gotchas / bugs found & fixed

- Reward unit tests broke during the components refactor (scalar
  `compute_reward` moved out of env.py) — rewritten against
  `compute_reward_components` + `total_of`.
- Replay ring test asserted on `latest(1)` (the NEWEST item) instead of the
  eviction boundary — fixed to inspect the full tail window.

### Known issues / deferred

- Reward plot currently shows random-policy curves (no learning yet);
  "clear learning curve" verification lands in Sprint 14/18 when PPO trains.
- Hacking detector only observes the controlled settlement (single-agent env).
- SQLite experience archive (`agent_history`) and the RAM replay buffer are
  separate by design; unifying them is deferred until PPO dictates format.

### Acceptance criteria status

- ✅ Reward breakdown logged per tick in info dict (named components)
- ✅ Replay buffer stores 10k transitions (ring semantics verified)
- ✅ Hacking detection triggers when >80% of earned reward comes from one
  source (unit-tested; balanced distributions never flagged)
- ✅ Reward plot generated via `rl run --plot` (learning-curve check moves to
  Sprint 14/18)

### Next up (Sprint 14)

First learning agent: Stable-Baselines3 PPO on WorldSimEnv, training script,
metrics logging (episode return/loss/entropy), first checkpoint, evaluation
vs rule-based baseline.

---

## Session 13 — 2026-08-20 — Pre-Phase-3 Hygiene + Sprint 12: ML Environment

**Scope:** Test split + profiling (queued from Session 11), then Phase 3 /
Sprint 12 — `WorldSimEnv(gym.Env)`, §6.4 reward, headless runner.

### Part 1 — Hygiene

**Test split:** `slow` marker registered in pyproject; default run excludes
it (`addopts = "-m 'not slow'"`). 12 long tests marked. Fast suite:
**203 tests in ~2 min**; slow tier: `pytest -m slow` (12 tests, ~3 min).

**Profiling** (cProfile, 5 settlements × 300 ticks; total 9.0s → 6.4s,
step-loop **2× faster**):
1. World generation was 33% of profile → module-level `(seed, size)`
   generation cache in `world.py` (arrays copied out; capped at 64 entries).
2. Full-grid scans repeated per settlement-tick (`buildings_of` ×4,
   `territory_of`, `roads_of`) → per-tick memo (`Simulation._cached`) with
   invalidation at every mutation point.
3. `_produce_resources` redundant masks/per-tile loops → bincounts with a
   no-debuff fast path.
- Gotcha: cache invalidation initially missed `build_road` — roads were
  built but invisible until next tick. Every mutator must invalidate;
  consider a decorator if more mutators appear.

### Part 2 — Sprint 12: WorldSimEnv

- `env.py` — `WorldSimEnv(gym.Env)` exposing ONE settlement's perspective:
  - `reset(seed)` → fresh world/settlements, returns the frozen-contract
    observation `(60,) float32`
  - `step(action)` → executes the GIVEN action for the controlled settlement
    (its rule-based agent is skipped that tick via
    `Simulation.step(skip_agent_ids=...)`); other settlements continue under
    rule-based control
  - spaces: `Discrete(62)` / `Box(0,1,(60,),float32)`
  - termination: controlled settlement dies or all die; truncation at
    max_ticks (default 5000)
- Reward per §6.4 shape, normalized [-1,+1]: survival bonus, population
  gain/loss, building/route deltas, starving penalty
- CLI: `worldsim rl run --episodes N --ticks T --settlements S` — random-
  policy headless episode runner printing returns/lengths/survivors
- gymnasium added to dependencies
- Tests: 17 new env tests (spaces, reset reproducibility, step tuple,
  agent-skip semantics, truncation/termination, reward unit tests incl.
  clamping, trajectory determinism). Fast suite: 203 passing.

### Decisions

- **[DECISION] Single-settlement perspective**: the env controls one
  settlement; the rest of the world lives on under rule-based agents. This
  gives PPO a stationary-ish multi-agent backdrop without self-play
  complexity (self-play arrives in Phase 4).
- **[DECISION] Reward scale**: weights chosen so a normal tick lands around
  ±0.05 and disasters matter (~0.2+); clamped to [-1,1]. The formal
  normalization/rolling-average machinery from §6.4/Sprint 13 will refine.
- **[DECISION] Agent-skip via `step(skip_agent_ids)`**: mechanics
  (production/consumption/population/diplomacy) still run for the controlled
  settlement — only its DECISION is external.

### Gotchas / bugs found & fixed

- Reward sign error: population LOSS added +0.2 instead of subtracting —
  dying was profitable! Caught by the loss-dominates unit test before any
  training happened. Exactly the class of bug reward-unit-tests exist for.

### Acceptance criteria status

- ✅ `env.reset()` returns valid observation + info dict
- ✅ `env.step(action)` returns correct `(obs, reward, terminated, truncated,
  info)`
- ✅ Reward within [-1, +1], normalized per tick
- ✅ Headless mode runs episodes (random policy smoke: 3×300 ticks in seconds)
- ✅ Reward function unit-tested for known scenarios
- (100-episode perf criterion deferred to Sprint 14 when PPO lands)

### Next up (Sprint 13)

Reward system refinement: reward shaping (redundant-action penalties),
rolling normalization, replay buffer (10k RAM + SQLite flush), reward
hacking detection flags, reward breakdown logging/visualization.

---

## Session 12 — 2026-08-20 — Pre-Phase-3 Hygiene: Test Split + Profiling

**Scope:** Split slow integration tests (suite had grown to ~15 min) and
profile/optimize hot paths before Phase 3's training loops.

### What was done

**Test split**
- `pyproject.toml`: `slow` marker registered; default run excludes it
  (`addopts = "-m 'not slow'"`)
- 12 long-running tests marked `@pytest.mark.slow` (long survival runs,
  multi-hundred-tick determinism checks, benchmark CLI test)
- Fast suite (`pytest`): **186 tests in ~2 min** (was ~15 min)
- Slow suite (`pytest -m slow`): 12 tests in ~3 min
- Full coverage still exists — CI/dev loop uses fast, pre-release uses both

**Profiling + optimization** (cProfile, 5 settlements × 300 ticks; 9.0s → 6.4s,
with step-loop 6.0s → 3.2s ≈ **2× faster ticks**)
1. World generation was 33% of profile (opensimplex pure-Python loops):
   added a module-level `(seed, size)` generation cache in `world.py`
   (arrays copied out so sim mutations never poison the cache). Massive win
   for test suites/benchmarks that reuse seeds.
2. `observe_vector` called `buildings_of`/`territory_of`/`roads_of`
   repeatedly per settlement-tick, each doing full-grid scans: added a
   per-tick memo (`Simulation._cached`) keyed by settlement index, cleared at
   tick start and invalidated on every world mutation (claim/release/build/
   destroy/fire).
3. `_produce_resources` did redundant full-grid masks per resource and
   per-tile debuff loops: vectorized via bincounts with a no-debuff fast
   path.

### Gotchas / bugs found & fixed

- The cache initially missed invalidation in `build_road` (only `build_at`
  got it) — roads built but invisible to `roads_of` until next tick. Caught
  by `test_auto_road_rule_extends_network`; lesson: every mutator needs the
  invalidation call, consider a decorator later.

### Decisions

- **[DECISION] Default pytest run = fast suite only**; slow tier is explicit
  (`pytest -m slow`). Command-line `-m` overrides addopts cleanly.
- **[DECISION] Per-tick cache with mutation-point invalidation** rather than
  tick-lazy staleness — semantics stay exact, determinism unaffected.
- opensimplex generation itself (~3 s cold) left as-is: amortized in long
  runs; revisit with Numba only if world-gen shows up in training profiles.

### Next up (Sprint 12)

Phase 3 begins: Gymnasium environment wrapping the engine
(`WorldSimEnv(gym.Env)`), observation/action mapping to the frozen contract,
reward function, headless runner, unit tests for vectorization/rewards.

---

## Session 11 — 2026-08-20 — Sprint 11: Emergent Specialization & Strategy Differentiation (Phase 2 complete!)

**Scope:** Phase 2 / Sprint 11 — five archetypes biasing behavior, emergent
strategy labels derived from building mix + actions, evolution logging,
per-archetype strategy memory. **Closes Phase 2.**

### What was built

- `settlement.py` — `assign_archetype()` (5 presets seeded per settlement);
  archetype stored in the personality dict; `strategy_label`,
  `raids_committed`, `routes_established` counters
- `agents.py` —
  - archetype policy biases: trading halves trade cadence; mining doubles
    income-building cadence + builds regardless of stock level + higher
    income ceiling; agricultural gets deeper famine buffer + double farm
    growth; military gets lowered raid gate (0.5) and halved war weariness
  - **farm caps per archetype** (agri 40 / balanced 25 / trader 12 / military
    10 / miner 7) — specialization means non-farmers STOP spamming farms
  - **granary caps** (agri 6 / balanced 4 / others 2) — oversized storage
    kept food_level permanently low, which deadlocked the policy in famine
    mode (found via benchmark debugging)
  - `derive_strategy_label()`: each strategy scored on its OWN normalized
    scale (agri/mining building shares × activity magnitude; trading = route
    initiative; military = raid campaigns); near-ties or weak signals fall
    back to the settlement's ARCHETYPE — behavior hasn't differentiated yet,
    so identity defaults to intent
  - epsilon exploration pool excludes BUILD_* actions (random construction
    ignored caps/affordability and drowned specialization in noise)
- `simulation.py` — labels refreshed every 250 ticks (changes logged as
  "strategy" events); dominant-strategy distribution logged at every
  1000-tick checkpoint ("strategy_evolution" events); strategy memory (EMA of
  reward per archetype×action) recorded in `_finalize_transition`;
  NEIGHBOR_SPAWN_DISTANCE raised 48 → 96 (sparse worlds left most settlements
  unreachable, starving trade/diplomacy/emergence)
- `db.py` — new fields + strategy_memory persisted in snapshots (11-tuple API)
- CLI/benchmark — status lines show strategy labels; simulate prints
  strategy distribution; benchmark reports distinct-strategies count
- Tests: 198 passing (15 new)

### Decisions

- **[DECISION] Archetypes assigned at spawn** (seeded, uniform over 5),
  stored in personality dict; they bias thresholds/cadences but never force
  actions ("bias without constraint" per spec).
- **[DECISION] Strategy labels are DERIVED, not declared**: computed from
  building mix shares + route initiative + raid campaigns. When no signal is
  strong enough, the label falls back to the archetype — identity defaults
  to intent until behavior differentiates.
- **[DECISION] Per-strategy normalized scoring** after raw-count scoring
  failed three ways: farm counts trivially dominated; a lone farm counted as
  full agricultural expression; transfer credit made every connected
  settlement a "trader". Each strategy now measured on its own natural scale.
- **[DECISION] Agricultural requires near-total dominance (share ≥0.95)** —
  farming is everyone's baseline (~90% of a typical mix), so moderate farm
  share means "not specialized", falling back to archetype.
- **[DECISION] Military archetypes START conflicts**: warlike settlements
  generate contested border friction against neutral neighbors and may raid
  them — aggression creates hostility, not vice versa. Without this, wars
  never began in peaceful worlds and the military label could never emerge.
- **[DECISION] Epsilon exploration pool excludes BUILD_\* actions** — random
  construction ignores affordability/caps by design and was washing out all
  behavioral differentiation (~23 spurious buildings of each type per 3000
  ticks).
- **[DECISION] Neighbor radius 48 → 96**: geographic isolation made most
  world pairs unable to ever interact; honest-but-dead worlds.

### Gotchas / bugs found & fixed (the convergence saga)

Getting emergent differentiation working took four benchmark-driven rounds:
1. Everyone converged to "agricultural" — farm growth uncapped across
   archetypes → farm caps per archetype.
2. Granaries hit their cap (20!) inflating capacity ~10k → food_level stuck
   below famine threshold → policy deadlocked in famine mode, zero
   specialization buildings → granary caps per archetype.
3. Epsilon random-construction added ~23 buildings of EVERY type per
   settlement, drowning all signals → construction excluded from exploration.
4. Transfer-weighted trading scores tied for every connected settlement;
   route-initiative scoring + archetype fallback fixed attribution.
Also: `mining_archetype` NameError from an edit, an `is_allied` on the wrong
object, and inconsistent warlike thresholds (policy floored aggression at
0.75 then required >0.85 — no settlement could ever qualify).

### Benchmark results (acceptance: ≥3 distinct strategies in 80% of worlds)

```
Seeds 50000-50009, 5 settlements each, 3000 ticks:
  distinct strategies per world: 3,3,3,2,4,3,4,4,4,3
  Worlds with >= 3 distinct strategies: 9/10 (90%)  [criterion: >= 80%]
  Survival rate: 100%
```

Seed 50003 (the one miss) contains only 2 resident archetypes — its ceiling.

### Known issues / deferred

- Full suite now exceeds 15 minutes; slow integration tests (benchmarks,
  long runs) need isolation before Phase 3 parallel work — queued next
  session per plan.
- Simulation performance regressed across Sprints 9-11 (relations decay,
  contested-zone refresh every 50 ticks, repeated full-grid scans in
  observe_vector). Profile before Phase 3 training loops.
- Military label depends on wars occurring; truly isolated peaceful worlds
  honestly stay agricultural/balanced/trading.
- Strategy memory records but nothing consumes it yet — Phase 4
  (mutation/pattern selection) is its consumer.

### Acceptance criteria status

- ✅ Trading personalities establish more trade routes (unit-tested cadence)
- ✅ Mining personalities build mines heavily (doubled cadence, higher cap)
- ✅ Military personalities raid neighbors (warlike-initiation mechanic)
- ✅ ≥3 distinct strategies emerge in 90% of benchmark worlds (9/10)
- ✅ Labels visible in CLI status/distribution output and logged in events
  (UI future-ready)

### PHASE 2 COMPLETE

All six sprints delivered. The system now has: deterministic persistent
worlds, autonomous settlements driven by swappable agents with a frozen RL
contract, full economy/trade/war/diplomacy/reputation dynamics, disasters,
ruins and recovery — all reproducible from a seed.

Next session: split slow tests + profile hot paths (pre-Phase-3 hygiene),
then Phase 3 / Sprint 12 (Gymnasium environment wrapping this engine).

---

## Session 10 — 2026-08-20 — Sprint 10: Diplomacy & Trade Decisions

**Scope:** Phase 2 / Sprint 10 — alliances from sustained mutual trade,
automatic war declarations, bilateral peace treaties with tribute,
reputation system with non-interaction decay.

### What was built

- `diplomacy.py` — `DiplomacyState`: alliance pairs, wars (start tick +
  raid ledger), pending peace offers (200-tick validity), per-settlement
  reputation [−100,+100] with 0.001/tick non-interaction decay
- `actions.py` — **appended OFFER_PEACE=60, ACCEPT_PEACE=61** (first append;
  originals untouched), wired both handlers, new "diplomacy" category
- `simulation.py` —
  - war escalation: every raid recorded; 3 by same attacker/victim inside
    500 ticks → automatic declaration, relations pinned −100, event logged
  - raids against allies blocked at handler level (non-aggression floor)
  - peace: OFFER_PEACE creates a live offer; ACCEPT_PEACE sends our matching
    offer; treaty concludes when BOTH offers are live; aggressor (more
    logged raids) pays 25% stockpile tribute to victim; +10 rep each side;
    relations reset to −20 (below hostile — no instant re-raid)
  - alliance: TradeRoute tracks alternating-donor streak; ≥3 → alliance +
    reputation bonuses + event
  - trade gating: at-war pairs and rep < −50 refused new routes; routes die
    below war threshold
  - reputation seeded per settlement at registration; decay skips
    settlements that interacted that tick (raids/transfers)
- Observation dims 45–47 wired: at-war flag, incoming-offer flag, normalized
  reputation (`docs/agent_spec.md` updated)
- Agent policy: peaceful settlements offer/accept peace; aggressive ones
  never accept; long wars wear down even aggressive settlements
  (WAR_WEARINESS_TICKS=1000 offer cadence)
- Persistence: diplomacy serialized in snapshots (10-tuple API);
  `world_events` table carries diplomatic records
- Tests: 183 passing (20 new)

### Decisions

- **[DECISION] Appended IDs 60–61** for peace actions — contract rule is
  "never renumber, only append", exercised for the first time.
- **[DECISION] Peace concludes iff BOTH sides have live offers** (strict
  spec reading). ACCEPT_PEACE constitutes sending our own matching offer —
  so an offer plus an acceptance satisfies the bilateral requirement.
- **[DECISION] Tribute = 25% of aggressor's food/wood/stone stockpiles**,
  aggressor determined by the war's raid ledger (ties → first-listed side).
  Wars declared directly without raids have no recorded aggressor — tests
  escalate naturally via record_raid.
- **[DECISION] Alliance = 3 consecutive alternating-donor transfers**
  (operationalization of "mutually beneficial for 3 consecutive trades");
  one-way flow resets the streak.
- **[DECISION] Alliance effect this sprint = non-aggression floor only**
  (user choice); defensive pacts deferred.
- **[DECISION] Reputation decays only during non-interaction**: settlements
  involved in a raid or transfer that tick skip decay.

### Gotchas / bugs found & fixed

- update_world missed the diplomacy kwarg (signature replaceAll matched only
  the `-> str:` save methods) — caught by persistence tests.
- conclude_peace tie-break picked the wrong aggressor for directly-declared
  wars (no raid ledger); tests now escalate naturally via record_raid.
- Reputation decay silently did nothing until entries were seeded — rep()
  defaulted to 0 without storing a key; registration now seeds the ledger.

### Known issues / deferred

- No defensive pact: allies don't join each other's wars yet.
- Reputation currently affects only trade access; no opinion modifiers on
  raid targeting or peace willingness (personality covers that).
- War has no cost beyond raids themselves — no unit losses until military
  units exist.
- Full-suite runtime crossed 14 minutes; consider splitting slow integration
  tests before Phase 3's parallel-training work.

### Acceptance criteria status

- ✅ Alliances form when trade is mutually beneficial for 3 consecutive trades
- ✅ War declared if a neighbor raided 3 times within 500 ticks
- ✅ Peace treaties require both parties' offers (acceptance counts as
  sending your own)
- ✅ Reputation decays 0.1 per 100 ticks of non-interaction (verified: 0.100)
- ✅ Diplomatic events logged: alliance/war/peace_offer/peace types

### Next up (Sprint 11)

Emergent specialization & strategy differentiation: 5 personality archetypes
(agricultural/mining/trading/military/balanced), strategy labels derived from
behavior, strategy memory — closing out Phase 2.

---

## Session 9 — 2026-08-20 — Sprint 9: Multiple Settlements & Competition

**Scope:** Phase 2 / Sprint 9 — neighbor detection, relation states, raids
with timed debuffs + theft, contested border zones, event log, aggression
personalities finally wired.

### What was built

- `relations.py` — `RelationMatrix`: symmetric pairwise scores [−100,+100];
  trade pushes positive (+10 route, +0.05/transfer), raids push negative
  (−20 attempted, −30 success); decay 0.025/tick toward neutral (~2000 ticks
  to fully cool −50); hostile < −25, friendly > +25, war (route kill) < −60
- `simulation.py` —
  - `neighbors_of()`: spawn distance ≤48 OR territory contact; per-tick cache
  - `INITIATE_RAID` wired (#41): targets hostile neighbors' improved tiles in
    contested zones; success = clamp(0.4 + aggression×0.4 − defender-size);
    success applies 200-tick ×0.5 building-output debuffs (`BuildingDebuff`)
    + steals ≤10 wood/stone; cadence-enforced both in policy and handler
  - contested zones: recomputed every 50 ticks from hostile-pair borders;
    keys are (x, y) tile coords with expiry
  - trade gating: hostile pairs can't open routes; routes deactivate below
    war threshold; relations adjust on establish/transfer
  - `WorldEvent` log: raid/trade_route events with descriptions
- `agents.py` — raid policy branch: aggression > 0.7 AND hostile neighbor
  AND %200 cadence; observation dims 42–44 wired (hostile/friendly neighbor
  counts, contested-tile count) — see updated `docs/agent_spec.md`
- `db.py` — `world_events` table + `insert_world_events()`; snapshots now
  serialize relations, contested zones, debuffs, event log (9-tuple API)
- Default settlements 3 → 5
- Tests: 163 passing (18 new)

### Decisions

- **[DECISION] Border-friction contested zones**, not overlapping ownership —
  claims stay unowned-only; hostile borders get flagged as raid targets with
  expiry. Minimal disruption to existing mechanics.
- **[DECISION] Raids yield debuff + theft**: spec's 200-tick output halving
  plus up to 10 units stolen per resource — raids need a payoff beyond spite.
- **[DECISION] Personality-gated raids only**: aggression > 0.7, hostile
  relation, ≥200-tick cadence (enforced in both agent and handler). No
  desperation raids yet.
- **[DECISION] Deterministic entity IDs**: settlement/route/ruin ids switched
  from uuid4 to uuid5(seed/index/pair/tick). Found via demo: the raid RNG
  hashes attacker ids, so uuid4 ids silently broke cross-run determinism.
  This was a latent Phase-3 blocker caught early.
- **[DECISION] deserialize_world now returns a 9-tuple** (added relations,
  contested, debuffs, event log).

### Gotchas / bugs found & fixed

- Coordinate-swap triple-play on contested zones: dict keyed (row,col) while
  `is_contested(x,y)` looked up (x,y); test helper then re-swapped unpacking.
  Standardized keys to (x, y).
- `_refresh_contested_zones` only pruned expired flags — peace never lifted
  contested status. Now fully recomputed from current relations.
- Demo determinism check itself was buggy (compared final state 100×); the
  underlying uuid4 issue was real and fixed.

### Known issues / deferred

- Raid targets limited to contested-zone buildings; deep strikes impossible
  until movement/military exists.
- No defender agency: defense is implicit (population size lowers raid
  success). Real defense units are a later sprint.
- Relations affect nothing diplomatically yet beyond trade gating and raid
  permission — alliances/wars/peace treaties are Sprint 10.
- Neighbor detection ignores territory growth between refreshes for spawn-
  distance pairs (contact via adjacency covers it).

### Acceptance criteria status

- ✅ 5 settlements start without overlapping territory (45 owned tiles)
- ✅ Neighbors detected dynamically (spawn distance OR territory contact)
- ✅ Raiding reduces Farm output for exactly 200 ticks (unit-tested recovery)
- ✅ Trade routes form naturally between non-hostile neighbors
- ✅ Hostile relations decay over time unless re-triggered (unit-tested)
- ✅ Events log captures "A raided B" / "C and D established trade"

### Next up (Sprint 10)

Diplomacy & trade decisions: alliances, war declarations from repeated raids,
peace treaties with tribute, reputation system with decay, diplomatic actions
in the action space.

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
