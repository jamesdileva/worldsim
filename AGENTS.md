# AGENTS.md — WorldSim Working Log & Changelog

This file is the project's working history: what was built, when, and **why**
key decisions were made. It is updated after every sprint's commit+push.

- Detailed per-session notes (gotchas, debugging sagas, benchmarks):
  see `notes.md`
- Architecture: `docs/architecture_detailed.md`, `docs/architecture_notes.md`
- RL contract (frozen observation/action spaces): `docs/agent_spec.md`
- Sprint plan / roadmap: `docs/detailed_sprint_plan.md`,
  `docs/architecture_and_roadmap.md`

## Project Snapshot

Autonomous AI civilization simulator ("AI ant farm"). Deterministic seeded
worlds; settlements run under swappable agents (rule-based today, RL from
Phase 3); economies, trade, war, diplomacy, reputation, disasters, ruins,
emergent strategies — all reproducible from a single seed.

**Stack:** Python + NumPy + opensimplex + SQLite + Gymnasium +
Stable-Baselines3 (PPO) + matplotlib + psutil + scipy + Ollama
(stdlib-urllib client, Phase 5).

**Status:** Phase 1 ✅, Phase 2 ✅, Phase 3 ✅ (Sprints 12–18 + remediation;
learning healthy in training metrics, eval metrics saturated), Phase 5 ✅
COMPLETE (S25–30: Ollama client, summarization, strategic reasoning,
intent→validated-action agent, scheduled background reasoning, and a LIVE
paired comparison showing LLM advice significantly improves
territory/buildings/reward; sprint docs expanded through Phase 10),
**Phase 6 ✅ COMPLETE** (S31 technology & eras, S32 market economies,
S33 highways/infrastructure, S34 treaties & federations, S35 warfare,
S36 collapse/recovery depth, S37 long-horizon stability: 100k ticks ≈
11 min at 151 tps with bounded memory; sprint docs expanded through
Phase 10), **Phase 7 ✅ COMPLETE** (S38 god controls polish, S39 disaster toolkit,
S40 resource manipulation depth, S41 terrain manipulation, S42 nuclear
events, S43 timeline branching/undo with byte-exact restore + branch
timelines; sprint docs expanded through Phase 10), **Phase 8 in
progress** (S44 advanced visualization done; S45 civilization
histories done; S46 event timeline done).

**Test tiers:** `pytest` = fast suite (~250 tests, ~3–5 min);
`pytest -m slow` = long integration runs.

---

## Changelog

| Commit | Sprint | Summary |
|---|---|---|
| `197c3e1` | — | Idea docs |
| `d2e51f8` | 1 | Seeded world generation, terrain, SQLite persistence, generate CLI |
| `bc56f5e` | 2 | Settlements, growth/starvation, territory claiming, simulate CLI |
| `c5c0e25` | 3 | Buildings/roads/infrastructure, food caps, build queue |
| `a7a482a` | 4 | Multi-settlement economy, trade routes, scarcity, collapse |
| `fb2de36` | 5 | Disasters, happiness/stability, ruins & re-settlement |
| `ab019c0` | 6 | Simulation clock, save/load/step, auto-save, God Mode (**Milestone 1**) |
| `9c3273f` | 7 | Agent abstraction, 60-action space, rule-based agent, experience logging |
| `3a12b66` | 8 | Personality vectors, urgency policy, benchmark worlds (100% survival) |
| `0cd09d6` | 9 | Neighbors, relations, raids, contested zones, event log, deterministic IDs |
| `24c48a0` | 10 | Alliances, war declarations, peace treaties, reputation |
| `27c2f6a` | 11 | Archetypes, emergent strategy labels, strategy memory (**Phase 2 complete**) |
| `5a79e6b` | — | Perf: gen cache + tick memoization (2× faster); slow-test split |
| `f7e6c99` | 12 | WorldSimEnv Gymnasium env, §6.4 reward, headless runner (**Phase 3 begins**) |
| `0beb361` | 13 | Reward components, rolling normalization, replay buffer, hacking detection, plots |
| `0792256` | 14 | PPO training pipeline, metrics callback, checkpoints, paired eval |
| `8617711` | 15 | SubprocVecEnv parallel training (×2.26 @ 4 workers), CPU tracking |
| `1dbe0b4` | 16 | Policy checksums, registry resolution (`--policy-id`), training_runs, rl compare |
| `923cb40` | 17 | Difficulty knobs, shared reward measurement, Wilcoxon/permutation significance, evaluation reports |
| `ab780ec` | 18 | Multi-generation dashboard, learning curves, per-seed regression detection |
| `44e34d1` | 18b | Learning remediation: entropy capture fix, configurable reward weights, controlled retraining (no regressions) |
| `0a9f070` | 19 | Population manager, champion selection/promotion, lineage; sprint docs expanded through Phase 10 (**Phase 4 begins**) |
| `aa0bd9d` | 20 | Weight mutation, elitism, mutant scoring via rollouts, lineage types, strategy-shift report |
| `ed3b232` | 21 | Strategy-memory aggregation into population priors, failure-weighted curricula |
| `fafb008` | 22 | Multi-controller self-play, head-to-head competition, competitive shares/metrics |
| `bca94ed` | 23 | Behavioral signatures, k-means strategy discovery, novelty detection, discovery log with exemplars |
| `847624a` | 24 | RewardGuard ladder (WARN/PENALIZE/QUARANTINE), selection quarantine, exploit regression suite (**Phase 4 complete**) |
| `43297e8` | 25 | Ollama integration: zero-dep urllib client, graceful degradation, config precedence, llm status/ask CLI (**Phase 5 begins**) |
| `045ac39` | 26 | Settlement/world state summarization: deterministic token-budgeted tiny/full prompt views |
| `86cec92` | 27 | Strategic reasoning: JSON advice prompts + strict parser, never-raise advise(), advisory log |
| `0e30601` | 28 | Intent mapping onto frozen action space, pre-tick validation layer, LLMDrivenAgent with rule fallback |
| `d740c70` | 29 | Reasoning scheduler (interval/event/struggling), single-flight background advisor, non-blocking sim loop |
| `8784814` | 30 | Paired LLM-vs-rulebased comparison, Wilcoxon+permutation, llm compare CLI; live verdict: advice helps (**Phase 5 complete**) |
| `085e5e0` | 31 | Technology & eras: deterministic research, four-tech tree, era gates on Mine/Granary, Era III bonuses (**Phase 6 begins**) |
| `a35048f` | 32 | Market economies: derived prices, valuation-gap trade direction, gap-scaled shipments |
| `c8b6586` | 33 | Large-scale infrastructure: inter-settlement highway projects, pay-as-you-go segments, +30% trade bonus |
| `aff5026` | 34 | Advanced diplomacy: treaties with clauses, derived federations, +15% intra-federation shipments |
| `db6372a` | 35 | Warfare: armies/forts/sieges, wired military action slots, deterministic battles, victor-imposed tribute treaties |
| `77acc2e` | 36 | Collapse/recovery depth: enriched ruins, refugee migration, knowledge recovery on re-settlement, building decay |
| `3a0af35` | 37 | Long-horizon stability: bounded logs, epoch history, memoized food math, soak tests (**Phase 6 complete**) |
| `08a7a01` | 38 | God controls polish: spawn/freeze/bless_happiness, divine audit trail, --force guard (**Phase 7 begins**) |
| `d15dc27` | 39 | Disaster toolkit: authored disasters via shared application path, zone preview, CLI wiring |
| `df9fcc4` | 40 | Resource manipulation depth: region targeting, mass bless/strip/smite, persistent land blessings |
| `a375795` | 41 | Terrain manipulation: god_terraform single/region, cache invalidation, improvement-compatibility policy |
| `e50d5e0` | 42 | Nuclear events: blast annihilation, contamination zones (20-year yield/happiness debuffs), double-confirmation CLI |
| `30406f3` | 43 | Timeline branching/undo: undo_points snapshots, worldsim undo + --as-world branches, byte-exact restore (**Phase 7 complete**) |
| `a3bca56` | 44 | Advanced visualization: deterministic ASCII maps + overlays, settlement panels, byte-reproducible PNG exports (**Phase 8 begins**) |
| `a67b88b` | 45 | Civilization histories: event-log chronicles, civilizations summary, population curves + chart export |
| `163daef` | 46 | Event timeline: categorized chronological view, filters, stacked category histogram chart |

