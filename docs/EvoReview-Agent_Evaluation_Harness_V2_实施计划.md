# EvoReview-Agent Evaluation Harness V2 实施计划

> 目标：在**不改变原 100 个受控 PR Diff 数据集与核心匹配口径**的前提下，将当前 EvoReview-Agent 的完整 Agent Harness 与 Self-Evolution 闭环接入 Evaluation Harness，得到一组**可复现、可横向比较、可用于简历表述**的评测结果。

---

## 1. 最终目标

本轮评测不再只比较：

```text
Single-Agent Baseline
        ↓
Legacy Multi-Agent
```

而是扩展为：

```text
A. Single-Agent Baseline
        ↓
B. Legacy Multi-Agent
        ↓
C. Current Full Harness
        ↓
D. Self-Evolved Harness Candidate
```

最终需要得到两类结论：

### 1.1 Agent 能力结果

证明当前完整 Harness 相比原始 Single-Agent / Legacy Multi-Agent 在 Code Review 任务上的效果变化：

- Precision
- Recall
- F1
- High-risk Recall
- Critical Misses
- Clean PR Accuracy
- Severity Accuracy
- Execution Success Rate

### 1.2 Harness / Self-Evolution 结果

证明项目不是单纯“多 Agent 调用”，而具有 Runtime Governance 与安全自进化能力：

- Validation / Holdout F1
- Holdout High-risk Recall
- Evolution Candidate ΔF1
- Catastrophic Forgetting Gate
- Generalization Gate
- Recovery Success Rate
- Tool Policy Violation / Denial
- Average Agent Steps
- Average Tool Calls
- P50 / P95 Latency
- Candidate Promotion / Rollback Result

---

# 2. 当前基线冻结

## 2.1 冻结原始数据集

继续使用当前：

```text
evaluation_data/pr_diff_100.jsonl
```

数据规模保持：

- 100 个 PR Diff
- 40 个风险样本
- 60 个干净样本
- 10 个 repository
- Validation：8 repositories / 80 PR
- Holdout：2 repositories / 20 PR
- 数据来源：synthetic-controlled

评测开始前必须校验数据集 SHA-256：

```text
88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115
```

### 验收要求

- 不重新生成 Dataset
- 不修改 Expected Findings
- 不修改 Validation/Holdout repository 划分
- 不修改 CWE 标签
- 不修改 Severity 标签

否则新版结果不能与原 71.4% / 82.5% 直接比较。

---

## 2.2 冻结匹配规则

继续使用现有 Evaluation Harness 的一对一匹配逻辑：

```text
Path 相同
+
CWE 相同
+
预测行位于 GT 区间
或距离 GT ≤ 2 行
+
Maximum-cardinality One-to-One Matching
```

保持：

- 一个 Prediction 最多匹配一个 GT
- 一个 GT 最多匹配一个 Prediction
- 重复预测计为 FP
- 未匹配 GT 计为 FN
- Clean PR 只有完全无 Finding 才算正确

### 原则

**本轮只改变被评测系统，不改变评分器。**

---

# 3. Phase 0：建立 Evaluation V2 目录和配置

建议新增：

```text
evoagent/
├── evaluation_harness.py
├── evaluation_benchmark.py
└── evaluation_v2/
    ├── __init__.py
    ├── adapters.py
    ├── metrics.py
    ├── experiment.py
    ├── evolution_protocol.py
    └── report.py

scripts/
└── run_e2e_evaluation_v2.py

output/
└── evaluation_v2/
    ├── baseline.json
    ├── legacy_multi_agent.json
    ├── current_harness.json
    ├── evolved_candidate.json
    ├── comparison.json
    ├── evaluation-report.md
    └── case-results.jsonl
```

### 目的

不要直接大改旧 `evaluation_harness.py`。

旧版评测继续保留作为历史证据，新版通过 Adapter 接入当前完整 Runtime。

---

# 4. Phase 1：实现统一 Reviewer Adapter

当前旧评测要求：

```python
reviewer.review(diff, parsed) -> List[Finding]
```

而当前完整系统入口是：

