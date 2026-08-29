# EvoReview-Agent：Evaluation V2 接入 Six-Agent-v2 与可信检测指标重构实施计划

> 目标：修复冻结 100 条 PR Diff Benchmark 与当前 Six-Agent Runtime 的接线偏差，使 `Current Harness` 与 `Self-Evolved` 都真实运行在 `six-agent-v2` 上，并重新获得可信的 Precision / Recall / F1 / High-risk Recall / Critical Misses。
>
> 核心原则：**先修评测链路，再谈 Recall 优化。**

---

# 1. 当前问题

当前 100-case Benchmark 出现：

```text
Single Agent        TP=25 FP=5 FN=15
Legacy Multi-Agent  TP=33 FP=7 FN=7
Current Harness     TP=0  FP=0 FN=40
Self-Evolved        TP=14 FP=0 FN=26
```

其中最异常的是：

```text
Current Harness Recall = 0%
```

当前 Evaluation V2 的 `CurrentHarnessEvaluationAdapter` 仍走：

```text
svc._build_coordinator(...)
```

这是旧 `MultiAgentCoordinator` 入口。

而当前真实六 Agent Runtime 的生产入口是：

```text
ReviewService._build_leader(...)
        ↓
agent_architecture == six-agent-v2
        ↓
build_six_agent_reviewer(...)
        ↓
CoordinatorAgent(mode="v2")
        ↓
Security / Reliability / Critic / Verifier / Fix
```

同时，当前 Self-Evolved 仍通过：

```text
extra_reviewers=[DeclarativeSkillReviewer]
```

注入候选能力；但 `SixAgentReviewer` 内部直接构造自己的 Specialist Agents，因此这套注入方式不再适合作为 six-agent-v2 的正式 Self-Evolved 实验。

---

# 2. 最终目标实验设计

修复后必须严格比较：

```text
A. Single Agent
   LocalRuleReviewer

B. Legacy Multi-Agent
   LocalRuleReviewer + ContextRuleReviewer

C. Current Six-Agent Runtime
   six-agent-v2
   Stable Skills

D. Self-Evolved Six-Agent Runtime
   six-agent-v2
   Stable Skills + Frozen Evolved Skill
```

控制变量：

```text
Runtime 相同
Harness 相同
Planner 相同
Critic 相同
Verifier 相同
Matcher 相同
Dataset 相同
Split 相同
Policy 相同
Feature Flags 相同

唯一变量：
是否加载 Frozen Evolved Skill
```

---

# 3. Phase 0：冻结修改前结果

保存：

```text
output/evaluation_v2_before_fix/
```

至少包含：

```text
baseline.json
legacy_multi_agent.json
current_harness.json
evolved_candidate.json
evaluation-report.json
evaluation-report.md
holdout-comparison.json
candidate-manifest.json
```

同时固定：

```text
evaluation_data/pr_diff_100.jsonl
Dataset SHA
```

本轮禁止修改冻结 Dataset。

---

# 4. Phase 1：Evaluation Settings 显式固定 six-agent-v2

修改：

```text
evoagent/evaluation_v2/adapters.py
```

在 `build_evaluation_service()` 中显式加入：

```python
agent_architecture="six-agent-v2"
```

不要依赖默认值、环境变量或开发机配置。

新增测试：

```text
tests/evaluation_v2/test_six_agent_architecture.py
```

断言：

```python
assert svc.settings.agent_architecture == "six-agent-v2"
```

---

# 5. Phase 2：CurrentHarnessEvaluationAdapter 改走 `_build_leader`

当前：

```python
coordinator = svc._build_coordinator(
    self._lineup(svc),
    execution_policy=context.execution_policy,
)
harness = svc._build_harness(
    coordinator,
    context.execution_policy,
    context,
)
```

改为：

```python
leader = svc._build_leader(
    self._lineup(svc),
    execution_policy=context.execution_policy,
)
harness = svc._build_harness(
    leader,
    context.execution_policy,
    context,
)
```

