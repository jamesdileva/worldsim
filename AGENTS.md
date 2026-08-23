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
learning healthy in training metrics, eval metrics saturated), **Phase 5 in
progress** (Phase 4 ✅ COMPLETE: S19–24; S25 Ollama integration done; sprint
docs expanded through Phase 10).

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