```text
ReviewHarness.run(
    task_id,
    repository,
    pull_request,
    diff,
    tenant_id
)
```

因此需要新增统一 Adapter。

## 4.1 新增 `evaluation_v2/adapters.py`

至少实现四个 Adapter：

### A. `SingleAgentEvaluationAdapter`

内部调用：

```text
LocalRuleReviewer
```

作用：

复现原 Single-Agent Baseline。

---

### B. `LegacyMultiAgentEvaluationAdapter`

内部调用：

```text
MultiAgentCoordinator(
    LocalRuleReviewer,
    ContextRuleReviewer
)
```

作用：

复现原 82.5% Legacy Multi-Agent 结果。

---

### C. `CurrentHarnessEvaluationAdapter`

必须真正走：

```text
PR Diff
 ↓
Risk Profile
 ↓
Execution Policy Resolution
 ↓
ReviewExecutionContext
 ↓
ReviewHarness
 ↓
AgentRuntime
 ↓
MultiAgentCoordinator
 ↓
Critic / Evidence / Verifier / Arbiter
 ↓
ReviewReport
 ↓
List[Finding]
```

核心要求：

不能在 Adapter 中直接调用：

```python
candidate_reviewer().review(...)
```

否则仍然只是旧版 Multi-Agent。

---

### D. `EvolvedHarnessEvaluationAdapter`

与 `CurrentHarnessEvaluationAdapter` 使用相同 Harness。

唯一差异是：

```text
Stable Runtime / Prompt / Skill Policy
```

替换为：

```text
Frozen Evolved Candidate
```

这样才能保证：

> Stable vs Evolved 的差异来自演进 Candidate，而不是评测路径不同。

---

## 4.2 Adapter 输出统一结构

建议：

```python
EvaluationExecutionResult(
    findings=[],
    success=True,
    latency_ms=...,
    agent_steps=...,
    tool_calls=...,
    recovery_attempts=...,
    recovery_successes=...,
    policy_denials=...,
    circuit_breaker_trips=...,
    policy_id=...,
    policy_version=...,
    deployment_lane=...,
    error=None,
)
```

现有 `EndToEndEvaluationHarness` 只关心 Findings。

V2 需要额外保留 Harness Telemetry。

---

# 5. Phase 2：把当前完整 ReviewHarness 接入评测

## 5.1 每个 PR 使用独立 Task ID

格式建议：

```text
eval-v2-{system}-{case_id}
```

例如：

```text
eval-v2-current-pr-0001
eval-v2-evolved-pr-0001
```

避免不同实验组共享 Checkpoint。

---

## 5.2 使用隔离的 Evaluation Store

不要让评测污染正常开发数据库。

建议默认：

```text
output/evaluation_v2/evaluation.db
```

每次正式评测：

```text
rm old evaluation.db
create fresh evaluation store
```

但每一组系统的结果文件永久保存。

---

## 5.3 每个 Case 必须创建完整 Execution Context

至少记录：

```text
task_id
repository
pull_request
tenant_id = evaluation-v2
risk_level
policy_id
policy_version
deployment_id
candidate/baseline lane
dataset_split
case_id
```

Evaluation 中禁止丢失 Policy Attribution。

---

## 5.4 Runtime Policy 必须真实生效

Current Harness 评测时必须确认：

- max_steps 来自 ExecutionPolicy
- timeout 来自 ExecutionPolicy
- retries 来自 ExecutionPolicy
- Tool Budget 来自 ExecutionPolicy
- Risk Level 会影响执行配置

建议在 Case Result 中写入：

```json
{
  "resolved_policy": "...",
  "max_steps": 8,
  "timeout_seconds": 120,
  "tool_budget": 12
}
```

防止“代码接入了 Policy，但实际跑的仍是默认参数”。

---

# 6. Phase 3：记录 Harness 指标

在原 Detection Metrics 外新增：

## 6.1 Runtime Metrics

每个 PR 记录：

```text
execution_success
latency_ms
agent_steps
runtime_retries
recovery_attempts
recovery_successes
checkpoint_count
resume_count
```