目标执行链：

```text
Evaluation V2
→ ReviewService
→ _build_leader
→ SixAgentReviewer
→ CoordinatorAgent(v2)
→ Specialist Agent Loops
```

---

# 6. Phase 3：SixAgentReviewer 暴露 Runtime Summary

旧 Adapter 依赖：

```text
coordinator.collaboration_summary(task_id)
```

但 Six-Agent Runtime 应使用自己的统一 Runtime Metadata。

在：

```text
evoagent/loop_agents/reviewer.py
```

增加：

```python
self.last_runtime_artifact = {}
```

`review()` 中：

```python
outcome = coordinator.run(task)
artifact = outcome.get("artifact") or {}
self.last_runtime_artifact = artifact
```

增加：

```python
def runtime_summary(self) -> dict:
    return dict(self.last_runtime_artifact or {})
```

Evaluation Adapter 统一读取：

```python
runtime_summary = (
    leader.runtime_summary()
    if hasattr(leader, "runtime_summary")
    else {}
)
```

---

# 7. Phase 4：扩展 EvaluationExecutionResult

新增字段：

```python
architecture: str = ""
called_agents: List[str] = field(default_factory=list)
graph_revision: int = 0
graph_shapes: List[dict] = field(default_factory=list)
loop_steps_by_agent: Dict[str, int] = field(default_factory=dict)
parallel_batches: List[dict] = field(default_factory=list)
replan_count: int = 0
replan_targets: List[str] = field(default_factory=list)
feature_flags: Dict[str, Any] = field(default_factory=dict)
skill_invocations: Dict[str, int] = field(default_factory=dict)
```

并写入 `to_dict()`。

---

# 8. Phase 5：增加 Runtime Wiring Hard Gate

Current Harness 正式评测必须断言：

```text
architecture == six-agent-v2
graph_shapes 非空
called_agents 非空
```

对于 risk case 至少应调用：

```text
security-agent
或
reliability-agent
```

如果再次出现：

```text
Current Harness = 0 / 0 / 40
```

但 Runtime Telemetry 不符合要求：

```text
CI 直接 FAIL
```

而不是继续生成性能报告。

---

# 9. Phase 6：修复 Self-Evolved Skill 注入位置

Self-Evolved 不再通过“普通 Reviewer lineup”注入。

正确位置：

```text
Frozen Candidate
      ↓
Security Agent Tool/Skill Layer
      ↓
security_rule_scan
```

原则：

```text
不新增 Agent
不修改 Planner
不修改 Runtime Graph
不替换 Stable Skill
只扩展 Specialist Capability
```

---

# 10. Phase 7：ExpertContext 支持多个 Security Reviewers

修改：

```text
evoagent/loop_agents/tools.py
```

当前：

```python
self._security = SecurityRuleReviewer()
```

改为：

```python
self._security_reviewers = list(
    security_reviewers or [SecurityRuleReviewer()]
)
```

构造函数增加：

```python
security_reviewers=None
```

Stable：

```text
[SecurityRuleReviewer()]
```

Evolved：

```text
[
  SecurityRuleReviewer(),
  FrozenDeclarativeSkillReviewer(...)
]
```

---

# 11. Phase 8：security_rule_scan 合并多个 Skill 输出

改造：

```python
def security_rule_scan():
    findings = []
    for reviewer in ctx._security_reviewers:
        findings.extend(
            reviewer.review(ctx.diff, ctx.parsed)
        )

    findings = deduplicate_findings(findings)

    return {
        "findings": findings_to_dicts(findings),
        "count": len(findings),
    }
```

初始去重 Key：

```text
rule_id + path + line
```

---

# 12. Phase 9：SixAgentReviewer 支持 Skill 配置注入

推荐增加：

```python
tool_context_config
```

例如：

