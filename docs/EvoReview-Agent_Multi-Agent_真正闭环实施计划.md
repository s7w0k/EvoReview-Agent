# EvoReview-Agent Multi-Agent 真正闭环实施计划

> 目标：在当前 6-Core-Agent + A2A + Dynamic Planner + Targeted Replan + Dynamic Graph + Evaluation V4 Scaffold 基础上，把剩余机制真正接通，并用真实运行结果证明，而不是仅证明“代码中存在这些模块”。

## 0. 当前缺口

当前已具备：

- 6 Core Agents：Coordinator / Security / Reliability / Critic / Verifier / Fix
- BaseLoopAgent / AgentLoop
- A2A InProcess / HTTP
- Semantic Dynamic Planner
- TaskGraph Validator + Fallback Planner
- Targeted Replan + Budget + Fingerprint
- GraphMutator
- Deep-loop helper
- Evaluation V4 scaffold

但尚未真正闭环的核心问题：

1. **Parallel Scheduler 只计算 batch，实际 delegate 仍同步串行。**
2. **Deep Local Loop 仍偏浅，strategy 多数只记录在 Artifact，没有真正控制下一步 Tool。**
3. **Evaluation V4 正式 Runner 仍是 synthetic placeholder，没有真实调用 SixAgentReviewer / Coordinator Runtime。**
4. Dynamic Graph 仍需修复潜在 dependency/self-cycle 问题，并让 Critic / Verifier / Fix 更彻底按结果条件化。
5. 缺少真实 Ablation + CI Hard Gate 来证明各组件价值。

本轮只做五个 Workstream：

```text
WS1  真并行 TaskGraph 执行
WS2  真 Deep Local Agent Loop
WS3  Dynamic Graph 正确性与条件化
WS4  Evaluation V4 接真实 Runtime
WS5  Real Ablation + CI Gate
```

---

# 1. WS1：真正并行 TaskGraph 执行

## 1.1 目标

从：

```text
next_batch() = [Security, Reliability]
Security → 等返回 → Reliability → 等返回
```

升级为：

```text
Coordinator
   ↓
ready batch
   ↓
┌──────────────┬──────────────┐
Security     Reliability     Other
RUNNING      RUNNING         RUNNING
└──────────────┴──────────────┘
          ↓
        fan-in
          ↓
dependent nodes READY
```

## 1.2 改造 Delegator

在 `evoagent/loop_agents/delegator.py` 增加：

```python
class DelegationHandle:
    task_id: str
    agent_id: str
    task_type: str
    correlation_id: str

class Delegator:
    def submit(...)
    def poll(...)
    def collect(...)
    def submit_batch(...)
    def collect_batch(...)
```

Coordinator 不再依赖阻塞式：

```python
delegate() -> artifact
```

## 1.3 InProcess 并发

第一版使用：

```python
concurrent.futures.ThreadPoolExecutor
```

即可，不必立即全面 async 化。

要求：

```text
两个 200ms Agent
顺序 ≈ 400ms
并发应明显低于 400ms
```

## 1.4 HTTP 并发

第一版可继续使用线程池并发调用现有同步 HTTP A2A Transport。

目标是：

```text
HTTP request wall-clock overlap
```

无需为了“异步”重写整个协议栈。

## 1.5 Coordinator 增加 governed batch tool

新增：

```text
delegate_agent_batch
```

输入：

```json
{
  "tasks": [
    {"node_id":"spec0","agent_id":"security-agent","task_type":"review.security"},
    {"node_id":"spec1","agent_id":"reliability-agent","task_type":"review.reliability"}
  ]
}
```

输出：

```json
{
  "completed": [],
  "failed": [],
  "latency_ms": 0
}
```

所有调用仍经过 GovernedToolRegistry。

## 1.6 并发预算真实生效

至少：

```text
max_parallel_agents
max_parallel_remote_calls
per_agent_concurrency
```

建议：

```text
Fix Agent concurrency = 1
Security = 2
Reliability = 2
```

## 1.7 批次失败语义

例如：

```text
Security success
Reliability timeout
```

要求：

