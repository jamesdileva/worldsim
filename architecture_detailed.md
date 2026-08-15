# World Simulator — Detailed Architecture Specification

> **Status:** Draft v1.0  
> **Scope:** Phase 1 (v0.1) through Milestone 1 (Living Ant Farm)  
> **Target Release:** End of Sprint 6

---

## 1. Vision

### 1.1 Core Concept

The project is an **autonomous AI civilization simulator** where settlements independently make decisions, construct infrastructure, expand, compete, cooperate, survive, and fail. The user observes but does not directly control every entity. The simulation is designed to produce **emergent behavior** through machine learning and strategic reasoning.

### 1.2 Core Philosophy

> **The developer defines the laws of the world, not the outcome of the world.**

The world operates by deterministic rules. Civilizations emerge from the interaction of these rules with autonomous agents. Success or failure is not scripted; it is observed.

### 1.3 The Ant Farm Concept

| Property | Description |
|---|---|
| Settlements | Behave like colonies -- self-directed, goal-oriented entities |
| Agents | Operate continuously within the world's physics |
| User | Acts as an observer/god — can interfere but does not control |
| World | Continues evolving without user intervention |

### 1.4 Long-Term Vision

Agents progressively become better through machine learning across generations. Each simulation episode feeds back into training, producing policies that improve over time. The world becomes increasingly complex without requiring every behavior to be manually programmed.

### 1.5 What Makes This Different From SimCity

| Aspect | SimCity | WorldSimulator |
|---|---|---|
| Control | User is city planner | Cities are autonomous |
| Strategies | Hand-crafted balance | Learned through RL |
| Outcomes | Predictable by design | Emergent and surprising |
| Civilizations | Single player-controlled | Multiple independent agents |
| Failure | Player restarts | Failure feeds learning |

---

## 2. Core System Architecture

### 2.1 High-Level Architecture Layers

```text
┌─────────────────────────────────────────────┐
│              Frontend/UI Layer               │
│  Electron + React + PixiJS/WebGL             │
├─────────────────────────────────────────────┤
│           FastAPI Bridge Layer              │
├─────────────────────────────────────────────┤
│           Simulation Engine Layer           │
│  Python + NumPy                              │
├─────────────────────────────────────────────┤
│         Machine Learning Engine Layer       │
│  PyTorch + Stable-Baselines3                 │
├─────────────────────────────────────────────┤
│          Local LLM Integration Layer        │
│  Ollama                                      │
├─────────────────────────────────────────────┤
│          Persistence/Data Layer             │
│  SQLite + SQLAlchemy                         │
└─────────────────────────────────────────────┘
```

### 2.2 Separation of Concerns

| Concern | Subsystem | Rationale |
|---|---|---|
| Deterministic world physics | Python Simulation Engine | Seeded reproducibility |
| Agent decision-making | RL Policy Network | Trainable, inspectable policy |
| Machine-learning policy | Stable-Baselines3 | Industry standard RL library |
| Rendering | PixiJS/WebGL via GPU | GPU-accelerated, decoupled from simulation |
| Narrative/LLM systems | Ollama (optional) | Periodic strategic reasoning only |
| User interaction | React UI | Controls, inspectors, dashboards |

### 2.3 Simulation Loop

The core loop drives both live observation and training:

```text
World State (t)
     ↓
Agent Observation (perceive world state)
     ↓
Agent Decision (policy selects action)
     ↓
Action Execution (world applies action)
     ↓
World Tick (physics, resource flow, etc.)
     ↓
Outcome (new state + events)
     ↓
Reward / Feedback (per-agent, per-settlement)
     ↓
Policy Update (only during training mode)
     ↓
Improved Agent / Next Iteration
```

**Live Mode:** Loop runs continuously in a single Python process; rendering updates via FastAPI → PixiJS.  
**Training Mode:** Loop runs headless; reward is computed per-tick; no rendering overhead.

### 2.4 Deterministic Simulation Core

| Feature | Requirement | Implementation |
|---|---|---|
| Seeded worlds | Reproducible generation | `random.seed(world_seed)` at init |
| Reproducible terrain | Perlin/simplex noise with fixed seed | `noise` library |
| Reproducible resources | Deterministic placement | Seeded Voronoi or grid |
| Deterministic physics/rules | All updates are functions of state only | No external RNG in tick logic |
| Replay capability | Snapshots enable full rewind/replay | SQLite checkpoints + state diff |
| Simulation snapshots | Periodic state capture | JSON-serialized world state at tick N |