---

## Session Log & Key Decisions (why)

### Sessions 1–6 — Phases 1 (Sprints 1–6)
Deterministic persistent worlds with rule-based settlements.
- **[WHY] opensimplex over `noise`**: C extension fails on Windows/modern
  Python; pure-Python wins portability.
- **[WHY] NumPy arrays per tile property** instead of tile objects: keeps
  vectorization/Numba possible later.
- **[WHY] Food yields rescaled (fertile 4/plains 2)**: original values made
  growth mathematically impossible vs consumption — scarcity reachable but
  viability achievable.
- **[WHY] Shared §6.4-style reward measurement** for baseline runs too:
  enables fair policy-vs-baseline comparisons.
- Milestone 1 reached end of Sprint 6: living ant farm, save/load, God Mode.

### Sessions 7–11 — Phase 2 (Sprints 7–11)
Agents replace auto-rules; the frozen RL contract is born
(`docs/agent_spec.md`).
- **[WHY] Full 60-action space defined up front with no-op stubs**: action
  spaces can never be renumbered without invalidating trained policies.
- **[WHY] Stateless rule-based agents** (decisions keyed by seed+tick):
  saved/resumed sims continue identically with zero agent serialization —
  the same property RL checkpointing needs.
- **[WHY] Deterministic uuid5 entity IDs** (Sprint 9): raid RNG hashes
  attacker ids; uuid4 silently broke cross-run determinism.
- **[WHY] Emergent labels derived from behavior with archetype fallback**
  (Sprint 11): raw-count scoring made everyone "agricultural"; per-strategy
  normalized scales + intent fallback gives honest labels AND diversity.
- **Emergence lesson:** specialization requires caps (farm/granary ceilings
  per archetype), exploration that doesn't spam construction, and
  military archetypes that *start* conflicts — peaceful worlds otherwise
  converge to a single farm-heavy strategy.

### Session 12 — Pre-Phase-3 Hygiene
- Fast/slow test split (`pytest -m slow`): default suite ~2 min.
- Profiled: world-gen cache, per-tick scan memoization with mutation-point
  invalidation, vectorized production → ~2× faster ticks.
- **[WHY] Cache invalidation at every mutation point** (build/destroy/
  claim/release/fire/road): stale-scan bugs are silent; tests caught the
  first miss within minutes.

### Sessions 13–17 — Phase 3 so far (Sprints 12–16)
- **[WHY] Single-settlement Gymnasium perspective** over a living multi-agent
  world: stationary-ish backdrop for PPO without self-play complexity.
- **[WHY] Reward components as named dict + clamped total**: breakdowns feed
  hacking detection (>80% single-source flag) and analysis plots.
- **[WHY] Replay buffer separate from SQLite archive**: RAM buffer serves
  training batches; SQLite stays the durable archive.
- **[WHY] SubprocVecEnv over multiprocessing.Pool** (Sprint 15): batching/
  episode/seeding handled natively. Measured ×2.26 speedup @4 workers at
  20k timesteps; serial PPO updates cap scaling (Amdahl).
- **[WHY] Checksums verified at registry resolution** (Sprint 16): corrupted
  checkpoints fail loudly before torch.load crashes obscurely. Legacy
  checksumless records skip gracefully; missing files always fail.
