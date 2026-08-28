# Multi-Agent Value Evaluation V4

| Variant | detection_quality | planning_quality | replan_quality | collaboration_quality | loop_quality | latency_ms | tool_calls | a2a_calls | overall |
|---|---|---|---|---|---|---|---|---|---|
| Full (baseline) | 0.55 | 0.6705 | 0.9 | 0.6125 | 0.8875 | 3.3882 | 4.4375 | 2.65 | 0.7241 |
| No Dynamic Planner | 0.55 | 0.6336 | 0.9 | 0.6125 | 0.8875 | 3.9419 | 4.4375 | 2.9125 | 0.7167 |
| No Targeted Replan | 0.55 | 0.6705 | 0.85 | 0.6125 | 0.8875 | 3.6993 | 4.1375 | 2.35 | 0.7141 |
| No Critic | 0.475 | 0.5384 | 0.85 | 0.0125 | 0.8875 | 3.0549 | 3.6125 | 1.825 | 0.5527 |
| No Verifier | 0.475 | 0.571 | 0.9 | 0.0875 | 0.8875 | 3.3967 | 3.9 | 2.1125 | 0.5842 |
| No Parallel Scheduler | 0.55 | 0.6705 | 0.9 | 0.6125 | 0.8875 | 3.4634 | 4.65 | 2.65 | 0.7241 |
| Shallow Loops | 0.2625 | 0.6687 | 0.85 | 0.6 | 0.85 | 2.2052 | 4.425 | 2.6375 | 0.6462 |

## Ablation deltas vs baseline (A)

| Variant | delta overall |
|---|---|
| B | 0.0974 |
| C | -0.0424 |
| D | -0.3550 |
| E | -0.2208 |
| F | 0.0360 |
| G | -0.1997 |

## CI Hard Gates

| Gate | Result | Value | Threshold |
|---|---:|---:|---:|
| planner_routing_recall | PASS | 1.0 | >=0.90 |
| planner_unnecessary_rate | PASS | 0.0 | <0.20 |
| replan_correct_target_rate | PASS | 1.0 | >=0.90 |
| replan_recovery_advantage | PASS | 0.3333 | >0 |
| parallel_speedup | PASS | 1.6008 | >1.20 |
| sequential_width | PASS | 1.0 | ==1 |
| deep_loop_advantage | PASS | 0.25 | >=0.10 |
| critic_fp | PASS | 0.0 | <No-Critic |
| verifier_fp | PASS | 0.0 | <No-Verifier |
| graph_cycles | PASS | 0.0 | ==0 |
| graph_self_cycles | PASS | 0.0 | ==0 |
| fix_stale_inputs | PASS | 0.0 | ==0 |
| fix_after_verifier | PASS | 0.0 | ==0 |
| planner_flag_effect | PASS | 1.0 | ==1 |
| replan_flag_effect | PASS | 1.0 | ==1 |
| parallel_flag_effect | PASS | 1.0 | ==1 |
| deep_loop_flag_effect | PASS | 1.0 | ==1 |

**Overall Hard Gate: PASS**

## Evolution Attribution (plan §12)

- Full (baseline): SHALLOW_LOOP_FAILURE=23
- No Dynamic Planner: SHALLOW_LOOP_FAILURE=23
- No Targeted Replan: REPLAN_INSUFFICIENT=12, SHALLOW_LOOP_FAILURE=23
- No Critic: PLANNER_OVER_ROUTING=6, REPLAN_INSUFFICIENT=12, SHALLOW_LOOP_FAILURE=23
- No Verifier: PLANNER_OVER_ROUTING=6, SHALLOW_LOOP_FAILURE=23
- No Parallel Scheduler: SHALLOW_LOOP_FAILURE=23
- Shallow Loops: CRITIC_FALSE_REJECT=12, REPLAN_INSUFFICIENT=12, SHALLOW_LOOP_FAILURE=47