```text
Security artifact 保留
Reliability 单独 Recovery / Fallback
```

不能整个 batch 一起失败。

## 1.8 WS1 测试

新增：

```text
tests/multi_agent/test_parallel_execution.py
```

必须覆盖：

- Security + Reliability 真重叠执行
- `max_parallel_agents=1` 退化为顺序
- `max_parallel_agents=2` 真并行
- 一个 timeout 不删除 sibling 成果
- critical branch failure fail-safe
- non-critical branch failure 可继续
- Fix `serial=True` 不并发修改同一 workspace

## 1.9 验收

只有满足以下条件才算完成：

```text
同一时刻存在 >=2 RUNNING Agent
P95 latency 明显低于 sequential baseline
artifact correlation 无错误
dependency 顺序无违反
```

---

# 2. WS2：真正 Deep Local Agent Loop

核心原则：

> 不以“执行了 AgentLoop”为完成标准，而以“前一 Observation 会改变下一 Tool 选择”为标准。

## 2.1 新增统一决策结构

建议增加：

```text
evoagent/loop_agents/decision.py
```

```python
@dataclass
class AgentDecision:
    action: str
    tool_name: str | None
    arguments: dict
    stop: bool
    reason_code: str
    confidence: float
```

每个 `agent_step()` 都执行：

```text
state
→ decide_next_action()
→ tool / final
```

## 2.2 Security Agent

至少支持：

```text
security_rule_scan
semantic_scan
trace_dataflow
inspect_context
```

Loop：

```text
rule_scan
 ↓
有风险？
 ├─ No → Final
 └─ Yes
      ↓
semantic_scan
      ↓
证据不足？
 ├─ Yes → trace_dataflow
 └─ No
      ↓
冲突？
 ├─ Yes → inspect_context
 └─ No
      ↓
Final
```

若没有真正 `trace_dataflow`，第一版实现 deterministic static approximation，不允许空壳 Tool。

## 2.3 Reliability Agent

至少：

```text
reliability_rule_scan
semantic_scan
inspect_execution_path
run_targeted_test
```

Loop：

```text
rule hit
→ semantic confirm
→ 是否需要 runtime evidence
→ targeted test
→ Final
```

## 2.4 Critic Agent

从：

```text
compare_peer_findings
→ _reflect()
→ Final
```

升级为：

```text
compare_peer_findings
 ↓
conflict?
→ find_conflict
 ↓
evidence weak?
→ check_evidence_match
 ↓
explanation weak?
→ check_explanation_quality
 ↓
fix unsafe?
→ check_fix_actionability
 ↓
Targeted ReplanRequest
 ↓
Final
```

Critic 仍不能直接调用 Specialist。

## 2.5 Verifier Agent

这是本轮最高优先级。

从：

```text
semantic_scan
→ strategy label
→ Final
```

升级为：

```text
inspect_evidence
 ↓
VerificationStrategySelector
 ↓
选择：
  verify_rule_signature
  semantic_verify
  run_targeted_test
  cross_check_finding
  inspect_context
 ↓
Observation
 ↓
confidence enough?
 ├─ No → choose another strategy
 └─ Yes → Final
```

每个 Finding 保存：

```python
{
    "finding_id": "...",
    "attempted_strategies": [],
    "remaining_strategies": [],
    "confidence": 0.0,
    "evidence": [],
    "verified": None
}
```

禁止重复选择同一策略导致 loop。

## 2.6 Fix Agent

补充失败分类与策略切换：

```text
deterministic patch
→ compile fail
→ AST patch

AST patch
→ test fail
→ model-assisted patch / abort
```

禁止同一种 patch 策略机械重复。

## 2.7 Stop Condition 真正进入 step

将现有：

```text
goal_satisfied
confidence_threshold_met
budget_exhausted
no_progress
tool_unavailable
policy_blocked
```

真正控制 `final_action()` / abort，而不是仅作为 metadata。

## 2.8 WS2 测试

每个 Agent 至少覆盖：

```text
1-step
2-step
3-step
Observation A → Tool X
Observation B → Tool Y
tool failure → fallback
no-progress
budget exhaustion
confidence early-stop
```