```python
SixAgentReviewer(
    ...,
    tool_context_config={
        "security_reviewers": [...]
    }
)
```

Delegator / LoopAgentHost 创建 Specialist Tool Context 时将其传入。

不要把 Frozen Candidate 写死在 `SecurityAgent` 类中。

---

# 13. Phase 10：Stable 与 Evolved 共用同一 Leader Factory

新增：

```python
def build_evaluation_leader(
    svc,
    execution_policy,
    evolved_skill=None,
):
```

Stable：

```python
build_evaluation_leader(
    svc,
    policy,
    evolved_skill=None,
)
```

Evolved：

```python
build_evaluation_leader(
    svc,
    policy,
    evolved_skill=frozen_skill,
)
```

除 `evolved_skill` 外，其余配置必须一致。

---

# 14. Phase 11：证明 Candidate 真的被调用

增加：

```text
skill_invocations
```

例如：

```json
{
  "security-rule@1": 1,
  "evolved-review@candidate-x": 1
}
```

Self-Evolved 正式 case 必须断言：

```python
assert frozen_candidate_id in result.skill_invocations
```

如果 Candidate 已加载但未调用：

```text
Evaluation FAIL
```

---

# 15. Phase 12：修复 Critical Misses 定义

当前若使用：

```text
high_total - high_hits
```

实际是：

```text
High-risk Misses
```

应同时输出：

```text
high_risk_total
high_risk_hits
high_risk_misses

critical_total
critical_hits
critical_misses
```

真正：

```python
critical_misses = critical_total - critical_hits
```

Critical 只能统计：

```text
severity == critical
```

的 Gold FN。

---

# 16. Phase 13：Matcher 本轮冻结，不修改

保持：

```text
same path
AND same CWE
AND predicted line within Gold ±2
AND one-to-one matching
```

本轮只修 Runtime，不修改 Scorer。

这样才能确定性能变化来自：

```text
评测对象改变
```

而不是：

```text
评分规则改变
```

---

# 17. Phase 14：Finding Schema Contract Test

新增：

```text
tests/evaluation_v2/test_six_agent_finding_schema.py
```

断言 Six-Agent Finding：

```text
path 非空
line > 0
rule_id 非空
severity 有效
rule_id 可映射 CWE
```

这是非常重要的排错项。

如果 Six-Agent 实际发现了漏洞，但 `rule_id/path/line` 与 Matcher Schema 不兼容：

```text
系统会被错误统计为 FN
```

---

# 18. Phase 15：检查 Rule ID → CWE Mapping

确保：

```text
SEC-SQL-CONCAT → CWE-89
SEC-SUBPROCESS-SHELL → CWE-78
...
```

所有 Six-Agent 能输出的 Rule ID 必须有 Matcher 映射。

新增：

```text
tests/evaluation_v2/test_rule_cwe_mapping.py
```

正式要求：

```text
Produced Rule IDs Mapping Coverage = 100%
```

---

# 19. Phase 16：先跑 5-case Smoke Test

不要直接重跑 100 条。

挑：

```text
2 Security risk
1 Reliability risk
1 clean
1 High/Critical
```

逐 case 输出：

```text
Gold
Predicted Findings
Matched TP
Unmatched Prediction FP
Unmatched Gold FN
Called Agents
Graph Shape
Tool Calls
Rule→CWE Mapping
```

人工核验：

```text
Runtime 正确
Matcher 正确
Schema 正确
```

---

# 20. Phase 17：再跑 20-case Diagnostic

从 Validation 取 20 条。

输出：

```text
TP / FP / FN
Precision / Recall / F1
Per-CWE Recall
Called Agents
Rule hit rate
Verifier rejection rate
```

同时给每个 FN 打原因标签：

```text
NO_AGENT_ROUTED
RULE_NOT_TRIGGERED
FINDING_DROPPED_BY_CRITIC
FINDING_REJECTED_BY_VERIFIER
MATCHER_SCHEMA_MISMATCH
CWE_MAPPING_MISMATCH
LINE_MISMATCH
```

