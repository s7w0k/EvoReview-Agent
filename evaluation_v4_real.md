# Multi-Agent Value Evaluation V4

| Variant | planning_quality | replan_quality | collaboration_quality | loop_quality | efficiency | overall |
|---|---|---|---|---|---|---|
| Full (baseline) | 0.0 | 1.0 | 1.0 | 0.4889 | 0.1998 | 0.5377 |
| No Dynamic Planner | 0.0 | 1.0 | 1.0 | 0.4889 | 0.1998 | 0.5377 |
| No Targeted Replan | 0.0 | 1.0 | 1.0 | 0.4889 | 0.1998 | 0.5377 |
| No Critic | 0.0 | 1.0 | 1.0 | 0.9889 | 0.4917 | 0.6961 |
| No Verifier | 0.0 | 1.0 | 1.0 | 0.8222 | 0.3302 | 0.6305 |
| No Parallel Scheduler | 0.0 | 1.0 | 1.0 | 0.4889 | 0.1998 | 0.5377 |
| Shallow Loops | 0.0 | 1.0 | 1.0 | 0.4889 | 0.1998 | 0.5377 |

## Ablation deltas vs baseline (A)

| Variant | delta overall |
|---|---|
| B | 0.0000 |
| C | 0.0000 |
| D | 0.1584 |
| E | 0.0927 |
| F | 0.0000 |
| G | 0.0000 |

## Evolution Attribution (plan §12)

- Full (baseline): REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=34
- No Dynamic Planner: REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=34
- No Targeted Replan: REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=34
- No Critic: REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=59
- No Verifier: REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=59
- No Parallel Scheduler: REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=34
- Shallow Loops: REPLAN_INSUFFICIENT=10, SPECIALIST_LOOP_TOO_SHALLOW=59