重点断言：

```text
test_verifier_strategy_changes_next_tool()
```

而不是只断言 Artifact 里出现了 `verification_strategy` 字段。

## 2.9 验收

至少能证明：

```text
同一个 Finding
不同 Observation
→ Verifier 下一 Tool 不同
```

---

# 3. WS3：Dynamic Graph 正确性与真正条件化

## 3.1 修复潜在 self-dependency

检查并删除任何类似：

```python
change_dependency(verifier, [verifier])
```

正确语义应是：

```text
Fix.dependencies = [Verifier]
```

## 3.2 Validator 增加 SELF_DEPENDENCY

显式验证：

```python
node.node_id not in node.dependencies
```

错误码：

```text
SELF_DEPENDENCY
```

## 3.3 Verifier 真正条件化

Verifier Trigger 改为：

```text
high-risk
OR critical
OR low confidence
OR conflicting findings
OR auto-fix candidate
OR policy requires verification
```

不要因为存在 Specialist 就默认必有 Verifier。

低风险强确定性 Finding 可以：

```text
Specialist → Arbiter
```

## 3.4 Critic 真正条件化

Critic Trigger：

```text
multi-agent disagreement
high-risk
low-confidence
novel finding
evidence gap
```

不要仅依赖 change_types 数量。

## 3.5 Fix 改为 runtime 插入

不要只因为 Planner 阶段看到：

```text
remediation=True
```

就预先创建 Fix。

改为：

```text
Verifier Artifact
 ↓
verified findings > 0
AND remediation allowed
 ↓
GraphMutator.add(Fix)
```

这样 Fix 才是真正的运行时条件节点。

## 3.6 必须出现至少四种真实 Graph Shape

```text
A. Reliability → Final

B. Security → Verifier → Final

C. Security + Reliability → Critic → Verifier → Final

D. Security → Critic → Security-Recheck → Verifier → Fix
```

## 3.7 WS3 测试

必须覆盖：

- self-dependency reject
- cycle reject
- clean PR 无 Fix
- 无 verified finding 无 Fix
- verified + remediation 才插入 Fix
- Critic 可跳过
- Verifier 可跳过
- completed node history 不被改写

---

# 4. WS4：Evaluation V4 接真实 Runtime

## 4.1 当前 synthetic runner 只能保留为 demo

正式 Evaluation 禁止继续使用：

```python
_synthetic_runner()
```

除非明确：

```text
--runner synthetic
```

默认必须：

```text
--runner runtime
```

## 4.2 新增 RuntimeScenarioRunner

新增：

```text
evoagent/evaluation_v4/runtime_runner.py
```

核心：

```python
class RuntimeScenarioRunner:
    def run(self, scenario, config):
        reviewer = build_six_agent_reviewer(...)
        findings = reviewer.review(...)
        return collect_real_runtime_metrics(...)
```

必须走：

```text
SixAgentReviewer
→ CoordinatorAgent
→ AgentLoop
→ A2A
→ real artifacts
```

## 4.3 FeatureFlags

新增统一：

```python
@dataclass
class MultiAgentFeatureFlags:
    planner: bool = True
    targeted_replan: bool = True
    critic: bool = True
    verifier: bool = True
    parallel_scheduler: bool = True
    deep_loop: bool = True
```

显式传入 Runtime，不要让正式 Ablation 靠散乱环境变量控制。

## 4.4 Ablation 开关必须真实改变行为

```text
planner=False
→ FallbackPlanner

targeted_replan=False
→ ignore/disable targeted graph mutation

critic=False
→ GraphPolicy 不插入 Critic

verifier=False
→ 在允许场景下禁用 Verifier

parallel_scheduler=False
→ max_parallel_agents=1

deep_loop=False
→ shallow stepper
```

不能只是 config 里出现 False。

## 4.5 Scenario Corpus

正式：

```text
evaluation_data/multi_agent_scenarios.jsonl
```

建议至少 60 cases：

```text
Planning        15
Replan          10
Collaboration   10
Deep Loop       10
Fix              5
Failure         10
```

## 4.6 每个 Scenario 必须有 Gold