---

# 21. Phase 18：20-case 正常后重跑 100-case

执行：

```bash
python scripts/run_e2e_evaluation_v2.py --stage all
```

正式比较：

```text
Single Agent
Legacy Multi-Agent
Current Six-Agent-v2
Self-Evolved Six-Agent-v2
```

---

# 22. Phase 19：正式报告增加 Architecture Proof

加入：

| System | Runtime | Candidate Skill |
|---|---|---|
| Single | LocalRuleReviewer | No |
| Legacy | Legacy MultiAgentCoordinator | No |
| Current | Six-Agent-v2 | No |
| Self-Evolved | Six-Agent-v2 | Frozen Candidate |

并打印：

```text
Current architecture = six-agent-v2
Self-Evolved architecture = six-agent-v2
```

---

# 23. Phase 20：正式 Detection Metrics

最终计算：

```text
Precision = TP / (TP + FP)

Recall = TP / (TP + FN)

F1 = 2PR / (P + R)

High-risk Recall =
High-risk TP / High-risk Gold

Critical Misses =
Unmatched Gold where severity == critical
```

建议额外输出：

```text
Clean PR Accuracy
Per-CWE Recall
Macro Recall by vulnerability type
```

---

# 24. Phase 21：增加 FN Root-Cause Report

生成：

```text
fn-analysis.json
```

结构：

```json
{
  "case_id": "...",
  "cwe": "CWE-89",
  "severity": "high",
  "reason": "VERIFIER_FALSE_REJECT",
  "agents_called": [],
  "tools_called": [],
  "candidate_skill_hit": false
}
```

---

# 25. Phase 22：按 FN 原因决定下一步优化

如果主要是：

```text
NO_AGENT_ROUTED
→ Planner / Risk Profiler

RULE_NOT_TRIGGERED
→ Security / Reliability Skill Coverage

MATCHER_SCHEMA_MISMATCH
→ Evaluation Schema

CWE_MAPPING_MISMATCH
→ Rule-to-CWE Mapping

VERIFIER_FALSE_REJECT
→ Verifier Policy

Candidate Validation-only hit
→ Evolution Generalization / Rule Abstraction
```

---

# 26. Phase 23：Self-Evolved 不允许覆盖 Stable 能力

必须是：

```text
Stable Skill
+
Candidate Skill
```

不是：

```text
Candidate 替换 Stable Skill
```

增加 forgetting gate：

```text
Stable TP 保留率
```

例如：

```text
forgetting_rate <= threshold
```

---

# 27. Phase 24：重新跑 Validation / Holdout

流程保持：

```text
Validation
→ mine misses
→ synthesize candidate
→ safety gate
→ freeze
→ blind Holdout
```

Holdout 不得参与 Candidate 修改。

正式输出：

```text
Stable Holdout P/R/F1
Evolved Holdout P/R/F1
Delta
High-risk Recall Delta
Critical Misses Delta
```

---

# 28. Phase 25：Evaluation V2 CI Hard Gates

至少加入：

```text
Dataset SHA unchanged

Single baseline reproduced
Legacy baseline reproduced

Current runtime == six-agent-v2
Evolved runtime == six-agent-v2

Stable/Evolved runtime config identical

Candidate skill invocation confirmed

Finding schema valid

Produced Rule IDs → CWE mapping coverage = 100%

Current Harness TP > 0

Current Harness execution success = 100%

Holdout isolation intact
```

其中：

```text
Current Harness TP > 0
```

只是 Wiring Regression Gate，用来防止再次出现明显错误的：

```text
0 / 0 / 40
```

不是最终性能门槛。

---

# 29. Phase 26：保留旧基线作为 Dataset/Matcher Anchor

不能删除：