汇总：

```text
Execution Success Rate
Recovery Success Rate
Average Agent Steps
P50 Latency
P95 Latency
```

---

## 6.2 Tool Governance Metrics

每个 PR 记录：

```text
tool_calls_total
tool_calls_allowed
tool_calls_denied
schema_validation_failures
budget_denials
circuit_breaker_trips
sandbox_requests
approval_requests
timeouts
side_effect_blocks
```

最终至少输出：

```text
Avg Tool Calls / PR
Tool Denial Rate
Timeout Rate
Circuit Breaker Trips
Side-effect Block Count
```

---

## 6.3 Replay / Trace Metrics

记录：

```text
decision_trace_created
replay_snapshot_created
trace_event_count
```

目标：

```text
Decision Trace Coverage = 100%
Replay Snapshot Coverage = 100%
```

只要任务成功，就必须存在对应 Trace 与 Replay Snapshot。

---

# 7. Phase 4：先跑“不进化”的三组基准

首先不要运行任何 Self-Evolution。

顺序：

```text
Run 1: Single Agent
Run 2: Legacy Multi-Agent
Run 3: Current Full Harness
```

全部使用同一个：

```text
evaluation_data/pr_diff_100.jsonl
```

## 7.1 预期验证

Single-Agent 应近似复现：

```text
Precision 83.3%
Recall 62.5%
F1 71.4%
High-risk Recall 84.2%
Clean Accuracy 91.7%
```

Legacy Multi-Agent 应近似复现：

```text
Precision 82.5%
Recall 82.5%
F1 82.5%
High-risk Recall 94.7%
Clean Accuracy 91.7%
```

如果不能复现，**先停止 V2 对比**。

因为说明：

- Dataset 发生变化
- Reviewer 发生变化
- Matcher 发生变化
- Case 排序/过滤发生变化

之一存在问题。

---

## 7.2 Current Harness 才是第一组“新结果”

得到：

```text
Current Harness
Precision = ?
Recall = ?
F1 = ?
High-risk Recall = ?
Clean Accuracy = ?
Critical Misses = ?
Execution Success = ?
P95 Latency = ?
```

这组数据回答：

> 当前完整 Harness 在没有 Self-Evolution 的情况下，相比 Legacy Multi-Agent 到底提升还是退化？

注意：

Harness 价值不等于 F1 必须提升。

即使：

```text
F1 ≈ 82.5%
```

但新增：

```text
Policy Governance
Checkpoint / Resume
Recovery
Trace
Replay
Tool Safety
```

依然是工程能力提升。

---

# 8. Phase 5：设计严格的 Self-Evolution 实验

这是本计划最重要的部分。

禁止使用 Holdout 参与 Candidate 生成。

---

## 8.1 数据隔离

固定：

```text
Validation
80 PR
8 repositories
32 Risk
48 Clean
```

用于：

```text
Stable Harness Evaluation
↓
Failure / False Negative Mining
↓
Experience
↓
Hypothesis
↓
Prompt / Skill / Runtime Policy Candidate
↓
Replay Evaluation
↓
Safety Gate
↓
Candidate Selection
```

Holdout：

```text
20 PR
2 unseen repositories
8 Risk
12 Clean
```

在 Candidate Freeze 前：

**任何 Self-Evolution 模块都禁止读取 Holdout Ground Truth。**

---

# 9. Phase 6：运行 Validation Evolution Loop

## 9.1 Step 1：Stable Harness 跑 Validation

只运行：

```text
split == validation
```

保存：

```text
validation_stable.json
```

收集：

- TP
- FP
- FN
- high-risk FN
- clean false positives
- execution errors
- recovery events
- tool failures

---

## 9.2 Step 2：从 Validation 失败案例生成 Experience

允许进入 Experience Pool 的主要事件：

```text
False Negative
High-risk False Negative
Confirmed False Positive
Execution Failure
Tool Governance Failure
Repeated Recovery Pattern
```

Experience 必须包含：

```text
case_id
repository
failure_type
expected_cwe
predicted_findings
decision_trace_id
replay_snapshot_id
policy_id
evidence
```