- **Honest finding (Sprint 14/17):** survival/population metrics saturate
  against the rule-based baseline (it never dies; population equilibrium is
  environmental). Gen1 matches baseline but strict outperformance requires
  harder conditions/metrics/longer training. Measurement infrastructure
  (shared rewards, permutation tests, hard mode) now exists to detect real
  deltas.

### Session 18 — Sprint 17 (this session)
- Difficulty knobs (disaster ×2 / gather ×0.5 hard mode), shared reward
  measurement for baseline runs, richer paired metrics (territory/buildings/
  routes/reward), Wilcoxon + permutation significance testing, markdown/PNG
  reports via `rl evaluate --report --chart`.
- Verified end-to-end on hard worlds; survival still ties honestly.

### Session 19 — Sprint 18 (this session)
- gen2 (40k) + gen3 (80k) trained via parallel pipeline; multi-generation
  dashboard (`rl dashboard --gens --metric --plot`) with monotonicity and
  per-seed regression detection.
- **Honest finding (Sprint 18):** gen3 REGRESSED vs gen1 (survival 1009 vs
  1500 ticks; peak pop 49 vs 71; regressions on 3 of 4 seeds) despite the
  highest training return — more training ≠ better policy under current
  reward shaping and uncontrolled cross-generation configs. Detection
  tooling works exactly as intended; improvement does not yet exist.
- **[WHY] Dashboard supports three metrics** (survival/reward/peak-pop):
  survival saturates, so learning curves need metrics with headroom.
- Follow-ups: entropy capture fix (currently None), controlled training
  configs across generations, longer runs, reward-shaping rebalance guided
  by Sprint 13 component breakdowns.

### Session 20 — Learning Remediation (this session)
- Entropy capture fixed: SB3 logs `train/entropy_loss` (negative entropy),
  never `train/entropy`. Also captures explained_variance + approx_kl as
  policy-health indicators; summary gains final_entropy for collapse
  detection.
- `RewardWeights` dataclass: §6.4 shaping is now config, not code. Rebalanced
  defaults from breakdown data (population gain ×2.5, building delta ÷2.5).
- Controlled retraining cohort gen1r/gen2r/gen3r (identical configs, only
  timesteps differ): **regression resolved** — no gen3r-vs-gen1r losses.
- Training health now demonstrably good: returns 6.5→10.2→13.9 monotonic,
  explained variance 0.54→0.81, entropy converging without collapse.
- **[WHY] Controlled configs first**: the Sprint 18 regression was caused by
  uncontrolled cross-generation setups, not by RL being impossible — proven
  by the controlled cohort's clean results before any deeper changes.
- Remaining gap: eval metrics (survival/peak-pop) still saturate at food-
  carrying equilibrium; eval-visible improvement needs harder worlds or
  efficiency-style metrics at longer horizons.

### Session 21 — Sprint 19 (this session)
- `detailed_sprint_plan.md` expanded through Phase 10 from the roadmap,
  reconciled with reality: strategy memory/hacking-detection/dashboards/
  God Mode core landed early; Phase 4 sprints carry full detail, Phases 5–7
  as themed tables pending scoping.
- **[WHY] Docs reconciled rather than copied**: several roadmap items shipped
  ahead of their slots — the plan must reflect what's actually missing or
  sprints would rebuild existing systems.
- `population.py`: N candidates per generation on disjoint seeds, champion
  by mean training return (deterministic tie-break), promoted to the bare
  generation label so all existing tools work unchanged; lineage via new
  `parent` column.
- **[WHY] Selection on training return for now**: validation-world selection
  deferred to Sprint 20; training return is free (no extra rollouts).
- Verified end-to-end: gen1→gen2 champion chain with parent lineage.

### Session 22 — Sprint 20 (this session)
- `mutate_checkpoint()`: Gaussian noise per-tensor scaled by param std —
  large-weight layers aren't destabilized; children load with identical
  topology.
- Evolution v2: each generation = **elite** (champion unchanged) + **n
  Gaussian mutants** (scored by cheap rollouts, no gradient cost) + fresh
  trained candidates; selection across all three.
- **[WHY] Mutants scored by rollout not training**: evolutionary pressure
  without gradient expense; fresh candidates keep training-return scoring.
- **[WHY] Elite wins score ties**: elitism must be able to protect the
  champion against equal-scoring challengers.
- Parent lineage now points at exact champion checkpoint labels (full chains
  queryable); `mutation` column records elite/fresh/gaussian:<strength>.
- `strategy_shift_report()`: per-generation settlement label distributions —
  first view of how behavior mix evolves under evolutionary pressure.
- Verified live: gen2's elite won its tie vs a mutant (0.8113).

### Session 23 — Sprint 21 (this session)
- `merge_strategy_memories()`: EMA-weighted merge of per-generation
  action-reward tables into a population prior (later gens weigh more);
  persisted as `strategy_priors.json` next to checkpoints.
- RuleBasedAgent consumes the prior on idle-fallback decisions (prefers
  historically-rewarded actions); deterministic seeded rng keeps the
  stateless/resumable property; lazy import breaks the agents→population
  import cycle.
- **Curriculum**: champion scored across an eval seed-set each generation;
  below-mean seeds become the NEXT generation's fresh-candidate training
  worlds. Mechanism verified structurally.
- **Finding**: tiny 32-tile smoke worlds produce seed-independent dynamics
  (best-spawn search finds all-fertile squares) — score variance and thus
  meaningful curricula need full-size worlds. Regression-reduction
  measurement deferred to full-scale evolution runs.
- **[WHY] Curriculum = training worlds, not reward changes**: candidates
  that failed before train ON those worlds — targeted practice, no reward
  distortion.

### Session 24 — Sprint 22 (this session)
- `competition.py`: multi-controller runner — k policies each drive one
  settlement in a shared world (simultaneous pre-tick actions; shared
  mechanics via skip_agent_ids); per-controller survival/peak/reward +
  territory/resource SHARES across controllers.