---

## 3. World Model

### 3.1 Terrain

The world is a 2D grid of tiles. Each tile has properties:

| Property | Type | Notes |
|---|---|---|
| `x, y` | int | Grid coordinates |
| `terrain_type` | enum | Plains, Forest, Mountain, Water, Desert, Fertile |
| `movement_cost` | float | Multiplicative cost for worker movement |
| `resource_yield` | dict | Base resource production (food, wood, stone, etc.) |
| `owner_id` | UUID | Settlement claiming the tile |
| `improvement` | enum | Road, Farm, Mine, etc. |
| `elevation` | float | 0–1 scale |
| `moisture` | float | 0–1 scale |

**Terrain Types & Characteristics:**

| Terrain | Movement | Food | Wood | Stone | Metal | Notes |
|---|---|---|---|---|---|---|
| Plains | 1.0 | + |  |  |  | Fast travel, open |
| Forest | 1.2 | + | +++ |  |  | High wood yield |
| Mountain | 2.0 |  |  | ++ | + | Metal veins |
| Water | 5.0 |  |  |  |  | Requires bridge |
| Desert | 1.5 | - |  |  | rare | Low fertility |
| Fertile | 1.0 | ++ |  |  |  | High food yield |

### 3.2 Resources

Resources are categorized into two buckets:

#### Renewable Resources (regenerate over time)
| Resource | Source | Regeneration Rate |
|---|---|---|
| Food | Terrain yield (fertile/plains) | Per-tile daily |
| Water | Water tiles | Per-tile daily |

#### Non-Renewable Resources (depletable)
| Resource | Source | Depletion Mechanism |
|---|---|---|
| Wood | Forest tiles | Logging depletes forest density |
| Stone | Mountain tiles | Quarrying depletes stone deposit |
| Metal | Mountain tiles | Mining depletes ore vein |
| Energy | Rare resource nodes | Extracted, no regen |

### 3.3 Environmental Systems

| System | Mechanics | Data Structure |
|---|---|---|
| Weather | Seasonal cycle (rain/dry seasons) affects resource yield | `world.weather` dict |
| Seasons | 4-season cycle: Spring (growth), Summer (peak), Autumn (harvest), Winter (slower production) | `world.season_timer` |
| Climate | Affects base terrain productivity | `world.climate_map` |
| Resource Regeneration | Renewables grow back slowly; non-renewables require time to discover new deposits | Regeneration rules per resource |
| Degradation | Over-farming or over-mining reduces tile yield temporarily | `tile.degradation` stat |

### 3.4 World Boundaries

| Property | Design |
|---|---|
| World size | 256x256 tiles (default, configurable) |
| Edge behavior | Wrap-around (toroidal) initially; borders may be added later |
| Procedural generation | Optional; seeded for reproducibility |
| Infinite world | Future expansion: chunk-based loading |

---

## 4. Settlement System

### 4.1 Settlement Creation

Each settlement starts with a minimal set of properties:

| Property | Initial Value | Notes |
|---|---|---|
| Population | 10 workers | Grows over time |
| Food stores | 50 units | Must sustain population |
| Territory | 3x3 tiles centered on spawn point | Expands via claiming |
| Resources | Starting reserves | Determined by spawn conditions |
| Capabilities | Basic gathering/building | Unlocks via development |
| Unique characteristics | Personality, cultural traits | Affects decision bias |

### 4.2 Settlement Identity

Each settlement has an identity that influences its behavior and is visible to the user:

| Attribute | Type | Source |
|---|---|---|
| Name | String | Procedurally generated |
| Culture | Enum | Assigned at creation |
| Color | RGB | Based on culture |
| Symbol | Icon | Cultural symbol |
| History | List of events | Populated by event system |
| Personality/behavioral tendencies | Vector (e.g., [expansionist, peaceful, innovative]) | Assigned or learned |

### 4.3 Settlement State (Detailed)

The settlement state is the observation vector for the RL agent. It is a compact representation:

```json
{
  "id": "uuid",
  "name": "string",
  "population_count": int,
  "food_surplus": float,
  "resource_inventory": {
    "food": int, "wood": int, "stone": int, "metal": int, "energy": int
  },
  "buildings": [{"type": "string", "x": int, "y": int}],
  "road_network": [{"from": [x,y], "to": [x,y]}],
  "territory_radius": int,
  "technology_level": int,
  "military_strength": int,
  "happiness": float (0.0–1.0),
  "economic_growth_rate": float,
  "recent_events": [{"type": "string", "tick": int}],
  "neighbors": [{"settlement_id": "uuid", "relation": "friendly/hostile/neutral"}]
}
```