```text
Single Agent F1 ≈ 71.4%
Legacy Multi-Agent F1 ≈ 82.5%
```

如果修改 Evaluation 后这两列明显变化：

```text
说明 Dataset / Matcher / baseline path 被意外改动
```

CI 应失败。

---

# 30. 最终实施顺序

严格按：

```text
1. Freeze 修改前结果
2. Evaluation Settings 固定 six-agent-v2
3. Adapter _build_coordinator → _build_leader
4. SixAgentReviewer 暴露 runtime_summary
5. EvaluationExecutionResult 扩展 Runtime Telemetry
6. Runtime Wiring Hard Gate
7. ExpertContext 支持 composed security reviewers
8. Frozen Candidate 注入 Security Tool/Skill 层
9. Stable/Evolved 统一 Leader Factory
10. Skill Invocation Proof
11. Critical Misses 定义修正
12. Finding Schema Contract
13. Rule ID → CWE Mapping Contract
14. 5-case Smoke
15. 20-case Diagnostic
16. 100-case Full Run
17. Validation/Holdout 重跑
18. FN Root-Cause Analysis
19. CI Hard Gates
20. 最终报告冻结
```

---

# 31. 开发规则

每一步：

```text
Red
→ 先写失败测试

Green
→ 实现功能

Freeze
→ 保存结果 / commit
```

不要一口气修改完再补测试。

---

# 32. 最终验收 Checklist

## Runtime

- [ ] Current 使用 six-agent-v2
- [ ] Self-Evolved 使用 six-agent-v2
- [ ] Current 不再使用旧 `_build_coordinator` 作为 Leader
- [ ] Graph / Agents / Loop telemetry 可见

## Candidate

- [ ] Candidate 进入 Security Agent Tool/Skill 层
- [ ] Candidate invocation 可观测
- [ ] Candidate 不覆盖 Stable Skill
- [ ] Stable 与 Evolved 唯一差异是 Frozen Candidate

## Matcher

- [ ] Frozen matcher 未改
- [ ] Finding Schema 合法
- [ ] Rule ID → CWE mapping 完整
- [ ] one-to-one matching 保持不变

## Metrics

- [ ] Precision
- [ ] Recall
- [ ] F1
- [ ] High-risk Recall
- [ ] High-risk Misses
- [ ] Critical Misses
- [ ] Clean Accuracy

## Isolation

- [ ] Validation-only synthesis
- [ ] Candidate freeze before Holdout
- [ ] Holdout blind
- [ ] Dataset SHA unchanged

## CI

- [ ] Single baseline reproduced
- [ ] Legacy baseline reproduced
- [ ] Current TP > 0
- [ ] Runtime == six-agent-v2
- [ ] Candidate invocation confirmed
- [ ] Schema / CWE Mapping contracts pass

---

# 33. 修完后如何判断 Recall 是否真的有问题

如果修复后：

```text
Current Six-Agent Recall 仍明显偏低
```

此时才能下结论：

```text
检测能力本身不足
```

下一阶段才进入：

```text
Per-CWE FN Analysis
→ Security Rule Coverage
→ Semantic / AST / Dataflow
→ Verifier False-Reject Analysis
→ Evolution Generalization
```

如果修复后 Recall 明显恢复，则说明此前主要问题是：

```text
Evaluation Wiring / Schema / Mapping
```

而不是 Multi-Agent 本身。

---

# 34. 最终原则

最终必须证明：

```text
Frozen 100 PR Diff
       ↓
Real Six-Agent-v2 Runtime
       ↓
Real Findings
       ↓
Frozen One-to-One Matcher
       ↓
TP / FP / FN
       ↓
Precision / Recall / F1
```

而 Self-Evolved 必须是：

```text
Same Six-Agent-v2 Runtime
+
Frozen Candidate Skill
```

只有这样，后续得到的 Recall / F1 才值得用于判断 EvoReview-Agent 的真实检测能力。
