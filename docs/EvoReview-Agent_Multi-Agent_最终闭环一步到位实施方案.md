# EvoReview-Agent Multi-Agent 最终闭环实施方案（一步到位版）

> 目标：在当前已经具备 6 Core Agents、AgentLoop、A2A、Dynamic Planner、Targeted Replan、Parallel Scheduler、Dynamic Graph、Runtime Evaluation V4 和 Self-Evolution Harness 的基础上，完成最后一次结构性收尾，使 Multi-Agent 部分达到：
>
> **真正的 Result-driven Dynamic Multi-Agent Runtime + Observation-driven Local Loops + Targeted Replan + Runtime Graph Mutation + Real Ablation Evidence + CI Hard Gates + Evolution Attribution。**

---

# 0. 最终闭环判定标准

只有以下 8 点全部满足，才判定 Multi-Agent 真正闭环：

```text
1. Planner 真正根据语义生成不同 TaskGraph
2. Scheduler 真正并行执行 READY Agents
3. Agent 真正根据 Observation 决定下一 Tool
4. Critic / Verifier 能触发 Targeted Replan
5. Replan 后的新证据会重新进入下游 Verifier，而不是事后补证
6. Critic / Verifier / Fix 都是 Runtime Conditional Node
7. Evaluation V4 真正测出不同机制 ON/OFF 的差异
8. CI 用 Hard Assertions 阻止“机制看似存在但实际上未生效”
```

---

# 1. 当前剩余的三个根问题

## 1.1 Replan 时序仍可能发生得太晚

错误闭环：

```text
Security
 ↓
Critic
 ↓
Verifier
 ↓
Fix
 ↓
Finalize
 ↓
发现 ReplanRequest
 ↓
Security-Recheck
```

真正正确的闭环：

```text
Security
 ↓
Critic
 ↓
发现 evidence gap
 ↓
Coordinator 立即修改 Graph
 ↓
Security-Recheck
 ↓
Verifier-v2
 ↓
Fix（可选）
 ↓
Arbiter
```

## 1.2 下游节点必须彻底 Runtime Conditional

禁止继续把：

```text
Critic
Verifier
Fix
```

全部在初始 Planner 阶段固定塞进 DAG。

目标：

```text
Planner 只生成初始必要工作
↓
Runtime 根据 Artifact 决定是否插入 Critic / Verifier / Fix
```

## 1.3 Evaluation 必须证明组件价值

如果：

```text
Full == No Planner
Full == No Replan
Full == No Parallel
Full == Shallow Loop
```

则优先排查：

```text
A. Feature Flag 没真实改变 Runtime
B. Scenario 没触发对应机制
C. Metric / Gold 计算错误
```

---

# 2. 最终目标执行链

```text
PR Diff
  ↓
Semantic Understanding
  ↓
Risk Profile
  ↓
Coordinator Agent
  ↓
Dynamic Planner
  ↓
TaskGraphValidator
  ↓
Initial Minimal Graph
  ↓
Parallel Scheduler
  ↓
┌───────────────────────┐
│ Security Agent        │
│ Reliability Agent     │
└───────────────────────┘
  ↓
Intermediate Artifacts
  ↓
GraphPolicy.evaluate()
  ↓
Need Critic?
  ├─ No
  └─ Yes → Critic Agent
               ↓
        ReplanRequest?
         ├─ No
         └─ Yes
              ↓
          Graph Mutation
              ↓
       Targeted Specialist
              ↓
        New Evidence
              ↓
           Verifier
              ↓
       Verification Result
              ↓
          Need Fix?
         ├─ No
         └─ Yes → Fix Agent
                    ↓
               Fix Verify Loop
                    ↓
             Deterministic Arbiter
```

旁路：

```text
All Trace / Graph / Artifact / Outcome
                 ↓
         Evolution Harness
                 ↓
Candidate → Replay → Gate → Canary → Promote/Rollback
```

---

# 3. Phase 1：彻底修正 Replan 时序

## 3.1 核心原则

Replan 必须发生在 `finalize()` 之前。

Coordinator 每完成一个 batch 后执行：

```text
1. collect artifacts
2. update graph state
3. inspect new artifacts
4. evaluate runtime graph policy
5. consume replan requests
6. mutate graph
7. calculate next READY nodes
8. continue
```

推荐伪代码：

```python
while not graph.is_terminal():
    batch = scheduler.next_batch()
    if not batch:
        break

    results = delegator.submit_and_collect(batch)
    graph.apply_results(results)

    runtime_events = inspect_new_artifacts(results)

    graph_policy.apply(
        graph=graph,
        events=runtime_events,
        state=state,
    )

    replan_requests = collect_replan_requests(runtime_events)
    for req in replan_requests:
        apply_targeted_replan(graph, req)

    scheduler.refresh()

return finalize()
```

