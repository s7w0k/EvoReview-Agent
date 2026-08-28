# A2A Baseline Snapshot

Freeze of Evaluation V2 metrics + repository state taken **before** the A2A
remote-transport work, per plan §3.2. A2A must not cause an unexplained change
to the detection results.

## Commit

```
SHA: f9d0add154f1eb124b461d503721fc0329936d5c
Msg: feat(eval-v2): prove multi-agent DAG execution + isolated Holdout-20 report
```

## Dataset (frozen)

```
source: evaluation_data/pr_diff_100.jsonl
sha256: 88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115
cases: 100  risk_cases: 40  clean_cases: 60  repositories: 10
```

## Evaluation V2 detection baseline (output/evaluation_v2/evaluation-report.json)

| System | F1 | High-risk Recall | Clean Accuracy | Execution Success |
|--------|----:|----:|-----:|--------:|
| single_agent (baseline) | 0.7143 | 0.8421 | 0.9167 | 1.0 |
| legacy_multi_agent | 0.8250 | 0.9474 | 0.9167 | 1.0 |
| current_harness | 0.7143 | 0.8421 | 0.9167 | 1.0 |
| evolved_candidate | 0.8750 | 0.9474 | 0.9167 | 1.0 |

## Acceptance

- Current Evaluation V2 report is saved (above path).
- Commit SHA recorded (above).
- CI gate: functional metrics must not regress after A2A transport work.