---

## 9.3 Step 3：Experience → Hypothesis

调用现有 Reflection / Hypothesis 流程。

例如：

```text
Observation:
SEC-OPEN-REDIRECT repeatedly missed

Hypothesis:
Security specialist lacks redirect-target validation capability

Candidate Direction:
Add declarative rule skill / specialist prompt capability
```

禁止写：

```text
pr-0091 的第 12 行应该报 CWE-601
```

Candidate 必须学习**可泛化规则**，不能记住 Case ID 或固定行号。

---

## 9.4 Step 4：生成 Candidate

优先级建议：

### 第一优先：Skill Candidate

原因：

- 可解释
- 可审计
- Replay 可复现
- 简历表达更强

### 第二优先：Runtime Policy Candidate

例如：

```text
High Risk:
increase security specialist
increase evidence budget
enable verifier
increase max steps
```

### 第三优先：Prompt Candidate

Prompt 演进更容易受 LLM 随机性影响。

---

## 9.5 Step 5：Counterfactual Replay

在 Validation Replay Dataset 上执行：

```text
Stable Candidate
vs
Evolved Candidate
```

必须保持：

```text
same cases
same matching
same truth
same runtime limits where policy itself is not the variable
```

---

# 10. Phase 7：Safety Gate

Candidate 不允许因为 Validation F1 提升就直接通过。

至少建立以下 Hard Gates。

## 10.1 Detection Gate

建议：

```text
candidate_validation_f1 >= stable_validation_f1
candidate_high_risk_recall >= stable_high_risk_recall
candidate_critical_misses <= stable_critical_misses
```

---

## 10.2 Clean Regression Gate

```text
candidate_clean_accuracy >= stable_clean_accuracy - 0.02
```

不能为了召回率疯狂误报。

---

## 10.3 Catastrophic Forgetting Gate

对 Stable 原本正确识别的核心风险重新检查。

要求：

```text
critical_recall_drop == 0
high_risk_recall_drop <= configured threshold
key_rule_recall_drop <= configured threshold
```

---

## 10.4 Runtime Safety Gate

要求：

```text
execution_success_rate >= 0.99
timeout_rate <= threshold
policy_violation_count == 0
unapproved_side_effect_count == 0
```

---

## 10.5 Cost / Latency Guard

建议不作为第一版 Hard Gate，但记录：

```text
candidate_p95_latency / baseline_p95_latency
candidate_avg_tool_calls / baseline_avg_tool_calls
```

防止：

```text
F1 +1%
Cost +500%
```

这种 Candidate 被误判为优秀演进。

---

# 11. Phase 8：冻结 Candidate

Safety Gate 通过后生成：

```text
FrozenCandidateManifest
```

至少包含：

```json
{
  "candidate_id": "...",
  "parent_policy_id": "...",
  "prompt_versions": {},
  "skill_versions": {},
  "runtime_policy_version": "...",
  "validation_dataset_sha256": "...",
  "created_from_split": "validation",
  "gate_result": "PASS"
}
```

此时 Candidate **不可继续修改**。

然后才能进入 Holdout。

---

# 12. Phase 9：Holdout Blind Evaluation

只加载：

```text
split == holdout
```

运行：

```text
Stable Full Harness
vs
Frozen Evolved Candidate
```

禁止：

- 自动收集 Holdout FN 后再次修改 Candidate
- 自动触发 Evolution Controller
- Candidate 自动升级
- 根据 Holdout Ground Truth 调阈值

建议运行模式：

```text
EVOLUTION_CONTROLLER_ENABLED=false
CANDIDATE_FROZEN=true
```

---

## 12.1 Holdout 最关键指标

输出：

```text
Holdout Precision
Holdout Recall
Holdout F1
Holdout High-risk Recall
Holdout Critical Misses
Holdout Clean Accuracy
Holdout Execution Success
```

其中：

### 最有简历价值的是

```text
Holdout F1 Δ
Holdout High-risk Recall Δ
Critical Misses Δ
```

因为这是未参与 Evolution 的 repository。

---

