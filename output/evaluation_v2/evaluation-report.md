# EvoReview-Agent — Evaluation Harness V2 Report

> Dataset SHA-256: 88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115 · 100 cases · 10 repos

> 100 受控 PR Diff Benchmark（40 风险 / 60 干净，Validation 80 / Holdout 20）。评分器固定（Path + CWE + line ±2，one-to-one），本轮只改变被评测系统。

## Overall Comparison

| Metric | Single Agent | Legacy Multi-Agent | Current Harness | Self-Evolved |
|---|---|---|---|---|
| Precision | 83.3% | 82.5% | 0.0% | 100.0% |
| Recall | 62.5% | 82.5% | 0.0% | 35.0% |
| F1 | 71.4% | 82.5% | 0.0% | 51.8% |
| High-risk Recall | 84.2% | 94.7% | 0.0% | 15.8% |
| Clean Accuracy | 91.7% | 91.7% | 100.0% | 100.0% |
| Execution Success | 100.0% | 100.0% | 100.0% | 100.0% |
| Critical Misses | 3 | 1 | 19 | 16 |
| P95 Latency (ms) | — | 0.6 | 324.9 | 247.3 |

## Generalization (Holdout, unseen repositories)

| Metric | Stable Validation | Evolved Validation | Stable Holdout | Evolved Holdout |
|---|---|---|---|---|
| Precision | 0.0% | 100.0% | 0.0% | 100.0% |
| Recall | 0.0% | 40.6% | 0.0% | 12.5% |
| F1 | 0.0% | 57.8% | 0.0% | 22.2% |
| High-risk Recall | 0.0% | 12.5% | 0.0% | 33.3% |
| Clean Accuracy | 100.0% | 100.0% | 100.0% | 100.0% |
| Critical Misses | 16 | 14 | 3 | 2 |

### Holdout Deltas

- **Holdout F1**: 0.0% → 22.2%（+22.2 pp）
- **Holdout High-risk Recall**: 0.0% → 33.3%（+33.3 pp）
- **Critical Misses**: 3 → 2

## Harness Engineering

| Metric | Current Harness | Evolved |
|---|---|---|
| Execution Success Rate | 100.0% | 100.0% |
| Recovery Success Rate | — | — |
| Multi-Agent DAG Executed | 100.0% | 100.0% |
| Specialist Agents Active | code-quality | evolved-review@1, code-quality |
| Collaboration Rounds (avg) | 0.40 | 0.40 |
| Collaboration Messages (avg) | 5.00 | 8.08 |
| Avg Agent Tool-Steps | 0.00 | 0.00 |
| Avg Tool Calls | 0.00 | 0.00 |
| Tool Denials | 0 | 0 |
| P50 Latency (ms) | 224.87 | 204.30 |
| P95 Latency (ms) | 324.85 | 247.28 |
| Trace Coverage | 100.0% | 100.0% |
| Replay Snapshot Coverage | 100.0% | 100.0% |

## Evolution Safety

| Gate | Result |
|---:|---|
| Validation Improvement | **PASS** · candidate F1 0.5777 >= stable F1 0.0000 |
| High-risk Non-regression | **PASS** · candidate HR-Recall 0.1250 >= stable 0.0000 |
| Critical Miss Non-regression | **PASS** · candidate critical hits 2 >= stable 0 |
| Clean Accuracy Non-regression | **PASS** · candidate clean 1.0000 >= stable 1.0000 - 0.02 |
| Catastrophic Forgetting | **PASS** · no high-risk recall drop beyond threshold |
| Runtime Safety | **PASS** · candidate execution success 1.0000 >= 0.99 |

**Overall Safety Gate: PASS**

## Canary / Rollback

- **Canary promotion**: PASS
  - stages advanced: 5 · exposure count: 0
- **Auto rollback**: PASS
  - traffic share after rollback: 0.0 · previous-good restored: True
