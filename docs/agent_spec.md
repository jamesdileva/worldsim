# Agent Specification — Observation & Action Spaces (Sprint 7)

> **This is the RL contract.** Dimensions and action IDs are frozen from day
> one; later sprints wire real features into reserved slots but never change
> shapes or renumber IDs. Changing either invalidates trained policies.

---

## Observation Space

- Shape: `(60,)`, dtype `float32`, all values in `[0.0, 1.0]`
- One vector per settlement, rebuilt every tick
- Reserved dimensions are filled with `0.0` until their mechanics exist

| Index | Feature | Normalization |
|-------|---------|---------------|
| 0 | population | pop / 100 |
| 1 | food stock | stock / food capacity |
| 2 | net food rate | (clamp(rate, −1, 1) + 1) / 2 |
| 3 | wood | inventory / 1000 |
| 4 | stone | inventory / 1000 |
| 5 | metal | inventory / 1000 |
| 6 | territory size | tiles / 1000 |
| 7 | farms | count / 50 |
| 8 | sawmills | count / 50 |
| 9 | mines | count / 50 |
| 10 | granaries | count / 50 |
| 11 | roads | count / 200 |
| 12 | happiness | direct (already 0–1) |
| 13 | scarcity flag | 1 if any inventory < 0 |
| 14 | growth progress | progress / 24 |
| 15 | starvation progress | progress / 48 |
| 16 | settlement age | ticks / 2000 |
| 17 | ruin-origin flag | 1 if founded on ruins |
| 18 | active trade routes | count / 10 |
| 19 | season | (tick // 128 % 4) / 3 |
| 20 | water terrain share | owned-tile fraction |
| 21 | desert terrain share | owned-tile fraction |
| 22 | plains terrain share | owned-tile fraction |
| 23 | fertile terrain share | owned-tile fraction |
| 24 | forest terrain share | owned-tile fraction |
| 25 | mountain terrain share | owned-tile fraction |
| 26 | build queue length | len / 10 |
| 27 | economic collapse timer | progress / 48 |
| 28 | low-happiness timer | progress / 100 |
| 29 | negative-food streak | ticks / 50 |
| 30 | drought exposure | active droughts / 3 |
| 31 | ruin adjacency flag | 1 if within 2 tiles of origin ruin |
| 32–37 | **reserved**: military | wired Sprint 9+ (units) |
| 38–41 | **reserved**: research/tech | wired Phase 3+ |
| 42 | hostile neighbors | count / 5 |
| 43 | friendly neighbors | count / 5 |
| 44 | contested tiles | count / 500 |
| 45–47 | **reserved**: diplomacy detail | wired Sprints 10–11 |
| 48 | living settlements | count / 20 |
| 49 | ruins | count / 20 |
| 50 | active disasters | count / 5 |
| 51 | year | year / 100 |
| 52 | tick-in-year fraction | (tick % 512) / 512 |
| 53–59 | **reserved**: meta | unwired |

## Action Space

- Discrete: `Discrete(60)`
- Unwired actions are validated no-ops (they consume the decision, nothing else)
- Wired actions route through the same mechanic methods the old auto-rules used

### Production (0–9)
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 0 | BUILD_FARM | yes | build farm on best valid owned tile |
| 1 | BUILD_SAWMILL | yes | build sawmill (forest tile) |
| 2 | BUILD_MINE | yes | build mine (mountain tile) |
| 3 | BUILD_GRANARY | yes | build granary (+500 food cap) |
| 4 | UPGRADE_BUILDING | no-op | Sprint 6+ economy |
| 5 | REPAIR_STRUCTURE | no-op | with degradation system |
| 6 | BUILD_SECOND_FARM | no-op | alias of farm build |
| 7 | BUILD_SECOND_SAWMILL | no-op | alias |
| 8 | BUILD_SECOND_MINE | no-op | alias |
| 9 | DEMOLISH_BUILDING | no-op | future |

### Infrastructure (10–19)
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 10 | BUILD_ROAD | yes | extend road network one tile |
| 11 | EXPAND_ROAD_NETWORK | yes | same as 10 |
| 12 | CONNECT_TERRITORY | no-op | inter-settlement roads |
| 13 | REPAIR_ROADS | no-op | with degradation |
| 14–17 | BUILD_ROAD_{EAST,WEST,NORTH,SOUTH} | no-op | directional roads |
| 18 | SURVEY_TERRITORY | no-op | scouting info |
| 19 | IDLE_INFRASTRUCTURE | no-op | |

### Expansion (20–29)
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 20 | CLAIM_TERRITORY | yes | claim one ring of adjacent unowned tiles |
| 21 | FOUND_NEW_SETTLEMENT | no-op | Sprint 9+ |
| 22 | SCOUT_NEARBY | no-op | fog-of-war dependent |
| 23–29 | (aggressive claim, consolidate, etc.) | no-op | Sprint 9+ |

### Economy (30–37)
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 30 | ESTABLISH_TRADE_ROUTE | yes | connect to all adjacent unlinked non-hostile settlements |
| 31 | REQUEST_RESOURCE_TRADE | no-op | Sprint 10 diplomacy |
| 32–37 | (store/sell/buy/budget/hedge/idle) | no-op | markets are Phase 6 |

### Military (38–43) — partially wired as of Sprint 9
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 41 | INITIATE_RAID | yes | raid hostile neighbor's contested buildings: 200-tick output debuff + theft |
| 38–40, 42–43 | (train/fortify/disband/idle) | no-op | units arrive later |

### Research (44–47) — unwired until Phase 3+
RESEARCH_TECHNOLOGY, PRIORITIZE_INNOVATION, SHARE_KNOWLEDGE, IDLE_RESEARCH

### Social (48–53)
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 48 | BOOST_MORALE | yes | +0.01 happiness |
| 49–53 | REALLOCATE_WORKERS … IDLE_SOCIAL | no-op | future |

### Meta (54–59)
| ID | Name | Wired | Effect |
|----|------|-------|--------|
| 54 | RE_EVALUATE_STRATEGY | no-op | Phase 4 strategy memory |
| 55 | SAVE_STATE | no-op | handled by CLI auto-save |
| 56 | CHECK_NEIGHBORS | no-op | Sprint 9 |
| 57 | EMERGENCY_RESPONSE | no-op* | rule-based agent uses it as a famine signal; currently a no-op effect |
| 58 | WAIT | yes (no-op) | explicit pass |
| 59 | IDLE | yes (no-op) | explicit pass |

## Reward (placeholder until Sprint 13)

```
reward = 0.1 * Δpopulation + 0.05 * Δbuildings − 0.1 * starving + 0.001
clamped to [−1, +1]
```

## Experience Logging

One row per living settlement per tick in `agent_history`:
`(settlement_id, tick, observation BLOB(float32×60), action INT, reward REAL,
next_observation BLOB, done BOOL)`. Transitions are buffered in RAM and
flushed to SQLite every 500 ticks (and at save) — never per-tick writes.