# 13. Phase 10：Canary / Rollback 验证

100 PR Dataset 本质是离线 Benchmark。

Canary 的目标不是再提升 F1，而是验证：

> Candidate 能不能安全上线以及失败后能不能自动回滚。

使用 Frozen Candidate 创建：

```text
DRAFT
 ↓
REPLAY_PASSED
 ↓
SHADOW
 ↓
CANARY 5%
 ↓
10%
 ↓
25%
 ↓
50%
 ↓
100%
 ↓
PROMOTED
```

---

## 13.1 正向实验

Candidate 满足 Safety Gate。

验证：

```text
DRAFT → REPLAY_PASSED → SHADOW → CANARY → PROMOTED
```

记录：

```text
promotion_success = true
stage_count
exposure_count
lane_attribution_accuracy
```

---

## 13.2 负向实验

人为创建一个 Known-Bad Candidate，例如：

```text
High-risk Recall 明显下降
```

验证：

```text
Hard Safety Failure
↓
ROLLBACK
↓
traffic_share = 0
↓
previous-good restored
```

最终得到：

```text
Auto Rollback Success Rate
```

对于固定的确定性测试，应达到：

```text
100%
```

---

# 14. Phase 11：最终报告格式

生成：

```text
output/evaluation_v2/evaluation-report.md
```

---

## 14.1 Overall Comparison

| Metric | Single Agent | Legacy Multi-Agent | Current Harness | Self-Evolved |
|---|---:|---:|---:|---:|
| Precision | 83.3% | 82.5% | ? | ? |
| Recall | 62.5% | 82.5% | ? | ? |
| F1 | 71.4% | 82.5% | ? | ? |
| High-risk Recall | 84.2% | 94.7% | ? | ? |
| Critical Misses | ? | ? | ? | ? |
| Clean Accuracy | 91.7% | 91.7% | ? | ? |
| Execution Success | 100% | 100% | ? | ? |
| P95 Latency | — | — | ? | ? |

---

## 14.2 Generalization

| Metric | Stable Validation | Evolved Validation | Stable Holdout | Evolved Holdout |
|---|---:|---:|---:|---:|
| Precision | ? | ? | ? | ? |
| Recall | ? | ? | ? | ? |
| F1 | ? | ? | ? | ? |
| High-risk Recall | ? | ? | ? | ? |
| Clean Accuracy | ? | ? | ? | ? |
| Critical Misses | ? | ? | ? | ? |

---

## 14.3 Harness Engineering

| Metric | Current Harness | Evolved |
|---|---:|---:|
| Execution Success Rate | ? | ? |
| Recovery Success Rate | ? | ? |
| Avg Agent Steps | ? | ? |
| Avg Tool Calls | ? | ? |
| Tool Denial Rate | ? | ? |
| P50 Latency | ? | ? |
| P95 Latency | ? | ? |
| Trace Coverage | ? | ? |
| Replay Snapshot Coverage | ? | ? |

---

## 14.4 Evolution Safety

| Gate | Result |
|---|---|
| Validation Improvement | PASS / FAIL |
| High-risk Non-regression | PASS / FAIL |
| Critical Miss Non-regression | PASS / FAIL |
| Clean Accuracy Non-regression | PASS / FAIL |
| Catastrophic Forgetting | PASS / FAIL |
| Generalization | PASS / FAIL |
| Runtime Safety | PASS / FAIL |
| Canary | PASS / FAIL |
| Auto Rollback | PASS / FAIL |

---

# 15. Phase 12：JSON 报告 Schema

最终不仅生成 Markdown。

必须保留机器可读：

```text
evaluation-report.json
```

建议：

```json
{
  "schema_version": 2,
  "dataset": {},
  "systems": {
    "single_agent": {},
    "legacy_multi_agent": {},
    "current_harness": {},
    "evolved_candidate": {}
  },
  "evolution": {
    "validation": {},
    "candidate_manifest": {},
    "safety_gates": {},
    "holdout": {}
  },
  "deployment": {
    "canary": {},
    "rollback": {}
  }
}
```