---

# 4. Phase 2：Replan 改为“子图插入”

错误：

```text
Security.status = pending
```

正确：

```text
Security-v1
 ↓
Critic
 ↓
Security-Recheck-F17
 ↓
Verifier-F17
```

新增：

```python
AgentTaskNode(
    node_id="replan-security-F17-1",
    task_type="review.security",
    agent_id="security-agent",
    objective="trace source-to-sink evidence for F17",
    dependencies=["critic-1"],
    metadata={
        "replan_request_id": "...",
        "finding_id": "F17",
        "requested_action": "trace_dataflow",
    }
)
```

保留原历史节点，不回滚原状态。

---

# 5. Phase 3：Replan 后强制重新验证

新增：

```text
artifact_version
finding_version
verification_version
```

示例：

```text
Finding F17 v1
→ Verifier Result v1

Security-Recheck
→ Finding F17 v2

此时：
Verifier Result v1 = STALE
→ 必须生成 Verifier(F17 v2)
```

新增模块：

```text
evoagent/loop_agents/invalidation.py
```

接口：

```python
invalidate_downstream(graph, changed_artifact_ids)
```

规则：

```text
Finding changed
→ Critic downstream stale
→ Verifier stale
→ Fix stale
```

已完成历史不删除，仅标记：

```text
status = superseded
```

---

# 6. Phase 4：Critic Runtime Conditional

Planner 不再默认创建 Critic。

Runtime Trigger：

```text
HIGH_RISK
LOW_CONFIDENCE
AGENT_DISAGREEMENT
INSUFFICIENT_EVIDENCE
NOVEL_FINDING
MULTI_SOURCE_CONFLICT
```

不满足则：

```text
直接跳过 Critic
```

---

# 7. Phase 5：Verifier Runtime Conditional

Verifier Trigger：

```text
critical/high-risk finding
low confidence
conflict
replan result
auto-fix candidate
policy requires
```

低风险 deterministic rule：

```text
Specialist → Arbiter
```

但：

```text
High-risk / critical → 必须经过 Verifier
```

---

# 8. Phase 6：Fix 必须完全 Runtime Insert

初始 Planner 禁止创建 Fix node。

Fix 只能由最新 Verifier Artifact 触发：

```python
if (
    verified_findings
    and remediation_enabled
    and repo_permission_ok
    and policy_allows_fix
):
    GraphMutator.add(FixNode(...))
```

Fix 输入必须携带：

```text
finding_id
finding_version
verification_artifact_id
verification_version
```

并断言：

```python
assert verification.finding_version == latest_finding_version
```

不一致则拒绝或重新验证。

---

# 9. Phase 7：统一 Runtime Graph Event Model

新增：

```text
evoagent/loop_agents/events.py
```

事件类型：

```text
AGENT_COMPLETED
AGENT_FAILED
FINDINGS_EMITTED
CRITIQUE_EMITTED
REPLAN_REQUESTED
FINDING_UPDATED
VERIFICATION_COMPLETED
FIX_REQUESTED
FIX_COMPLETED
GRAPH_MUTATED
ARTIFACT_SUPERSEDED
```

执行流：

```text
Artifact
↓
RuntimeEvent
↓
GraphPolicy
↓
Graph Mutation
```

避免 Coordinator 中堆叠大量 task_type 分支。

---

# 10. Phase 8：统一 Observation-driven Agent Decision

新增：

```text
evoagent/loop_agents/decision.py
```

定义：

```python
@dataclass
class AgentDecision:
    action: str
    tool_name: str | None
    arguments: dict
    reason_code: str
    confidence: float
```

所有 Agent 都采用：

```text
state
→ decide_next_action()
→ tool / final
```

## Security

```text
rule_scan
→ semantic_scan
→ trace_dataflow / inspect_context
→ Final
```

## Reliability

```text
rule_scan
→ semantic_scan
→ inspect_execution_path
→ targeted_test
→ Final
```

## Critic

```text
compare
→ evidence check
→ conflict check
→ explanation check
→ ReplanRequest / Final
```

## Verifier

```text
inspect_evidence
→ verify_rule_signature
→ semantic_verify
→ run_targeted_test
→ cross_check_finding
→ Final
```

## Fix

```text
strategy A
→ compile/test failure
→ classify failure
→ strategy B
→ verify
```

---

# 11. Phase 9：Parallel Scheduler 最终收尾