### 4.4 Settlement Goals

Settlements have a hierarchy of goals that the agent pursues:

1. **Survival** — Maintain positive food surplus, avoid population death
2. **Growth** — Increase population, expand territory
3. **Expansion** — Claim new territory, found new settlements
4. **Resource Acquisition** — Build gatherer structures, trade
5. **Trade** — Establish trade routes with neighbors
6. **Defense** — Construct defensive structures, train military
7. **Conquest** — Expand into neighbor territory
8. **Technological Advancement** — Research new capabilities

These goals are encoded into the reward function. The agent learns to balance these priorities dynamically.

---

## 5. Autonomous Agent Architecture

### 5.1 Agent Definition

An agent is any entity capable of making decisions based on its observations and acting on those decisions within the world.

**Agent Components:**

| Component | Description | Data Type |
|---|---|---|
| Observations | Input vector from world state | List[float] |
| State | Internal memory (short-term) | dict or list[float] |
| Actions | Valid set of decisions | List of action enums |
| Policy | Learned strategy (neural network or rule set) | Function or model |
| Memory | Long-term strategy store | dict |

**Observations (Settlement Agent):**

The agent receives a vector of normalized floats representing the settlement state. Key observation dimensions:

- Population / carrying capacity ratio
- Food surplus / deficit
- Resource scarcity (min/max normalized inventory)
- Territory utilization
- Building efficiency (ratio of functional vs. planned)
- Neighbor count and relations
- Time since last expansion
- Technology progress

### 5.2 Agent Decision Space (Discrete Action Set)

The action space is a discrete set of 60 actions. Each action maps to a settlement-level strategic decision:

| Category | Action Count | Examples |
|---|---|---|
| Production | 10 | Build farm, build mine, build sawmill, upgrade building |
| Infrastructure | 10 | Build road, connect territory, repair structure |
| Expansion | 10 | Claim territory, found new settlement, scout nearby |
| Economy | 8 | Start trade route, request resource trade, store surplus |
| Military | 6 | Train defender, train raider, fortify border, initiate raid |
| Research | 4 | Research technology, prioritize innovation |
| Social | 6 | Boost morale, reallocate workers, optimize layout, |
| Meta | 6 | Re-evaluate strategy, save state, check neighbors, idle, wait, emergency response |

*Note: Continuous action variants may be introduced in Phase 5 (Advanced Intelligence) for fine-grained control.*

### 5.3 Hierarchical Agents

```text
World Civilization Agent (Level 3)
        ↓
Settlement Agents (Level 2)
        ↓
Worker/Individual Agents (Level 1)
```

- **Level 3 (Civilization):** Oversees multiple settlements, coordinates macro-strategy. Not present in Sprint 1–6; introduced in Phase 3+.
- **Level 2 (Settlement):** Primary RL agent during Phase 1–3. Makes strategic decisions per settlement.
- **Level 1 (Worker):** Executes low-level tasks (gathering, building) assigned by Level 2. Rule-based in Phase 1; may become trainable in Phase 9+.

### 5.4 Agent Memory

| Memory Type | Storage | Contents |
|---|---|---|
| Short-term state | In-memory dict | Current tick's perception |
| Historical outcomes | SQLite table (agent_history) | Sequence of (state, action, reward) tuples |
| Learned policies | Model files (models/policy_vN.pth) | Neural network weights |
| Strategy memory | In-memory cache + periodic checkpointing | High-level strategic patterns |

### 5.5 Agent Autonomy

Agents must not follow a scripted "optimal path." They determine their own priorities based on:

- Current settlement state
- Environmental pressures
- Neighbor interactions
- Internal reward signal

Different agents start with different random seeds for their policy networks, leading to natural divergence.

---

## 6. Machine Learning Architecture

### 6.1 ML Objective

Train settlement agents to become:

- **Smarter:** Better resource allocation over time
- **Faster:** Quicker convergence to effective strategies
- **More efficient:** Lower action waste, higher yield per action
- **More adaptive:** Respond to environmental changes
- **More successful:** Achieve higher survival/growth rates

### 6.2 Learning Loop (Training Mode)