这样以后 CI 可以直接解析。

---

# 16. Phase 13：CI 接入

在：

```text
.github/workflows/ci.yml
```

新增：

```text
evaluation-v2-regression
```

建议 CI 默认只执行**确定性、无外部 LLM 成本**的版本。

流程：

```text
checkout
↓
install
↓
verify dataset SHA
↓
run Single-Agent
↓
run Legacy Multi-Agent
↓
run Current Harness deterministic profile
↓
run Evolved Frozen Candidate
↓
evaluate gates
↓
upload artifacts
```

上传：

```text
output/evaluation_v2/*.json
output/evaluation_v2/*.md
```

---

# 17. 推荐开发顺序

不要一次性把所有代码都改完。

## Milestone 1：Baseline Reproduction

完成：

- V2 Runner
- Dataset fingerprint
- Single-Agent Adapter
- Legacy Adapter

验收：

```text
71.4%
82.5%
```

可以稳定复现。

---

## Milestone 2：Current Full Harness

完成：

- CurrentHarnessEvaluationAdapter
- ReviewHarness 接入
- Policy Context
- Trace / Replay
- Runtime metrics

验收：

```text
100 PR 可跑完
输出 Current Harness Detection + Runtime 指标
```

此时已经得到第一批新版结果。

---

## Milestone 3：Validation Evolution

完成：

```text
Validation Stable
↓
Experience
↓
Hypothesis
↓
Candidate
↓
Replay
↓
Safety Gate
```

验收：

生成：

```text
candidate_manifest.json
```

---

## Milestone 4：Holdout Blind Test

完成：

```text
Stable vs Frozen Candidate
```

验收：

生成：

```text
holdout-comparison.json
```

这是整个 Self-Evolution Evaluation 最核心的结果。

---

## Milestone 5：Canary / Rollback

完成：

- good candidate promote
- known-bad candidate rollback

验收：

```text
Promotion PASS
Rollback PASS
```

---

## Milestone 6：CI + Final Report

完成：

```text
evaluation-report.json
evaluation-report.md
case-results.jsonl
candidate-manifest.json
```

并由 CI 上传 Artifact。

---

# 18. 建议新增/修改文件清单

## 新增

```text
evoagent/evaluation_v2/__init__.py
evoagent/evaluation_v2/adapters.py
evoagent/evaluation_v2/experiment.py
evoagent/evaluation_v2/metrics.py
evoagent/evaluation_v2/evolution_protocol.py
evoagent/evaluation_v2/report.py

scripts/run_e2e_evaluation_v2.py

tests/evaluation_v2/test_adapters.py
tests/evaluation_v2/test_metrics.py
tests/evaluation_v2/test_baseline_reproduction.py
tests/evaluation_v2/test_harness_execution.py
tests/evaluation_v2/test_holdout_isolation.py
tests/evaluation_v2/test_candidate_freeze.py
tests/evaluation_v2/test_canary_rollback.py
```

## 尽量少改

```text
evoagent/evaluation_harness.py
evoagent/evaluation_benchmark.py
```

原因：

它们是旧版结果可复现性的基础。

---

# 19. 建议 CLI

最终做到：

## 19.1 复现旧结果

```bash
python scripts/run_e2e_evaluation_v2.py \
  --stage baseline \
  --dataset evaluation_data/pr_diff_100.jsonl
```

---

## 19.2 当前 Full Harness

```bash
python scripts/run_e2e_evaluation_v2.py \
  --stage current \
  --dataset evaluation_data/pr_diff_100.jsonl
```

---

## 19.3 Validation Evolution

```bash
python scripts/run_e2e_evaluation_v2.py \
  --stage evolve \
  --split validation
```

---

## 19.4 Holdout Blind Test

```bash
python scripts/run_e2e_evaluation_v2.py \
  --stage holdout \
  --candidate output/evaluation_v2/candidate-manifest.json
```

---

## 19.5 全流程

```bash
python scripts/run_e2e_evaluation_v2.py \
  --stage all \
  --reuse-dataset
```

---

# 20. 防止“评测造数”的关键规则

