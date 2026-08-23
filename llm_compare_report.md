# Policy vs Rule-Based Baseline — Evaluation Report

- Worlds evaluated: **10**
- Difficulty: {'disaster_chance_mult': 2.0, 'gather_mult': 0.5}
- Survival win fraction (strict): **0%** (ties: 10)
- Reward win fraction: **100%**

## Metric summary

| Metric | Baseline | Policy | Delta | Wilcoxon p |
|---|---|---|---|---|
| survival_ticks | 300.0 | 300.0 | +0.0 | 1.0 |
| peak_population | 20.9 | 20.9 | +0.0 | 1.0 |
| territory | 508.0 | 841.1 | +333.1 | 0.0039 * |
| buildings | 36.2 | 46.4 | +10.2 | 0.002 * |
| routes_established | 1.0 | 1.0 | +0.0 | 1.0 |
| cumulative_reward | 2.99 | 3.19 | +0.2 | 0.002 * |

* p < 0.05 (Wilcoxon signed-rank)

## Per-world detail

| Seed | Surv B | Surv P | Peak B | Peak P | Terr B | Terr P | Bld B | Bld P | Reward B | Reward P |
|---|---|---|---|---|---|---|---|---|---|---|
| 50000 | 300 | 300 | 22 | 22 | 924 | 1645 | 19 | 23 | 2.93 | 3.01 |
| 50001 | 300 | 300 | 22 | 22 | 391 | 864 | 19 | 27 | 2.93 | 3.09 |
| 50002 | 300 | 300 | 20 | 20 | 256 | 625 | 30 | 40 | 1.95 | 2.15 |
| 50003 | 300 | 300 | 18 | 18 | 665 | 1080 | 34 | 43 | 2.43 | 2.61 |
| 50004 | 300 | 300 | 17 | 17 | 625 | 625 | 33 | 48 | 2.21 | 2.51 |
| 50005 | 300 | 300 | 22 | 22 | 350 | 527 | 61 | 73 | 3.89 | 4.13 |
| 50006 | 300 | 300 | 22 | 22 | 1103 | 1364 | 53 | 63 | 3.61 | 3.81 |
| 50007 | 300 | 300 | 22 | 22 | 169 | 600 | 33 | 46 | 3.21 | 3.47 |
| 50008 | 300 | 300 | 22 | 22 | 441 | 841 | 62 | 75 | 3.79 | 4.05 |
| 50009 | 300 | 300 | 22 | 22 | 156 | 240 | 18 | 26 | 2.91 | 3.07 |