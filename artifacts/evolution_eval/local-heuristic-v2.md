# Evolution Regression Benchmark — local-heuristic-v2

Deterministic, reproducible evaluation of an evolved policy generation (`local-heuristic-v2`) against a baseline (`local-heuristic-v1-security`) on the fixed EvoReview benchmark dataset.

## Hard Gate

| Gate | Baseline | Candidate | Pass |
|---|---|---|---|
| Critical Misses ≤ | 1 | 0 | ✅ |
| High-risk Recall ≥ | 0.667 | 1.000 | ✅ |
| Recall ≥ | 0.143 | 0.286 | ✅ |
| F1 ≥ | 0.250 | 0.421 | ✅ |

**Decision: PASS — candidate safe to promote**

## Metrics (Baseline / Candidate / Delta)

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| F1 | 0.25 | 0.4211 | +0.1711 (68%) |
| Recall | 0.1429 | 0.2857 | +0.1428 (100%) |
| Precision | 1.0 | 0.8 | -0.2 (-20%) |
| High-risk Recall | 0.6667 | 1.0 | +0.3333 (50%) |
| Critical Misses | 1 | 0 | -1 (-100%) |
| False Positives | 0 | 1 | +1 |
| True Positives | 2 | 4 | +2 (100%) |
| False Negatives | 12 | 10 | -2 (-17%) |
| Task Success Rate | 0.375 | 0.4375 | +0.0625 (17%) |
| Tool Calls | 0 | 0 | 0 |
| Agent Steps | 16 | 16 | 0 (0%) |
| Latency (s) | 0.0002 | 0.0003 | +0.0001 (50%) |
| Cost (USD) | 0.0 | 0.0 | 0.0 |
| Recovery Rate | None | None | n/a |
| Policy Violations | 0 | 0 | 0 |

## Case Detail (Candidate)

| Case | Cat | Risk | Exp | Pred | TP | FP | FN | Full | Task |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| SEC-001 | security | high | 1 | 1 | 1 | 0 | 0 | ✅ | ✅ |
| SEC-002 | security | high | 1 | 1 | 1 | 0 | 0 | ✅ | ✅ |
| SEC-003 | security | high | 2 | 1 | 1 | 0 | 1 | — | ✅ |
| SEC-004 | security | medium | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| SEC-005 | security | medium | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| REL-001 | reliability | medium | 2 | 1 | 1 | 0 | 1 | — | ✅ |
| REL-002 | reliability | low | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| REL-003 | reliability | low | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| REL-004 | reliability | low | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| COR-001 | correctness | medium | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| COR-002 | correctness | low | 0 | 0 | 0 | 0 | 0 | — | ✅ |
| COR-003 | correctness | medium | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| COR-004 | correctness | medium | 1 | 0 | 0 | 0 | 1 | — | ❌ |
| REG-001 | regression | low | 0 | 0 | 0 | 0 | 0 | — | ✅ |
| REG-002 | regression | low | 0 | 0 | 0 | 0 | 0 | — | ✅ |
| REG-003 | regression | low | 0 | 1 | 0 | 1 | 0 | — | ❌ |
