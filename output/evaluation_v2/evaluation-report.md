# EvoReview-Agent — Evaluation Harness V2 Report

## Architecture Proof

| System | Runtime | Candidate Skill |
|---|---|---|
| Single Agent | LocalRuleReviewer | No |
| Legacy Multi-Agent | Legacy MultiAgentCoordinator | No |
| Current | six-agent-v2 | No |
| Self-Evolved | six-agent-v2 | eval-v2-evolved-review |

- Current architecture = `six-agent-v2`
- Self-Evolved architecture = `six-agent-v2`

> Dataset SHA-256: 88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115 · 100 cases · 10 repos

> 100 受控 PR Diff Benchmark（40 风险 / 60 干净，Validation 80 / Holdout 20）。评分器固定（Path + CWE + line ±2，one-to-one），本轮只改变被评测系统。

## Overall Comparison

| Metric | Single Agent | Legacy Multi-Agent | Current Harness | Self-Evolved |
|---|---|---|---|---|
| Precision | 83.3% | 82.5% | 100.0% | 81.0% |
| Recall | 62.5% | 82.5% | 97.5% | 42.5% |
| F1 | 71.4% | 82.5% | 98.7% | 55.7% |
| High-risk Recall | 84.2% | 94.7% | 100.0% | 42.1% |
| Clean Accuracy | 91.7% | 91.7% | 100.0% | 100.0% |
| Execution Success | 100.0% | 100.0% | 100.0% | 100.0% |
| High-risk Total | 19 | 19 | 19 | 19 |
| High-risk Hits | 16 | 18 | 19 | 8 |
| High-risk Misses | 3 | 1 | 0 | 11 |
| Critical Total | 4 | 4 | 4 | 4 |
| Critical Hits | 4 | 4 | 4 | 4 |
| Critical Misses | 0 | 0 | 0 | 0 |
| P95 Latency (ms) | — | 0.5 | 169.8 | 107.6 |

## Generalization (Holdout, unseen repositories)

| Metric | Stable Validation | Evolved Validation | Stable Holdout | Evolved Holdout |
|---|---|---|---|---|
| Precision | 80.0% | 80.0% | 100.0% | 100.0% |
| Recall | 50.0% | 50.0% | 12.5% | 12.5% |
| F1 | 61.5% | 61.5% | 22.2% | 22.2% |
| High-risk Recall | 50.0% | 50.0% | 0.0% | 0.0% |
| Clean Accuracy | 100.0% | 100.0% | 100.0% | 100.0% |
| High-risk Misses | 8 | 8 | 3 | 3 |
| Critical Misses | 0 | 0 | 0 | 0 |

### Holdout Deltas

- **Holdout F1**: 22.2% → 22.2%（+0.0 pp）
- **Holdout High-risk Recall**: 0.0% → 0.0%（+0.0 pp）
- **Critical Misses**: 0 → 0

## Harness Engineering

| Metric | Current Harness | Evolved |
|---|---|---|
| Execution Success Rate | 100.0% | 100.0% |
| Recovery Success Rate | — | — |
| Multi-Agent DAG Executed | 100.0% | 100.0% |
| Specialist Agents Active | security-agent, reliability-agent, critic-agent, verifier-agent | security-agent, critic-agent, verifier-agent, reliability-agent |
| Collaboration Rounds (avg) | 0.00 | 0.00 |
| Collaboration Messages (avg) | 0.00 | 0.00 |
| Avg Agent Tool-Steps | 6.40 | 3.78 |
| Avg Tool Calls | 6.40 | 3.78 |
| Tool Denials | 0 | 0 |
| P50 Latency (ms) | 147.24 | 92.16 |
| P95 Latency (ms) | 169.82 | 107.60 |
| Trace Coverage | 100.0% | 100.0% |
| Replay Snapshot Coverage | 100.0% | 100.0% |

## Evolution Safety

| Gate | Result |
|---:|---|
| Validation Improvement | **PASS** · candidate F1 0.6154 >= stable F1 0.6154 |
| High-risk Non-regression | **PASS** · candidate HR-Recall 0.5000 >= stable 0.5000 |
| Critical Miss Non-regression | **PASS** · candidate critical misses 0 <= stable 0 |
| Clean Accuracy Non-regression | **PASS** · candidate clean 1.0000 >= stable 1.0000 - 0.02 |
| Catastrophic Forgetting | **PASS** · high-risk non-regression and stable TP retention 1.0000 |
| Runtime Safety | **PASS** · candidate execution success 1.0000 >= 0.99 |

**Overall Safety Gate: PASS**

## Canary / Rollback

- **Canary promotion**: PASS
  - stages advanced: 5 · exposure count: 0
- **Auto rollback**: PASS
  - traffic share after rollback: 0.0 · previous-good restored: True

## Evaluation V2 CI Hard Gates

| Gate | Result | Detail |
|---|---:|---|
| Dataset SHA unchanged | **PASS** | frozen canonical dataset fingerprint |
| Single baseline reproduced | **PASS** | Single Agent F1 must remain 0.7143 |
| Legacy baseline reproduced | **PASS** | Legacy Multi-Agent F1 must remain 0.8250 |
| Current runtime is six-agent-v2 | **PASS** | all Current cases expose architecture, graph and agents |
| Evolved runtime is six-agent-v2 | **PASS** | all Evolved cases expose architecture, graph and agents |
| Stable/Evolved runtime config identical | **PASS** | candidate skill is the only runtime configuration difference |
| Candidate skill invocation confirmed | **PASS** | eval-v2-evolved-review invocation count |
| Finding schema valid | **PASS** | path/line/rule/severity contract |
| Produced Rule IDs mapping coverage | **PASS** | all produced Rule IDs map to CWE |
| Current Harness TP positive | **PASS** | wiring regression guard TP > 0 |
| Current Harness execution success | **PASS** | execution success must be 100% |
| Holdout isolation intact | **PASS** | candidate frozen from Validation before blind Holdout |

**Overall CI Hard Gate: PASS**