- `rl compare --head-to-head`: true policy-vs-policy matches recorded in
  training_runs with both generations filled.
- **Key finding**: the Sprint 18 gen3r "regression" REVERSES under direct
  competition — gen3r beats gen1r 3-1 head-to-head (reward 22.4 vs 6.2,
  territory 65%/35%). Baseline-relative metrics were structurally blind to
  this: saturation hides competitive dominance. Self-play measurement was
  the missing instrument.
- **[WHY] Simultaneous pre-tick execution**: no controller sees another's
  move within a tick; cross-tick turn-order asymmetry documented and bounded
  (~1.5 reward for same-model sanity runs).
- **[WHY] Shares across controllers only**: bystander rule-based settlements
  excluded from denominators so shares measure competitive balance.

### Session 25 — Sprint 23 (this session)
- `discovery.py`: 6-dim normalized behavioral signatures (building shares +
  capped route/raid/road activity); scipy k-means++ clustering across
  generation champions' probe-world rollouts; novelty = centroid distance
  beyond threshold from EVERY archetype reference centroid; discovery log
  persists named strategies with exemplars (checkpoint + seed).
- **Live finding**: two [NOVEL] road-centric strategies discovered in gen1r/
  gen3r rollouts — road-heavy play nobody scripted, exactly the roadmap's
  "I never programmed this" success criterion.
- **[WHY] Signatures use shares + activity caps**: raw counts made farm-
  heavy mixes indistinguishable (Sprint 11 lesson); shares separate
  composition, capped dims separate intensity.
- **[WHY] Novelty = distance from all archetypes, not cluster sparsity**:
  flags genuinely uncharted behavior rather than just small clusters.
- Weak supervision: member label majority votes reported alongside dominant-
  feature names so discovered clusters stay grounded in known vocabulary.
- Dashboard/graphics status confirmed: CLI analytics + matplotlib PNGs only;
  interactive UIs deliberately deferred to Phase 8 (Sprints 44–50).

### Session 26 — Sprint 24 (this session)
- `RewardGuard`: escalation ladder on top of Sprint 13 detection — OK →
  WARN → PENALIZE (reward ×0.5 after 100 flagged ticks) → QUARANTINE
  (200 more); clean ticks de-escalate gradually.
- Env applies penalized rewards; info gains `guard_level`/`quarantined`.
- `quick_eval_guarded()` scores candidates + assesses hacking; quarantined
  candidates excluded from champion selection (kept for forensics);
  fallback returns highest-scorer if ALL are quarantined.
- Exploit regression suite (slow tier): route farming, granary spam,
  alternator, synthetic exploiter → quarantine. Alternator test proves
  action-shaping evasion can't hide component-share dominance.
- **[WHY] Gradual de-escalation**: one lucky clean tick can't reset an
  active response; genuine reform recovers.
- **[WHY] Quarantine excludes from selection only**: registered checkpoints
  keep forensic value; evolution limps forward if all candidates are
  quarantined rather than crashing.

### Session 27 — Sprint 25 (this session)
- Phase 5 docs expanded: Sprints 25–30 carry full tasks/acceptance criteria
  grounded in roadmap §19 principles (LLM = advisory, never physics; never
  secretly mutates state; graceful degradation everywhere).
- `llm.py`: zero-dependency stdlib-urllib Ollama client; every method
  returns `LLMResult` and never raises into sim/training code; timeouts,
  unreachable servers, malformed JSON, and POST redirects all handled.
- CLI: `llm status` (reachability/models/config echo, warns if configured
  model missing) + `llm ask` for manual probing. Tests fully mocked; live
  test auto-skips without a server.
- **[WHY] Default host 127.0.0.1 not localhost**: avoids IPv6-first
  resolution quirks against Ollama's loopback listener (live-found: urllib
  surfaced an unhandled 307 "Temporary Redirect" via localhost).
- **[WHY] Redirects followed once manually preserving method+payload**:
  urllib will not re-POST on 307/308 by itself.
- **[WHY] Default timeout raised 30s→180s**: first call may load the model
  from disk on modest hardware (observed ~36s for llama3.1:8b).
- Config precedence: CLI flags > llm_config.json > defaults; corrupt config
  degrades to defaults.

### Session 28 — Sprint 26 (this session)
- `summaries.py`: deterministic settlement/world text views for LLM
  prompts — tiny (one line per settlement) and full (sections) tiers;
  `summarize_world` adds header + wars/disasters/routes + per-settlement
  lines; `estimate_tokens` (~4 chars/token) for budget checks.
- Tiny one-liner packs archetype/strategy/pop/food/net/happiness/
  territory/building mix + hostile/allies/WAR names — Sprint 27 prompts'
  primary input.
- 15 new tests: exact format pins on a duck-typed stub sim, byte-identical
  determinism within and across identical seeded sims, placeholder
  rendering (dead settlements, empty personality, None numerics), budgets.
  Fast suite: 309 passing.
- **[WHY] Names not IDs in output**: ids are opaque to an LLM and uuid4
  leakage would break the "pure function of state" contract; readable
  names only.
- **[WHY] Stub-based format pins**: pins against a live sim would couple
  tests to world dynamics — stubs pin formats, real sims pin determinism.

### Session 29 — Sprint 27 (this session)
- `advice.py`: advisor system prompt + user template (strict JSON-only
  output contract); `parse_advice()` tolerates fences/chatter around the
  JSON but validates types strictly inside it; `advise(client, summary)`
  never raises — server failure passes through, garbage becomes
  "unparseable model output"; `AdviceLog` jsonl side channel.
- Advisory-only enforced: nothing executes or touches sim physics.
- 23 new tests incl. live-gated slow test enforcing >=90% parseability on
  real llama3.1:8b across 10 seeds x 2 settlements — passed in 4m30s after
  server restart. Fast suite: 331 passing.
