# EvoReview-Agent — Production Demo


## Demo A — Risk-aware Harness
| Resource | Low-risk | High-risk |
|---|---:|---:|
| Enabled agents | 1 (reliability) | 3 (security,reliability,semantic) |
| Budget max_steps | 3 | 10 |
| Budget max_tool_calls | 5 | 25 |
| Verification steps | (none) | critic,evidence,verifier,sandbox |
| Tool perms | 6 | 6 |
=> PASS: high-risk engages 3 agents + 10 steps + 25 tool calls (vs 1 / 3 / 5 for low-risk).

## Demo B — Tool Governance
- side-effect `run_tests` -> DENY: approval declined for run_tests
=> PASS: side-effect tool is denied without approval.
- blocking `slow_job` -> TIMEOUT exceeded 0.2s
- metrics tool_timeouts_total: 0 -> 1.0
- recovery action=RETRY_WITH_BACKOFF
=> PASS: blocking tool timeout is caught and routed to recovery.

## Demo C — Self-Evolution (baseline -> missed -> candidate -> promote)
- baseline replay metrics -> candidate deltas:
  finding_f1 0.000 -> 0.000 ; high_risk_recall 0.000 -> 0.000 ; approved=True
- deployment state=PROMOTED ; live review policy=baseline-high-raise_max_steps lane=candidate
- lineage chain: EXPERIENCE -> HYPOTHESIS -> CANDIDATE -> EVALUATION -> DEPLOYMENT

## Demo D — Auto Rollback (bad candidate -> previous-good restored)
- bad candidate state=ROLLED_BACK (hard-safety gate failed -> auto rollback)
- after rollback + restart, live review policy=baseline-high-raise_max_steps (previous-good=baseline-high-raise_max_steps)

## Demo E — Restart Recovery (canary -> restart -> same lane)
- state CANARY -> CANARY ; lane baseline -> baseline

## Demo Summary
| Demo | Scenario | Result |
|---|---|---|
| A | Risk-aware Harness | PASS |
| B | Tool Governance | PASS |
| C | Self-Evolution | PASS |
| D | Auto Rollback | PASS |
| E | Restart Recovery | PASS |

Total elapsed: 2.9s
