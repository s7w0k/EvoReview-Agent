# EvoReview-Agent — Evaluation Harness V2 Report

> Dataset SHA-256: 88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115 · 100 cases · 10 repos

> 100 受控 PR Diff Benchmark（40 风险 / 60 干净，Validation 80 / Holdout 20）。评分器固定（Path + CWE + line ±2，one-to-one），本轮只改变被评测系统。

## Overall Comparison

| Metric | Single Agent | Legacy Multi-Agent | Current Harness | Self-Evolved |
|---|---|---|---|---|
| Precision | 83.3% | 82.5% | 83.3% | 87.5% |
| Recall | 62.5% | 82.5% | 62.5% | 87.5% |
| F1 | 71.4% | 82.5% | 71.4% | 87.5% |
| High-risk Recall | 84.2% | 94.7% | 84.2% | 94.7% |
| Clean Accuracy | 91.7% | 91.7% | 91.7% | 91.7% |
| Execution Success | 100.0% | 100.0% | 100.0% | 100.0% |
| Critical Misses | 3 | 1 | 3 | 1 |
| P95 Latency (ms) | — | 0.5 | 267.1 | 269.1 |

## Generalization (Holdout, unseen repositories)

| Metric | Stable Validation | Evolved Validation | Stable Holdout | Evolved Holdout |
|---|---|---|---|---|
| Precision | 81.5% | 86.5% | 100.0% | 100.0% |
| Recall | 68.8% | 100.0% | 37.5% | 37.5% |
| F1 | 74.6% | 92.8% | 54.5% | 54.5% |
| High-risk Recall | 87.5% | 100.0% | 66.7% | 66.7% |
| Clean Accuracy | 89.6% | 89.6% | 100.0% | 100.0% |
| Critical Misses | 2 | 0 | 1 | 1 |

### Holdout Deltas

- **Holdout F1**: 54.5% → 54.5%（+0.0 pp）
- **Holdout High-risk Recall**: 66.7% → 66.7%（+0.0 pp）
- **Critical Misses**: 1 → 1

## Harness Engineering

| Metric | Current Harness | Evolved |
|---|---|---|
| Execution Success Rate | 100.0% | 100.0% |
| Recovery Success Rate | — | — |
| Multi-Agent DAG Executed | 100.0% | 100.0% |
| Specialist Agents Active | security-agent, reliability-agent, code-quality | evolved-review@1, security-agent, reliability-agent, code-quality |
| Collaboration Rounds (avg) | 0.40 | 0.40 |
| Collaboration Messages (avg) | 11.64 | 14.68 |
| Avg Agent Tool-Steps | 0.00 | 0.00 |
| Avg Tool Calls | 0.00 | 0.00 |
| Tool Denials | 0 | 0 |
| P50 Latency (ms) | 205.41 | 210.14 |
| P95 Latency (ms) | 267.11 | 269.09 |
| Trace Coverage | 100.0% | 100.0% |
| Replay Snapshot Coverage | 100.0% | 100.0% |

## Evolution Safety

| Gate | Result |
|---:|---|
| Validation Improvement | **PASS** · candidate F1 0.9276 >= stable F1 0.7458 |
| High-risk Non-regression | **PASS** · candidate HR-Recall 1.0000 >= stable 0.8750 |
| Critical Miss Non-regression | **PASS** · candidate critical hits 16 >= stable 14 |
| Clean Accuracy Non-regression | **PASS** · candidate clean 0.8958 >= stable 0.8958 - 0.02 |
| Catastrophic Forgetting | **PASS** · no high-risk recall drop beyond threshold |
| Runtime Safety | **PASS** · candidate execution success 1.0000 >= 0.99 |

**Overall Safety Gate: PASS**

## Canary / Rollback

- **Canary promotion**: PASS
  - stages advanced: 5 · exposure count: 0
- **Auto rollback**: PASS
  - traffic share after rollback: 0.0 · previous-good restored: True