- **[WHY] Strict-parse, degrade-loud**: malformed advice is a first-class
  outcome (ok=False + error), not an exception — Sprint 28's intent→action
  validation layer builds on this exact contract.
- **[WHY] Tolerant shell, intolerant core**: markdown fences/assistant
  chatter around JSON are stripped; inside the object types must be exact.
- **Ops finding**: an aborted live-test run wedged Ollama's sequential
  request queue (/api/tags healthy, generation hung forever, CLI spinner
  produced no tokens). Restart fixed it; worth remembering when live tests
  are interrupted mid-run.

### Session 30 — Sprint 28 (this session)
- `intents.py`: phrase→action mapping onto the frozen 62-action space via
  word-start regex (stems work; "ore" can't fire inside "more" — live-
  found bug fixed with \b boundaries); `validate_action()` reuses the
  sim's exact predicates read-only so accepted intents can't fail
  "illegally"; telemetry counts mapped/unmapped/dropped with reasons.
- `llm_agent.py`: `LLMDrivenAgent` — injected client, no inference in
  decide() (S29 owns scheduling), queued intents REVALIDATED every tick
  against current state ("stale_" drops), provider failure/garbage/
  exception → pure RuleBasedAgent behavior. `attach_llm_agent()` keeps
  index alignment in sim.agents.
- Live smoke: llama3.1:8b drove a settlement 60 ticks — farm intent
  executed, trade intent dropped (no_valid_trade_partner), timeouts
  degraded to rules seamlessly. Fast suite: 362 passing.
- **[DECISION] Intents are action IDs only**: frozen interface has no
  positional args; handlers already pick optimal sites/targets, so
  validation checks feasibility, not location legality.
- **[DECISION] Re-validate queue every tick**: state drifts between
  request and execution — a queued farm can go unaffordable.
- **[WHY] Two independent legality layers**: validate_action pre-tick
  AND the sim handler's own checks — no LLM output can break world rules.

### Session 31 — Sprint 29 (this session)
- `reasoning.py`: `ReasoningConfig` with three combinable trigger modes —
  interval (every N ticks), event-triggered (raid/war/disaster/collapse/
  peace since last advice), struggling-only gate; `struggle_score()`
  (starvation dominates; dead=inf) + worst-first `prioritize()`;
  `BackgroundAdvisor` daemon thread enforcing AT MOST ONE in-flight LLM
  call per world, submit non-blocking, poll-based consumption.
- LLMDrivenAgent gained `advisor=`/`config=`: scheduler-gated background
  requests replace the S28 sync path when set.
- Live: 50 ticks in 0.1s wall while real llama3.1:8b calls ran in
  background; manual-cycle run consumed a completed advice and executed
  validated BUILD_GRANARY. Fast suite: 381 passing.
- **[DECISION] One in-flight call per world**: protecting local inference
  throughput; global slot keeps queueing predictable and lets prioritize()
  starve nobody.
- **[DECISION] Poll not push**: results keyed by settlement id, consumed
  at the agent's own observe() — no callbacks mutate sim state mid-tick.
- **[DECISION] last_reasoned_tick updates on CONSUMED results only**:
  re-submitting while in flight is wasted latency; failures retry after
  the interval naturally.
- Bug found by tests: tick-0 events invisible to event mode (floor=0);
  fixed with floor=-1 sentinel.

### Session 32 — Sprint 30 (this session)
- `comparison.py`: `_run_llm_arm()` mirrors `_run_baseline` tick-for-tick
  (same raw §6.4 reward math) with settlement 0 LLMDriven;
  `compare_llm_vs_baseline()` reuses paired-seed methodology and Sprint
  17's report format; Wilcoxon + permutation p-values; `verdict_text()`
  refuses to overstate (inconclusive when advice never landed).
- CLI: `llm compare` — guards against unreachable Ollama, writes
  report/chart, records verdict in training_runs.
- **LIVE VERDICT (10 paired worlds, hard):** advice significantly
  improves territory (+333 avg, p=0.0039), buildings (+10.2, p=0.002),
  cumulative reward (p=0.002); survival/peak-pop tie at saturation.
  182 validated LLM actions, 1 request failure. training_runs id=4.
- **[DECISION] Deterministic-degradation equivalence**: with all advice
  failing, fallback is byte-identical to the replaced agent — pinned by
  test so degradation can never fake a win.
- **[DECISION] Doc-vs-reality reconciliation**: plan said "ML-only vs
  ML+LLM"; actual comparison is rule-based vs rules+LLM — isolates
  exactly the "does advice help?" variable.
- **Ops findings**: Ollama wedge root-caused to the user's sentinel
  backend saturating the local model (not our code); PowerShell `>`
  writes UTF-16 (use cmd /c for git show redirects); always name-check
  new test files — I clobbered Sprint 17's test_comparison.py and lost
  9 tests until collection counts flagged it (recovered as
  test_sprint17_eval.py).

### Session 33 — Sprint 31 (this session)
- `tech.py`: four technologies in fixed order (agriculture → masonry →
  engineering → administration), era derivation (II needs agri+masonry,
  III needs all four), Mine/Granary gated behind Era II, Era III grants
  +15% farm output and +25% trade transfer size.
- Settlement gains research_points/technologies; era is DERIVED. Sim
  accumulates research per tick (0.05 × pop × era multiplier) and logs
  technology/era events (event-mode LLM advice reacts automatically).
- Gates at both legality layers: build_at refuses below-era builds;
  intent validator drops with missing_technology_* reasons.
- Live: both seed-42 settlements hit Era 2 by tick 600, byte-identical
  timelines. Fast suite: 401 passing.
- **[DECISION] No new actions, no obs changes**: frozen RL contract
  sacred; eras gate EXISTING mechanics rather than wiring reserved
  no-op action IDs (that would shift dynamics for trained policies).