开发过程中必须遵守。

## Rule 1

不能为了得到更漂亮结果修改：

```text
40 Risk / 60 Clean
```

比例。

## Rule 2

不能在看到 Holdout 结果后继续针对 Holdout 修改 Candidate。

## Rule 3

不能改变 Line Tolerance 来提高 TP。

固定：

```text
±2 lines
```

## Rule 4

不能因为某个 CWE 检不出来就从 Benchmark 删除。

## Rule 5

不能把 Synthetic Benchmark 描述成：

```text
100 个真实 GitHub PR
```

必须写：

```text
100 个受控 PR Diff Benchmark
```

## Rule 6

F1 的“提升百分比”和“百分点提升”要区分。

例如：

```text
71.4% → 82.5%
```

是：

```text
+11.1 percentage points
```

相对提升约：

```text
+15.5%
```

---

# 21. 什么情况下可以形成最终简历指标

至少满足：

```text
✓ Dataset SHA 固定
✓ Baseline 71.4% 可复现
✓ Legacy 82.5% 可复现
✓ Current Harness 100 Case 全量运行
✓ Validation 与 Holdout 完全隔离
✓ Candidate 在 Validation 产生
✓ Candidate Freeze 后才跑 Holdout
✓ Hard Safety Gate 通过
✓ Canary / Rollback E2E 测试通过
✓ CI 可复现最终报告
```

然后再从真实结果中选择简历指标。

---

# 22. 最终简历表述生成规则

不要提前写一个想要的数字。

等 V2 跑完后，根据实际结果生成。

理想结构：

```text
建设基于 100 个受控 PR Diff 的端到端 Evaluation Harness，
按 repository 划分 Validation/Holdout，并以 Path + CWE + Line Range
进行一对一匹配；完整 Agent Harness 在固定 Benchmark 上实现
F1 XX.X%、高风险召回率 XX.X%，并通过 Validation→Evolution→
Frozen Candidate→Holdout 的盲测协议验证自进化策略，
Holdout F1 提升 X.X pp、高风险漏报由 X 降至 X。
```

Harness 如果数据值得展示，还可补：

```text
任务执行成功率 XX.X%，Recovery 成功率 XX.X%，
Decision Trace / Replay Snapshot 覆盖率 100%；
候选 Runtime Policy 通过 Safety Gate 后按
5%→10%→25%→50%→100% Canary 发布，
Known-Bad Candidate 可自动回滚至 Previous-Good Policy。
```

---

# 23. 第一轮最应该完成的任务

如果只看“尽快得到新版评测数字”，优先完成：

```text
1. 冻结 pr_diff_100.jsonl
2. 实现 Evaluation Adapter
3. 复现 71.4 / 82.5
4. CurrentHarnessEvaluationAdapter
5. 跑 Current Full Harness
6. 输出 Current Harness Detection + Runtime Metrics
```

做到第 6 步，就已经能够回答：

> “当前项目版本在原来 100 PR Benchmark 上表现如何？”

然后再进入：

```text
7. Validation Evolution
8. Candidate Freeze
9. Holdout Blind Evaluation
10. Canary / Rollback
```

这部分用来回答：

> “项目的 Self-Evolution 是否真的有效且安全？”

---

# 24. 最终完成标准

本计划完成后，项目应具备以下证据链：

```text
固定 Benchmark
      ↓
Baseline Reproduction
      ↓
Full Harness Evaluation
      ↓
Validation Failure Mining
      ↓
Experience
      ↓
Hypothesis
      ↓
Candidate
      ↓
Counterfactual Replay
      ↓
Safety Gate
      ↓
Frozen Candidate
      ↓
Unseen Holdout
      ↓
Canary
      ↓
Promote / Rollback
      ↓
CI Regression
```

这条链路完整跑通后，EvoReview-Agent 的项目定位就不再只是：

```text
“一个多 Agent Code Review 项目”
```

而可以有证据地描述为：

```text
“具备可治理 Agent Harness、可复现 Evaluation Harness
以及安全闭环 Self-Evolution 的 Agent Runtime 系统。”
```