当前真实并发基础保留，只补：

```text
batch_id
parallel_width
started_at
finished_at
agent_ids
success_count
failure_count
```

正式 Evaluation 必须读取真实 parallel trace，而不是推断并行。

---

# 12. Phase 10：Feature Flags 必须真实进入 Runtime

统一：

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

映射：

```text
planner=False
→ SemanticPlanner disabled
→ FallbackPlanner

targeted_replan=False
→ ReplanRequest 不产生 Graph Mutation

critic=False
→ GraphPolicy 不插 Critic

verifier=False
→ 仅 policy 允许场景跳过 Verifier

parallel_scheduler=False
→ max_parallel_agents = 1

deep_loop=False
→ shallow stepper
```

每条 evaluation trace 记录：

```text
feature_flags_snapshot
```

---

# 13. Phase 11：彻底修正 Evaluation V4 Planning 指标

每个 Scenario 加：

```text
expected_agents
allowed_agents
forbidden_agents
required_graph_edges
optional_graph_edges
```

Routing：

```text
TP = 调用了 expected agent
FP = 调用了 forbidden/unnecessary agent
FN = expected agent 未调用

Precision = TP/(TP+FP)
Recall = TP/(TP+FN)
```

不能再出现所有 Variant `planning_quality=0` 而没有解释。

---

# 14. Phase 12：Replan Gold Scenario

每个 Replan Case 必须：

```text
initial evidence intentionally insufficient
Critic must emit replan
target agent known
new evidence changes downstream result
```

示例：

```text
Security-v1:
找到 SQL concat，但没确认 request input

Critic:
MISSING_SOURCE_SINK_EVIDENCE

Expected:
Security-Recheck

Security-v2:
确认 request.args → execute()

Verifier:
verified = true
```

Ablation：

```text
No Replan:
FN / uncertain

Full:
Recovered FN
```

---

# 15. Phase 13：Deep Loop Gold Scenario

必须设计：

```text
Tool A alone insufficient
Tool B necessary
```

例如：

```text
rule_scan → suspicious
semantic_scan → ambiguous
trace_dataflow → confirm
```

目标：

```text
Deep Loop hard-case success > Shallow Loop
```

---

# 16. Phase 14：Parallel Gold Scenario

使用两个真正 READY Agent：

```text
Security
Reliability
```

记录：

```text
sequential_duration
parallel_duration
speedup_ratio
```

Hard Gate：

```text
parallel_duration < sequential_duration * 0.8
```

---

# 17. Phase 15：Critic Gold Scenario

设计：

```text
Specialist emits plausible FP
Critic evidence check rejects it
```

比较：

```text
No Critic → FP remains
Full → FP suppressed
```

---

# 18. Phase 16：Verifier Gold Scenario

设计：

```text
Specialist confidence high
但 targeted test 不复现
```

比较：

```text
No Verifier → FP
Full → rejected
```

---

# 19. Phase 17：Scenario Corpus 重新校准

建议最终：

```text
80 cases
```

分布：

```text
Planning                15
Parallel                 8
Deep Loop               12
Replan                   12
Critic                   10
Verifier                 10
Fix                       5
Failure/Recovery          8
```

原则：

> 一个 Scenario 主要测一个机制，最多附带一个次级机制。

---

# 20. Phase 18：正式 Ablation 固定

```text
A Full
B No Dynamic Planner
C No Targeted Replan
D No Critic
E No Verifier
F Sequential Scheduler
G Shallow Loops
```

每个 Variant 输出：

```text
called_agents
graph_shapes
replan_requests
replan_targets
graph_revisions
parallel_batches
loop_steps
tool_calls
critic_decisions
verifier_decisions
fix_attempts
```

---

# 21. Phase 19：禁止 Overall 掩盖机制价值

正式报告分栏：

```text
Detection Quality
Planning Quality
Replan Quality
Collaboration Quality
Loop Quality
Latency
Cost
```

不要只看：

```text
overall
```

例如 Parallel 主要评估 latency，不要求提升 F1。

---

# 22. Phase 20：最终 Hard Gates

## Planner

```text
Routing Recall >= 0.90
Unnecessary Agent Invocation < threshold
```

## Replan

```text
Correct Target Rate >= 0.90
Replan Recovery Rate > No-Replan baseline
```

## Parallel

```text
speedup > 1.2x
```

## Deep Loop

```text
hard-case success >= shallow + 10pp
```

若 corpus 稳定性不足，可先设更保守阈值，但必须显著优于 shallow。

## Critic

```text
FP <= No-Critic
```

## Verifier

```text
FP < No-Verifier
```