- **[DECISION] Era derived from techs, not stored**: fewer serialized
  fields; techs vs era can never desynchronize.
- **Bug fixed (latent since S28)**: territory_of yields (y, x) from
  np.argwhere but the S28 intent validators unpacked as (x, y) — both
  were examining transposed tiles. Fixed with explicit ty/tx naming.
- Test note: find_building_site returns (y, x); callers must use
  build_at(x=site[1], y=site[0]).

### Session 34 — Sprint 32 (this session)
- `markets.py`: derived world market prices per resource
  (base × reference/(reference + 4×mean per-capita availability),
  clamped [0.25, 8.0]; metal base 2×). Pure function of state — nothing
  persisted, cannot desynchronize (mirrors derived-era philosophy).
- `_trade_tick` rewrite: direction by largest valuation gap across both
  route ends; shipment size linear in gap up to 4 units; Era III donors
  +25% on top of the cap; clamped to donor stock; dust (<0.5) skipped.
- Summaries: full world tier shows "Market prices per unit" so LLM
  advisors can reason about the economy.
- Live smoke: prices slid to floor as stockpiles grew (metal stayed
  priciest); 1176 gap-scaled transfers over 400 ticks. Fast suite:
  416 passing (3 Sprint 4 trade tests re-pinned to new invariants).
- **[DECISION] Prices derived, never stored**: like eras — fewer fields,
  no desync possible.
- **[DECISION] Deficits clamp to zero in valuation math**: collapse can
  drive inventories negative; min+1 denominator would hit exactly −1 →
  ZeroDivisionError (found live via competition fixtures).
- **[DECISION] Era III bonus applies after the unit cap**: cap limits
  normal logistics; administration tech = superior commercial
  organization that legitimately exceeds it.
- **[DECISION] Direction by valuation gap, not raw surplus**: raw
  differences ignore need; gaps route goods toward desperation.

### Session 35 — Sprint 33 (this session)
- `infrastructure.py`: inter-settlement highways — deterministic uuid5
  ids, L-shaped spawn-to-spawn paths skipping water/existing roads,
  Era II masonry gate, territory-adjacency requirement, one per pair;
  pay-as-you-go construction (stone/segment/tick), PAUSE-not-cancel
  when unfunded, progressive road laying; Era III sponsors lay 2×.
- Effect: +30% shipments on routes between completed-highway endpoints.
- Rule hook: `_auto_road_rule` falls through to highway sponsorship when
  own network saturates — all agent types benefit without new actions.
- Persistence: serialize/deserialize gained a 12th tuple element
  (highway_projects); five explicit unpackings updated across cli/tests.
- Live: Brazemi sponsored and completed a 28-segment highway by tick
  1200 on seed 42. Fast suite: 430 passing in both pytest-randomly
  orders.
- **[DECISION] Pause-don't-cancel**: funding gaps are temporary;
  cancelling wastes paid segments. Resumes when stone returns.
- **[DECISION] Era III speed not cost**: administration doubles lay
  rate rather than discounting stone.
- **Gotcha**: growing deserialize_world's return tuple breaks every
  explicit unpack — prefer `*_, last` or indexing in tests.

### Session 36 — Sprint 34 (this session)
- `treaties.py`: formal treaties with clauses — non_aggression (raids
  blocked between parties at BOTH legality layers), trade_pact (+25%
  shipments), tribute (wealthier→poorer every 100 ticks);
  deterministic acceptance predicate; treaty-ONLY-tribute reserved for
  victor-imposed terms.
- Federations DERIVED: alliance-graph components with ≥3 members, pure
  function of state (third application of the derived-state pattern:
  eras, prices, federations). Members ship +15% to each other.
- Rule hook `maybe_propose_treaties` cadence-gated per settlement;
  summaries show "Treaties:" and "Federations:" lines; sim.treaties is
  the 13th snapshot field (five more unpackings updated).
- Live smoke on seed 42 @1500 ticks: all three settlements signed
  pairwise trade+non-aggression treaties AND the derived federation
  mechanism detected the mutual-alliance triangle → a real federation
  formed. Fast suite: 450 passing.
- **[DECISION] Federations derived, not stored**: components can never
  disagree with the alliances they come from.
- **[DECISION] Non-aggression beats military friction**: warlike
  archetypes cannot raid treaty partners even when hostile — a signed
  pact is stronger than personality.
- **[DECISION] Tribute-only treaties need conflict**: friendly pairs
  sign trade/non-aggression pacts; tribute imposition awaits S35
  warfare. Mixed-clause treaties may include it.

### Session 37 — Sprint 35 (this session)
- `warfare.py`: armies (float pool), forts (battle defense, capped 3),
  siege_progress persisted on settlements; TRAIN_RAIDER/TRAIN_DEFENDER/
  FORTIFY_BORDER wired from reserved slots (frozen IDs); field battles
  every 100 ticks per war with home-ground/fort defense multipliers and
  seeded rng; winner −15% army, loser −30%; SIEGE at 3 attacker wins →
  victor-imposed tribute treaty ends the war; WAR_EXHAUSTION_TICKS
  white-peaces stale wars; upkeep eats food, starving armies melt.
- Live: Brazemi won a field battle, then bilateral peace policies ended
  the war before the siege matured — warfare/diplomacy interplay is
  emergent. Fast suite: 466 passing.
- **[DECISION] Wiring reserved IDs is sanctioned**: agent_spec's "wire
  real features into reserved slots" — DISBAND_MILITARY etc. stay
  no-ops until needed; no renumbering, still 62 actions.
- **[DECISION] One shared army pool**: forts + home ground give
  defenders their edge; splitting off/def pools rejected as bookkeeping
  without depth.
- **[DECISION] Sieges impose treaties, not city capture**: capitulation
  via victor-imposed tribute reuses S34 machinery and avoids ownership
  transfer this sprint.