```text
For each training episode:
    Initialize world (seeded)
    Initialize settlement agent with policy π
    For each tick:
        obs = agent.observe(world)
        action_idx = agent.decide(obs)   # policy forward pass
        world.apply(action_idx)          # execute action
        reward = world.evaluate_agent(agent)  # compute reward
        agent.store_experience(obs, action_idx, reward)
    End tick loop
    policy_loss = agent.update_policy()  # gradient step
    Log metrics (episode reward, loss, accuracy)
End episode
Checkpoint policy → SQLite
```

### 6.3 Learning Approaches (Phase 1–3)

| Approach | Use Case | Status |
|---|---|---|
| Reinforcement Learning (PPO) | Primary training method | Implemented Sprint 12–14 |
| Rule-based baseline | Initial agent before learning | Implemented Sprint 8 |
| Evolutionary Strategies | Policy mutation/selection | Phase 4 (Sprint 19+) |
| Self-play | Multi-civilization competition | Phase 4 (Sprint 22+) |

### 6.4 Reward System

**Positive Rewards:**
- Population growth (+0.1 per new citizen)
- Survival duration (+0.01 per tick alive)
- Food surplus maintained (+0.05 per surplus unit)
- Territory claimed (+1.0 per tile)
- Buildings constructed (+2.0 per building)
- Trade established (+5.0 per route)
- Resource efficiency maintained (+0.1 per efficient action)
- Technology researched (+3.0 per tech)
- Military success (+10.0 per enemy defeated)
- Civilization longevity (+0.001 per tick)

**Negative Rewards (Penalties):**
- Population loss (-5.0 per citizen lost)
- Famine declared (-10.0)
- Resource waste (-1.0 per wasted unit)
- Failed construction (-3.0)
- Unnecessary warfare (-2.0 per unprofitable conflict)
- Economic collapse (-20.0)
- Settlement abandonment (-50.0)

**Total reward is normalized per tick to [-1.0, +1.0].**

### 6.7 Policy Improvement

| Feature | Implementation | Timing |
|---|---|---|
| Versioned policies | `policies/policy_genN.pt` with metadata | Every checkpoint |
| Training checkpoints | SQLite `policy_checkpoints` table | Every 1000 episodes |
| Policy comparison | Evaluate on fixed benchmark worlds | Sprint 17 |
| Best-performing strategies | Keep top-5 policies by avg reward | Ongoing |
| Regression detection | Run A/B test against baseline | Sprint 17–18 |

---

## 20. Learning Infrastructure

### 20.1 Training Environment Isolation

Training runs in isolated Python processes. No UI, no rendering, no Electron.

**File structure:**
```
data/world_sim/
├── world.db                 # Live world + snapshots
├── snapshots/               # World state checkpoints
├── policies/                # Trained model weights + metadata
├── experiments/             # A/B test runs
└── replays/                 # Replay state logs
```

### 20.2 Simulation Episodes

Each episode:
1. Creates a world from a seed
2. Spawns N settlements at random positions
3. Runs for 5,000 ticks or until all settlements collapse
4. Returns final reward + metric log

### 20.3 Parallel Simulations

Uses `multiprocessing.Pool` to run up to `(CPU count - 1)` simulations in parallel. Each worker loads the world module and communicates results via shared memory queue.

### 20.5 Replay Buffer

A rolling buffer of the last 10,000 experiences (obs, action, reward, next_obs) is maintained in RAM. Periodically flushed to SQLite in batches to reduce I/O.

### 20.6 Policy Storage

Policies are stored as PyTorch state dicts with metadata:

```python
{
  "policy_id": "uuid",
  "generation": int,
  "training_episodes": int,
  "avg_reward": float,
  "max_reward": float,
  "created_at": ISO timestamp,
  "hyperparameters": { ... }
}
```

---

## 24. Persistence

### 24.1 World Database (SQLite Schema)