至少：

```json
{
  "scenario_id": "replan-001",
  "kind": "missing-security-evidence",
  "diff": "...",
  "expected_agents": ["security-agent","critic-agent","verifier-agent"],
  "expected_replan": true,
  "expected_replan_target": "security-agent",
  "expected_findings": [],
  "expected_parallel_groups": [["security-agent","reliability-agent"]]
}
```

## 4.7 指标必须来自真实 Trace

禁止 Runner 自己写死：

```text
graph_revision
replan_count
loop_sizes
```

这些必须从：

```text
Coordinator artifact
Agent traces
A2A records
AgentLoop observations
```

真实读取。

## 4.8 指标

### Detection

```text
Precision
Recall
F1
High-risk Recall
Critical Misses
Clean Accuracy
```

### Planning

```text
Routing Precision
Routing Recall
Unnecessary Agent Invocation Rate
Graph Node Efficiency
```

### Replan

```text
Replan Trigger Precision
Correct Target Rate
Replan Success Rate
Recovery Rate
Repeated Replan Rate
```

### Parallel

```text
Parallel Batch Count
Mean Batch Width
P50/P95 Latency
Wall-clock Improvement
```

### Deep Loop

```text
Average Loop Steps
Tool Strategy Diversity
Useful Tool Call Ratio
No-progress Rate
Early-stop Rate
```

### Collaboration

```text
Critic Correction Rate
Verifier Correction Rate
Recovered FN
Suppressed FP
Conflict Resolution Rate
```

### Efficiency

```text
Tool Calls
A2A Calls
Tokens
Latency
```

---

# 5. WS5：Real Ablation + CI Hard Gate

## 5.1 正式 Ablation

跑 7 个 Variant：

| Variant | 配置 |
|---|---|
| A | Full |
| B | No Dynamic Planner |
| C | No Targeted Replan |
| D | No Critic |
| E | No Verifier |
| F | Sequential Scheduler |
| G | Shallow Local Loop |

## 5.2 不要求所有组件都提高 F1

合理结果可能是：

```text
Planner：
F1 不变
Unnecessary Agent Calls ↓

Parallel：
F1 不变
P95 latency ↓

Critic：
FP ↓
Latency ↑

Verifier：
FP ↓
Tool Calls ↑

Replan：
复杂场景 Recall ↑
Cost 小幅 ↑
```

目标是：

```text
每个组件作用可量化
```

## 5.3 CI 新增闭环 Job

新增：

```text
multi-agent-closure
```

包含：

```text
true-parallel-execution
deep-loop-observation-driven
targeted-replan-correctness
dynamic-graph-safety
runtime-eval-v4
ablation-smoke
```

## 5.4 Hard Gate：Parallel

两个 200ms 假 Agent：

```text
parallel elapsed < 合理阈值
```

阈值应留 CI 抖动余量，例如 <320ms，而不是要求精确 200ms。

## 5.5 Hard Gate：Deep Loop

必须断言：

```text
Observation X → Tool A
Observation Y → Tool B
```

## 5.6 Hard Gate：Replan

必须：

```text
target agent == gold
new node inserted
graph_revision increments
```

## 5.7 Hard Gate：Graph

必须：

```text
no cycle
no self-cycle
Fix only after verified finding
```

## 5.8 Hard Gate：Evaluation

正式 CLI 默认不得使用 synthetic runner。

建议：

```bash
python -m evoagent.evaluation_v4   --runner runtime   --corpus evaluation_data/multi_agent_scenarios.jsonl   --out evaluation_v4_real.md
```

synthetic 只能：

```bash
--runner synthetic
```

用于 demo/test。

---

# 6. 与 A2A 的最终边界

A2A 只负责：

```text
Task
Status
Message
Artifact
```

不要把每个 Agent 内部的每一次 Tool 调用都变成 A2A 消息。

局部 Loop：

```text
Security Agent
Plan
→ Tool
→ Observe
→ Replan
→ Final Artifact
```

只将最终/阶段 Artifact 返回 Coordinator。

---

# 7. 与 Harness 的边界保持不变

本轮不能破坏：