### Session 38 — Sprint 36 (this session)
- `recovery.py`: refugee migration (≤half of final pop to allies/
  federation members, federation first, ≤3 per recipient, "migration"
  events); enriched ruins record era/technologies/salvage; re-settlers
  inherit technologies + salvage ("recovery" events — civilizations
  rebound faster than they rose); building decay rides the scarcity-
  collapse cadence (one building stripped per collapse event).
- RuinSite encode/decode extended backward-compatibly.
- Live: killed an Era-2 settlement with 3 techs → ruin captured
  everything, ally absorbed 3 refugees, reborn civilization inherited
  all three technologies plus salvage. Fast suite: 478 passing.
- **[DECISION] Decay rides the collapse cadence**: a separate decay
  counter can never fire because the collapse branch resets it every 48
  ticks; decline now hits population and infrastructure together.
- **[DECISION] Inherit technologies, not research points**: knowledge
  persists in ruins; unfinished research does not.
- **[WHY] Per-recipient refugee cap (≤3)**: prevents single-ally
  instant booms; unabsorbed refugees vanish.
- **Test-design lesson**: production economics + market trade rescue
  negative-stockpile scenarios within ticks — scarcity tests must drive
  the counter directly or isolate from trade.

### Session 39 — Sprint 37 (this session)
- Long-horizon stability: event log capped at 20k / experience buffer
  at 50k rows (oldest dropped; events are advisory so physics never
  depended on them); `sim.history` records compact epoch records every
  500 ticks (~200 per 100k ticks, byte-identical across identical sims);
  food_income/food_capacity memoized per tick.
- Soak (slow tier): 10k ticks in 66s = 151 ticks/s → **100k ticks ≈ 11
  minutes**; traced memory growth 4.5 MB over warm ticks; total-collapse
  run keeps ticking through the real _kill path; determinism verified.
- Fast suite: 486 passing.
- **[DECISION] Events are droppable**: the Phase 5 advisory-only
  principle makes bounding the log free stability — capping cannot
  change physics.
- **Test lesson: tracemalloc lies about speed** (~10x allocation
  slowdown) — measure time untraced, memory in a separate traced pass.

### Session 40 — Sprint 38 (this session)
- §16 surface completion: god_spawn_settlement (deterministic names,
  agent auto-registered), god_toggle_freeze (absolute time-stop — no
  decisions/production/growth/decay/death), god_bless_happiness.
- Audit trail (§16.3): every intervention logs a "divine" event
  prefixed "GOD:" including the previously-unaudited smite/bless/
  destroy. CLI --force required for smite ≥ 25 (§16.4).
- Persistence bug fixed: cmd_step/cmd_god silently dropped highways +
  treaties on load-step-save since S33/S34 (`*_,` unpack); both now
  pass them through explicitly.
- Phase 7 plan docs expanded with full detail for Sprints 38–43.
- Live smoke: spawn + freeze through 60 ticks + bless verified; four
  GOD: events logged. Fast suite: 503 passing.
- **[DECISION] Freeze is absolute**: no partial ticking; the point is
  suspending judgment on a settlement's fate.
- **[DECISION] Interventions audited in-world** so LLM advisors can
  react to gods via event-triggered reasoning, not just SQLite rows.

### Session 41 — Sprint 39 (this session)
- `god_trigger_disaster(type, x, y, radius, duration)`: authored
  drought/fire/plague; drought duration defaults to the standard 200;
  zone preview (sorted affected settlements) in the before-dict; effect
  counts (tiles burned / plague deaths) in the after-dict.
- **Shared application path**: `_check_disasters` and god disasters
  both route through `_apply_disaster()` — natural vs authored
  divergence is structurally impossible.
- CLI: `god --action trigger_disaster --disaster-type --x --y
  [--radius] [--duration]` with validation + divine audit.
- Live: drought on Brazemi → affected list correct, multiplier 0.5,
  GOD: event logged. Fast suite: 512 passing.

### Session 42 — Sprint 40 (this session)
- `regions.py`: pure geometry — circle/rect tile selectors (sorted
  row-major, edge-clipped) + spawn-based settlement selection.
- New audited god ops: bless/strip/smite_region (spawn-position
  targeting), mass_bless (archetype filter), god_bless_land (persistent
  per-tile food yield overrides on World.tile_food_bonus, applied at
  income-read time, serialized with world state).
- CLI: five new god actions; smite_region always requires --force.
- Live smoke: mass bless hit all 3, region wood +50, land blessing
  lifted income 36→54. Fast suite: 529 passing.
- **[DECISION] Yield bonuses at read time**: base grid stays int+cached;
  a separate bonus dict summed into income avoids dtype churn and cache
  invalidation traps.
- **[DECISION] Region ops target spawn positions**: predictable "who is
  standing in the blast zone" semantics, O(1) membership.
- **[DECISION] smite_region needs --force always**: catastrophic by
  class, not by sum.

### Session 43 — Sprint 41 (this session)
- `god_terraform(x, y, terrain)` + `god_terraform_region`: transform
  tiles into any of six terrains; `world._food_grid` reset + sim cache
  invalidated so yields update within the same tick (proven by test).
- Improvement-compatibility policy: buildings die when new terrain
  leaves their spec.valid_terrain; roads survive everywhere except
  water; losses counted in the after-dict.
- movement_cost is a derived property — updates automatically.
- CLI: terraform / terraform_region with --terrain validation.
- Live: fertile→mountain single tile; desert region reshaped 35 tiles,
  4 improvements lost, income dropped 44→−9. Fast suite: 539 passing.
- **[DECISION] Incompatible buildings destroyed, not relocated**: a
  farm on mountain rock is dead land; the audit shows the cost.
- **[DECISION] Roads die only under water**: graded earth works on any
  land surface.