```sql
-- World metadata
CREATE TABLE worlds (
    id UUID PRIMARY KEY,
    seed TEXT,
    created_at TIMESTAMP,
    last_tick INTEGER
);

-- Snapshots: full world state at checkpoint
CREATE TABLE snapshots (
    tick INTEGER PRIMARY KEY,
    world_id UUID,
    state_json TEXT  -- compressed JSON of full state
);

-- Settlements (reference table for history queries)
CREATE TABLE settlements (
    id UUID PRIMARY KEY,
    name TEXT,
    world_id UUID,
    created_at_tick INTEGER,
    destroyed_at_tick INTEGER NULL
);

-- Agent history (for training)
CREATE TABLE agent_experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id UUID,
    tick INTEGER,
    observation BLOB,      -- numpy array serialized
    action INTEGER,
    reward REAL,
    next_observation BLOB,
    done BOOLEAN
);

-- Training metadata
CREATE TABLE training_runs (
    id UUID PRIMARY KEY,
    policy_version INTEGER,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    total_episodes INTEGER,
    final_avg_reward REAL,
    metadata_json TEXT
);

-- God Mode events
CREATE TABLE god_events (
    id UUID PRIMARY KEY,
    tick INTEGER,
    action_type TEXT,
    target_coords TEXT,
    before_state JSON,
    after_state JSON
);
```

### 24.2 Snapshot Strategy

- **Checkpoint frequency:** Every 500 ticks during live mode, every episode during training
- **Snapshot format:** Compressed JSON blob of the complete world state
- **Undo support:** God Mode actions log before/after states; undo restores the latest pre-intervention snapshot

---

## 26. Metrics & Observability

### 26.1 Civilization Metrics

| Metric | Calculation | Dashboard View |
|---|---|---|
| Avg. settlements alive | Count / time | Line chart over ticks |
| Max population across settlements | Peak value | Bar per settlement |
| Avg. food surplus | Sum / tick count | Time series |
| Total infrastructure built | Count of buildings | Cumulative |
| Territory owned | Area per settlement | Map overlay |
| Technology progress | Avg. research level | Heatbar |
| Wars won/lost | Event count | Pie chart |
| Economic trade volume | Sum of trade value | Time series |

### 26.2 Validation Dashboard

The dashboard answers: **"Are the agents actually getting better?"**

Key comparison:
- **Generation 1 policy** vs. **Latest trained policy**
- Both run on the same set of 10 benchmark worlds (fixed seeds)
- Comparison: avg survival time, final population, tech level, settlement count

---

## 29. Security & Stability

### 29.1 Action Validation

All agent actions pass through a validator before application to the world state:

```python
def validate_action(action_enum, world_state, agent_state):
    """
    Throws ActionValidationError if action is invalid given current constraints.
    Validates: resource requirements, territory access, cooldowns, etc.
    """
```

### 29.2 Infinite Loop Prevention

- Actions have a maximum retry limit (3 attempts)
- Each action consumes simulation time; time cannot go backward
- A global "action budget" per tick prevents agents from spamming actions indefinitely

### 29.3 ML Failure Handling

If a policy crashes or produces NaN outputs:
1. Fall back to the last known-good checkpoint policy
2. Log the crash with full traceback + world state
3. Notify the user via the event feed
4. Continue simulation without training for 10 ticks, then retry

---

## 33. Explicit Non-Goals (Phase 1)

| Non-Goal | Reason |
|---|---|
| Simulate every individual human | Too granular; hierarchy of agents better represents emergence |
| Photorealistic 3D worlds | Focus on emergent behavior first; 2D sufficient |
| LLM for every decision | O(n) inference overhead kills performance; strategic reasoning only |
| Massive local model training | Use Stable-Baselines3 + small networks; defer to cloud if needed |
| Full SimCity replacement | MVP focuses on learning loop, not polish |

---

## 38. Ultimate Project Definition (Phase 1)

> By end of Sprint 6, the system must be capable of:

1. Generating a deterministic world (seeded)
2. Spawning autonomous settlements (rule-based agents)
3. Running the full loop autonomously: observe → decide → act → reward
4. Allowing decisions to affect the world
5. Measuring outcomes
6. Saving/loading the world state from SQLite
7. Producing emergent events (e.g., resource scarcity causing conflict)
8. Allowing the user to observe the world via a live 2D map

> **Note:** ML training is not active until Phase 3. For Phase 1, agents are rule-based baselines that simulate the interface the RL policy will eventually plug into.

---

## 100. Appendix A: Key Design Principles

### A1. Determinism

All simulation logic must be a pure function of `(world_state, tick_number)`. External entropy (RNG) only enters during world generation.

### A2. Decoupling

Simulation logic lives entirely in Python. Rendering (PixiJS) and UI (React) are consumers of world state via FastAPI. No simulation state is stored in the frontend.

### A3. Scalability

The architecture supports scaling to thousands of parallel training simulations by isolating the training logic from the UI layer.

### A4. Reproducibility

Every world, agent, and training episode must be reproducible via a single seed. Policies are versioned with metadata to ensure experiments are repeatable.