```text
Tool Policy
Budget
Circuit Breaker
Cancellation
Timeout
Trace
Replay
Arbiter
Evolution Gate
```

原则仍是：

> Agent decides what to do next; Harness decides what it is allowed to do.

---

# 8. Self-Evolution 最后再接

完成真实 Evaluation 后再扩展 failure attribution：

```text
PLANNER_MISS
OVER_ROUTING
WRONG_REPLAN_TARGET
UNNECESSARY_CRITIC
VERIFIER_FALSE_REJECT
SHALLOW_LOOP_FAILURE
PARALLEL_BRANCH_FAILURE
TOOL_STRATEGY_FAILURE
```

这些 failure 才能可靠进入：

```text
Experience
→ Candidate
→ Replay
→ Gate
→ Canary
→ Promote/Rollback
```

不要在真实评测闭环之前自动生成/部署这类 Candidate。

---

# 9. 推荐实施顺序

严格按：

```text
Phase 1  修 Dynamic Graph dependency bug
↓
Phase 2  真 Parallel Execution
↓
Phase 3  Verifier Deep Loop
↓
Phase 4  Critic Deep Loop
↓
Phase 5  Security / Reliability Deep Loop
↓
Phase 6  Fix Strategy Replan
↓
Phase 7  Runtime FeatureFlags
↓
Phase 8  Evaluation V4 Runtime Runner
↓
Phase 9  60-case Scenario Corpus
↓
Phase 10 Real Ablation
↓
Phase 11 CI Hard Gates
↓
Phase 12 Evolution Attribution
```

不要同时改 Planner、Scheduler、Verifier、Fix 和 Evaluation。

---

# 10. 每个 Phase 的开发节奏

```text
Implement
↓
Unit Test
↓
Integration Test
↓
Failure Injection
↓
Scenario Test
↓
Benchmark
↓
Commit / Freeze
↓
Next Phase
```

任一阶段不过，不进入下一阶段。

---

# 11. 最终闭环验收标准

只有全部满足才算完成。

## Planner

```text
不同语义 PR → 不同 TaskGraph
Validator + Fallback 真生效
```

## Replan

```text
Critic / Verifier
→ targeted ReplanRequest
→ precise Agent
→ 新 Graph Node
→ downstream dependency 更新
```

## Parallel

```text
两个 READY Agent → 同时 RUNNING
```

不是仅仅：

```text
batch size > 1
```

## Deep Loop

至少 Verifier 实际出现：

```text
semantic_verify
→ insufficient
→ targeted_test
→ enough
→ Final
```

## Dynamic Graph

至少真实生成 4 种不同 Graph Shape。

## Evaluation

正式 V4 必须真实经过：

```text
SixAgentReviewer
Coordinator
AgentLoop
A2A
Artifact
```

不能使用 synthetic metric generation。

## Ablation

必须量化：

```text
Planner
Replan
Parallel
Critic
Verifier
Deep Loop
```

各自对：

```text
质量
成本
延迟
召回
FP
```

的影响。

---

# 12. 完成后可以安全描述的能力

> 构建 6-Agent 分层协作 Runtime，由 Coordinator 基于语义变更动态生成并校验 TaskGraph，通过 A2A 对 Specialist Agent 进行并行 fan-out/fan-in 调度；各 Agent 内部采用 Observation-driven Plan/Act/Replan Loop，并根据 Critic/Verifier 反馈执行 Targeted Replan 与运行时 Graph Mutation；结合确定性 Harness Gate、自进化 Replay/Canary/Rollback 与真实 Ablation Evaluation 验证 Multi-Agent 协作收益。

---

# 13. 最终一句话

本轮真正闭环的判断标准不是：

> “又新增了几个模块”。

而是必须真实证明：

```text
Planner 真规划
Scheduler 真并行
Agent 真根据 Observation 选下一 Tool
Replan 真针对具体问题
Graph 真动态变化
Evaluation 真跑实际 Runtime
Ablation 真证明机制价值
```

七点全部成立后，EvoReview-Agent 的 Multi-Agent 部分才算从“架构实现”进入“完整工程闭环”。