### Session 44 — Sprint 42 (this session)
- `god_nuke(x, y)`: annihilates all improvements in NUKE_RADIUS=12;
  settlements spawned in the fireball lose 60% population through the
  real _kill path (ruins/refugees/recovery apply); leaves a
  ContaminationZone for ~20 years (yields suppressed to 25% via per-tick
  mask in the memoized income path; happiness bleeds 0.01/tick).
- Zones expire on schedule; terrain-only yields restore fully, destroyed
  buildings stay gone. Contamination surfaced in world summaries.
- CLI: nuke requires BOTH --force AND --confirm (§16.4 double
  confirmation). Persistence: contamination_zones = 15th snapshot field.
- Live: strike on Brazemi → 9 improvements annihilated, 8 dead,
  income 94→20 under fallout, zone until t7400. Fast suite: 550 passing.
- **[DECISION] Deaths route through _kill**: nuclear war is catastrophic
  but lives inside the same rules — no special-casing.
- **[DECISION] Fallout despair > routine joy**: bleed (0.01) exceeds
  natural recovery (~0.0055) so contaminated settlements genuinely
  suffer.
- **[DECISION] Two separate confirmation flags**: --force and --confirm;
  one flag is too easy to alias out of habit.

### Session 45 — Sprint 43 (this session)
- `undo_points` table: every APPLIED god intervention writes a full-state
  pre-intervention snapshot (captured before mutation, persisted only
  when the action lands).
- CLI `worldsim undo --world-id`: reverts in place; `--as-world NEW`
  restores into a new world id so alternate timelines coexist
  (`skip_entity_rows=True` — branch worlds live in their snapshot
  state_json since settlements.id is globally unique in aux tables).
- `Simulation.from_state_json`: replay/branching primitive.
- **Latent serialization bug fixed (since Sprint 5!)**: happiness,
  negative_food_streak, low_happiness_progress were never serialized —
  save/resume silently reset settlement mood to 0.5. Found by the
  byte-exact undo test. Encode/decode now round-trips all three.
- Live smoke via real CLI: generate → simulate → smite (12→0) → undo
  (restored to tick 50, pop 12) → branch timeline created.
- 7 new tests. Fast suite: 557 passing.
- **[DECISION] Undo points written post-application**: captured pre-
  mutation, persisted only when actions land — no junk points from
  validation failures.
- **[WHY] Byte-exact restore as acceptance**: exact-equality summaries
  have now caught two latent bugs (happiness reset here, transposed
  territory checks in S31).

### Session 46 — Sprint 44 (this session)
- `visualization.py`: render_ascii_map with deterministic glyph
  priority (contamination ! > settlement initial > ruin X > road # >
  building b > terrain), crop windows, settlement panels, export_map_png
  (fixed palette + Agg → **byte-identical PNGs** for identical worlds).
- CLI: `worldsim map --world-id [--crop] [--png] [--panels]`.
- Live smoke on the S43 test world: region map with roads/buildings/
  initials + legend, panels, PNG written.
- Phase 8 plan docs: Sprint 44 detailed.
- 12 new tests. Fast suite: 569 passing.
- **[DECISION] Byte-deterministic PNG export**: identical worlds produce
  identical binary output — "identical worlds produce identical
  everything" extended to pixels.
- **[DECISION] Contamination glyph outranks settlements**: a ! over your
  capital's letter is exactly the alarm a map should raise.

### Session 47 — Sprint 45 (this session)
- `histories.py`: `build_chronicle` — each civilization's saga
  reconstructed deterministically from the persisted event log
  (founding, techs, eras, wars, battles, treaties, disasters, divine
  interventions, migration, fall/rebirth cross-references);
  `civilizations_summary` one-liners; `population_curves` from S37
  epoch history (now recording a per-settlement `populations` map).
- CLI: `worldsim chronicle --world-id [--settlement-index] [--png]`
  with matplotlib population-curve export.
- Live smoke on seed 42 @1200 ticks: Brazemi's chronicle showed
  founding, a divine blessing, trade routes, alliances, agriculture →
  masonry → engineering discoveries, Era 2 advancement, and treaties —
  emergent history readable as a saga. Fast suite: 582 passing.
- **[DECISION] Chronicles derive from the event log** — zero new state,
  nothing new persisted; text works across save/load while curves are
  RAM-only within a run (documented limitation).
- **[DECISION] Divine events now carry actor_ids**: god interventions
  appear in the affected settlements' chronicles — the audit trail got
  strictly better for free.

### Session 48 — Sprint 46 (this session)
- `timeline.py`: fixed category taxonomy (warfare/diplomacy/
  civilization/trade/divine/disasters/other, unknown→other);
  `build_timeline` with AND-combined type/category/actor/since filters,
  oldest-first limit; `render_timeline` with human date stamps;
  `category_histogram` — zero-filled per-window counts so series plot
  without padding logic.
- `export_event_histogram`: stacked bars per category per window with a
  fixed palette — byte-deterministic for identical worlds.
- CLI: `worldsim timeline --world-id [--types] [--categories] [--since]
  [--limit] [--no-dates] [--png --window]`.
- Live smoke on seed 42 @1500 ticks: warfare+diplomacy timeline showed
  the alliance and three treaties with human dates; histogram PNG
  written to screenshots/.
- 15 new tests. Fast suite: 597 passing.
- **[DECISION] Limit keeps OLDEST events first**: timelines read
  start-to-finish; newest-events views can slice from the end.
- **[WHY] Zero-filled windows**: every category series exists in every
  window — charts work with no padding logic at the consumer side.

---

## Conventions

---


---


- Every sprint: implement → verify acceptance → update `notes.md` →
  commit+push → append changelog row + session decisions here.
- Determinism is sacred: all simulation state transitions are pure functions
  of `(seed, tick)`; entropy only enters via seeded streams; entity IDs are
  deterministic uuid5 derivatives.
- New mechanics must ship with unit tests; long-running verification belongs
  in the `slow` tier.