## Graph Safety

```text
no cycle
no self-cycle
no stale verification reaches Fix
```

## Fix Safety

```text
Fix only after latest verified finding
```

---

# 23. Phase 21：CI 最终升级

建议拆为：

```text
multi-agent-planner-gate
multi-agent-replan-gate
multi-agent-parallel-gate
multi-agent-deep-loop-gate
multi-agent-dynamic-graph-gate
multi-agent-runtime-eval
multi-agent-ablation-gate
```

或者保留一个总 Job，但每项单独输出 Gate 结果。

---

# 24. Phase 22：CI 必须读 JSON，不解析 Markdown

正式输出：

```text
evaluation_v4_real.json
evaluation_v4_real.md
```

JSON 用于 CI。

示例：

```python
report = json.load(open("evaluation_v4_real.json"))

assert report["planner"]["routing_recall"] >= 0.90
assert report["replan"]["correct_target_rate"] >= 0.90
assert report["parallel"]["speedup"] > 1.2
assert report["graph"]["self_cycles"] == 0
assert report["graph"]["stale_fix_inputs"] == 0
```

---

# 25. Phase 23：Self-Evolution Attribution 最终接入

新增：

```text
PLANNER_ROUTING_MISS
PLANNER_OVER_ROUTING
WRONG_REPLAN_TARGET
REPLAN_TOO_LATE
REPLAN_INSUFFICIENT
CRITIC_FALSE_ACCEPT
CRITIC_FALSE_REJECT
VERIFIER_FALSE_ACCEPT
VERIFIER_FALSE_REJECT
SHALLOW_LOOP_FAILURE
PARALLEL_BRANCH_FAILURE
FIX_STALE_INPUT
```

Candidate 仍必须：

```text
Candidate
↓
Replay
↓
Validation Gate
↓
Final Holdout
↓
Freeze
↓
Canary
↓
Promote / Rollback
```

Agent 不允许直接修改 Production。

---

# 26. Phase 24：最重要的 E2E Replan Closed-Loop Test

新增：

```text
tests/multi_agent/test_replan_closed_loop.py
```

完整验证：

```text
Security-v1
 ↓
Critic
 ↓
ReplanRequest
 ↓
Graph revision +1
 ↓
Security-Recheck
 ↓
Finding version +1
 ↓
old Verification stale
 ↓
Verifier-v2
 ↓
Final Arbiter only sees latest result
```

断言：

```text
replan_count == 1
graph_revision >= 2
security runs == 2
verifier consumes latest artifact
old verifier artifact superseded
```

---

# 27. Phase 25：Stale Artifact Test

```text
Finding-v1
↓
Verifier-v1
↓
Replan
↓
Finding-v2
```

必须：

```text
Verifier-v1 = superseded
Fix cannot use Verifier-v1
```

---

# 28. Phase 26：Dynamic Graph Shape Test

至少真实出现：

```text
A. Reliability

B. Security → Verifier

C. Security + Reliability → Critic → Verifier

D. Security → Critic → Security-Recheck → Verifier → Fix
```

---

# 29. Phase 27：Feature Flag Effect Test

每个 Flag 必须改变 Trace。

例如：

```text
parallel=True → overlap
parallel=False → sequential
```

```text
deep_loop=True → 3 tools
deep_loop=False → 1 tool
```

```text
replan=True → graph revision
replan=False → no graph mutation
```

如果 ON/OFF Trace 完全相同：

```text
CI FAIL
```

---

# 30. Phase 28：Ablation 允许负结果

如果：

```text
No Critic > Full
```

不要调指标让 Full 看起来更好。

应判断：

```text
Critic 是否过度过滤
```

如果真实如此：

```text
进入 Critic Policy Evolution
```

成熟评测的目标不是“证明每个 Agent 都有用”，而是：

> **证明每个 Agent 在什么场景下有用、代价是什么。**

---

# 31. 推荐最终目录

```text
evoagent/
├── loop_agents/
│   ├── coordinator.py
│   ├── security.py
│   ├── reliability.py
│   ├── critic.py
│   ├── verifier.py
│   ├── fix.py
│   ├── decision.py
│   ├── scheduler.py
│   ├── replan.py
│   ├── graph_policy.py
│   ├── invalidation.py
│   ├── events.py
│   ├── delegator.py
│   └── planning/
│
├── evaluation_v4/
│   ├── runtime_runner.py
│   ├── scenarios.py
│   ├── metrics.py
│   ├── ablation.py
│   ├── report.py
│   └── gates.py
```

---

# 32. 最终实施顺序

严格按：

