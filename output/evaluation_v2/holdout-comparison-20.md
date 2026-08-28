# Holdout-20: Stable vs Self-Evolved (isolated)

**20 cases · 10 risk / 10 clean · 2 unseen repositories**

| System | F1 | High-risk Recall | Clean Acc | Critical Misses |
|---|---:|---:|---:|---:|
| Stable (Current Harness) | 54.5% | 66.7% | 100.0% | 1 |
| Self-Evolved | 54.5% | 66.7% | 100.0% | 1 |
| **Δ** | +0.0 pp | +0.0 pp | +0.0 pp | +0 |

**new catches: 0 · regressions: 0**

| Case | Repo | Expected | Stable TP/FP/FN | Evolved TP/FP/FN | ΔTP |
|---|---|---:|---|---:|---:|
| pr-0081 | acme/service-09 | 1 | 1/0/0 | 1/0/0 | +0 |
| pr-0082 | acme/service-09 | 1 | 0/0/1 | 0/0/1 | +0 |
| pr-0083 | acme/service-09 | 1 | 0/0/1 | 0/0/1 | +0 |
| pr-0084 | acme/service-09 | 1 | 0/0/1 | 0/0/1 | +0 |
| pr-0085 | acme/service-09 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0086 | acme/service-09 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0087 | acme/service-09 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0088 | acme/service-09 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0089 | acme/service-09 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0090 | acme/service-09 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0091 | acme/service-10 | 1 | 1/0/0 | 1/0/0 | +0 |
| pr-0092 | acme/service-10 | 1 | 1/0/0 | 1/0/0 | +0 |
| pr-0093 | acme/service-10 | 1 | 0/0/1 | 0/0/1 | +0 |
| pr-0094 | acme/service-10 | 1 | 0/0/1 | 0/0/1 | +0 |
| pr-0095 | acme/service-10 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0096 | acme/service-10 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0097 | acme/service-10 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0098 | acme/service-10 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0099 | acme/service-10 | 0 | 0/0/0 | 0/0/0 | +0 |
| pr-0100 | acme/service-10 | 0 | 0/0/0 | 0/0/0 | +0 |

## Conclusion

Self-Evolution yields **+0.0 pp on the blind holdout set**.
No new catches (0), no regressions (0).
The evolved declarative skill was mined only from Validation false negatives; the two
holdout repositories carry vulnerability families absent from that set, so the substring
rules match nothing new. This is an **honest, deliberate non-tuning result** -- per plan
Rule 1/2 the candidate is frozen from validation *before* any holdout measurement, and
thresholds are never fit to holdout. The harness is complete and trustworthy; the
*generalization* of the candidate to unseen families is not yet demonstrated.