```text
1. Replan 从 finalize 前移到主执行 Loop
2. Replan 改为插入新节点
3. Artifact version + stale propagation
4. Critic Runtime Conditional
5. Verifier Runtime Conditional
6. Fix Runtime Insert
7. Runtime Graph Event Model
8. Deep Loop 统一 Decision Model
9. Parallel Trace 完善
10. FeatureFlags 全面真实接入 Runtime
11. Evaluation Metrics 修正
12. Replan Gold Scenarios
13. Deep Loop Gold Scenarios
14. Critic / Verifier Gold Scenarios
15. 80-case Scenario Corpus
16. Real Ablation
17. JSON Hard Gate Report
18. CI Hard Assertions
19. Evolution Attribution 接入
20. End-to-End Closed Loop Regression
```

---

# 33. 开发规则：Red → Green → Freeze

每个 Phase：

```text
Red:
先写失败测试

Green:
实现功能

Freeze:
Benchmark + commit
```

任一 Phase 不通过，不进入下一阶段。

---

# 34. 最终验收 Checklist

## Runtime

- [ ] Planner 不同输入产生不同 TaskGraph
- [ ] 无 cycle / self-cycle
- [ ] READY Agents 真并行
- [ ] Observation 改变下一 Tool
- [ ] Critic 能发 Targeted Replan
- [ ] Replan 插入新 Node
- [ ] 新 Evidence 让旧 Verification stale
- [ ] Verifier 重新消费最新 Finding
- [ ] Fix 只消费最新 Verification
- [ ] Arbiter 只看最新有效 Artifact

## Evaluation

- [ ] Planner ON/OFF 行为不同
- [ ] Replan ON/OFF 行为不同
- [ ] Parallel ON/OFF latency 不同
- [ ] Deep Loop ON/OFF hard-case 不同
- [ ] Critic ON/OFF FP 行为不同
- [ ] Verifier ON/OFF FP 行为不同
- [ ] Scenario gold 不自相矛盾
- [ ] 正式结果来自 Runtime Trace

## CI

- [ ] JSON report
- [ ] Planner Gate
- [ ] Replan Gate
- [ ] Parallel Gate
- [ ] Deep Loop Gate
- [ ] Graph Safety Gate
- [ ] Fix Stale Input Gate
- [ ] Ablation Gate
- [ ] Legacy benchmark regression gate

## Evolution

- [ ] failure attribution 可定位到 Agent / Planner / Tool / Replan
- [ ] Candidate agent-specific
- [ ] Replay
- [ ] Holdout
- [ ] Freeze
- [ ] Canary
- [ ] Rollback

---

# 35. 完成后的项目定义

完成本方案后，可以准确描述为：

> **A policy-governed, hierarchical multi-agent code-review runtime with semantic task planning, validated dynamic task graphs, bounded parallel A2A orchestration, observation-driven local agent loops, targeted result-driven replanning, runtime graph mutation, deterministic arbitration, and closed-loop self-evolution under replay, holdout, canary, and rollback governance.**

中文：

> **构建面向代码审查的分层 Multi-Agent Runtime，由 Coordinator 基于语义变更动态生成并校验 TaskGraph，通过 A2A 对专业 Agent 进行有界并行调度；各 Agent 采用 Observation-driven Agent Loop，根据 Critic/Verifier 结果进行 Targeted Replan 与运行时 Graph Mutation，并由确定性 Harness 完成最终裁决，同时通过 Replay、Holdout、Canary 与 Rollback 实现受治理的闭环自进化。**

---

# 36. 完成后不要继续堆什么

完成本方案后，不建议继续：

```text
增加 Agent 数量
把所有 Agent 都拆微服务
加入更多消息中间件
再造一个 Planner
把 Arbiter Agent 化
把 Evolution 全交给 LLM
```

下一阶段转向：

```text
真实 PR 数据
跨仓库泛化
真实漏洞类别
真实开发者反馈
成本 / 延迟 / 召回权衡
长时间稳定运行
企业多租户 / 权限 / SLO
```

---

# 37. 最终一句话

真正闭环必须能证明：

```text
任务被规划
→ Agent 并行执行
→ 结果被观察
→ 缺口触发精准 Replan
→ 新证据使旧结果失效
→ 下游重新验证
→ 必要时修复
→ Harness 最终裁决
→ Evaluation 证明每个机制真实有效
→ Evolution 根据真实失败继续改进
```

当这条链路能够同时被 **代码、Trace、测试、Ablation、CI 和 Evolution Attribution** 证明时，EvoReview-Agent 的 Multi-Agent 部分才算真正“一步到位”完成工程闭环。
