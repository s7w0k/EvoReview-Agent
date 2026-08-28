# EvoReview-Agent Multi-Agent 深化优化实施计划

> 目标：在当前 6-Core-Agent + Agent Loop + A2A + Harness + Self-Evolution 架构已经成立的基础上，进一步完成以下 6 项 Multi-Agent 深化优化：
>
> 1. Semantic Dynamic Planner  
> 2. Targeted Result-driven Replan  
> 3. Parallel TaskGraph Scheduler  
> 4. Deeper Local Agent Loops  
> 5. Truly Dynamic Collaboration Graph  
> 6. Multi-Agent Value Evaluation / Ablation

---

# 1. 总体目标

当前系统已经具备：

```text
Coordinator Agent
├── Security Agent
├── Reliability Agent
├── Critic Agent
├── Verifier Agent
└── Fix Agent

+ AgentLoop
+ TaskGraph
+ A2A
+ Governed Tools
+ Deterministic Arbiter
+ Harness-level Self-Evolution
```

下一阶段的重点不再是“是不是 Multi-Agent”，而是：

> **让 Coordinator 真正根据任务语义和中间结果动态决定“谁做什么、什么时候做、需不需要继续做”，并让每个 Agent 的局部 Loop 真正根据 Observation 自主选工具、补证和结束。**

最终目标：

```text
PR Diff
  ↓
Semantic Understanding
  ↓
Coordinator Planner
  ↓
Dynamic TaskGraph
  ↓
Parallel A2A Delegation
  ↓
Agent Local Loops
  ↓
Intermediate Artifacts
  ↓
Targeted Replan
  ↓
Graph Mutation
  ↓
Verifier / Fix（按需）
  ↓
Deterministic Arbiter
  ↓
Final Result
```

---

# 2. 六项优化与优先级

| 优先级 | 优化项 | 目标 |
|---|---|---|
| P0 | Semantic Dynamic Planner | Coordinator 从规则路由升级为语义规划 |
| P0 | Targeted Result-driven Replan | 根据具体证据缺口精确重规划 |
| P0 | Parallel TaskGraph Scheduler | 无依赖 Agent 真正并行 fan-out/fan-in |
| P1 | Deeper Local Agent Loops | Critic / Verifier / Specialist 真正多步自主 |
| P1 | Truly Dynamic Collaboration Graph | Critic / Verifier / Fix 不再固定出现 |
| P1 | Multi-Agent Value Evaluation | 证明动态 Multi-Agent 机制确实带来收益 |

---

# 3. Phase 0：冻结当前 Multi-Agent 基线

## 3.1 目标

在开始改动前，固定当前行为，防止后续无法判断性能变化来自哪里。

## 3.2 建议新增架构版本

```text
EVOAGENT_AGENT_ARCHITECTURE=six-agent-v1
EVOAGENT_AGENT_ARCHITECTURE=six-agent-v2
```

其中：

```text
six-agent-v1 = 当前实现
six-agent-v2 = 本计划新实现
```

不要直接覆盖当前 `six-agent`。

## 3.3 保存以下基线

至少记录：

```text
100-case benchmark
F1
Precision
Recall
High-risk Recall
Clean Accuracy
Critical Misses

Average Coordinator Steps
Average Delegated Tasks
Average Agent Loop Steps
Average Tool Calls
P50 / P95 Latency
A2A Calls
Replan Count
```

## 3.4 保存关键行为 Fixture

至少建立以下固定案例：

```text
security-only PR
reliability-only PR
security + reliability PR
clean PR
critic evidence-gap PR
verifier conflict PR
fix-success PR
fix-failure PR
```

## Phase 0 验收

- `six-agent-v1` 所有原测试通过；
- frozen benchmark 可重复；
- 当前 6-Agent 行为有可追踪基线。

---

# 4. Phase 1：Semantic Dynamic Planner

这是本计划最重要的阶段。

## 4.1 当前问题

当前 Coordinator 的图生成大体还是：

```text
profile_risk
→ agents=[security/reliability]
→ specialists
→ critic
→ verifier
→ fix
```

虽然 Specialist 选择是动态的，但真正的任务分解与依赖结构仍较规则化。

目标：

> **Planner 根据 PR Diff 的语义、风险、变更范围、已有 Artifact 和 Runtime Policy 生成结构化 TaskGraph。**

## 4.2 新增 Planner 数据结构

建议新增：

```text
evoagent/loop_agents/planning/
├── __init__.py
├── models.py
├── planner.py
├── validator.py
└── fallback.py
```

核心结构：

```python
@dataclass
class PlanningContext:
    objective: str
    changed_files: list[str]
    semantic_summary: dict
    risk_profile: dict
    available_agents: list[dict]
    execution_policy: dict
    prior_artifacts: list[dict]

@dataclass
class PlannedTask:
    task_id: str
    agent_id: str
    task_type: str
    objective: str
    dependencies: list[str]
    priority: int
    required_evidence: list[str]
    stop_condition: dict

@dataclass
class PlanningDecision:
    tasks: list[PlannedTask]
    rationale_codes: list[str]
    confidence: float
```

注意：只保存结构化理由，如：

```text
AUTH_CHANGE
SECURITY_SENSITIVE_FILE
EXCEPTION_PATH_CHANGED
LOW_VERIFICATION_CONFIDENCE
```

不要保存原始 Chain-of-Thought。

## 4.3 增加 Semantic Understanding 阶段

Coordinator 在 Planner 前新增：

```text
inspect_diff
semantic_change_summary
risk_profile
```

输出例如：

```json
{
  "change_types": ["authentication", "database"],
  "sensitive_paths": ["auth.py"],
  "new_external_inputs": ["request.args"],
  "control_flow_changes": true,
  "test_changes": false,
  "estimated_risk": "high"
}
```

## 4.4 Planner 输出真实 TaskGraph

例如 PR A：

```text
Security
  ↓
Critic
  ↓
Verifier
```

PR B：

```text
Reliability
  ↓
Verifier
```

PR C：

```text
Security ─────┐
              ├→ Critic → Verifier
Reliability ──┘
```

## 4.5 Planner 必须经过 Harness Validator

新增：

```python
class TaskGraphValidator:
    validate_agents_exist()
    validate_dependencies()
    validate_no_cycle()
    validate_tool_permissions()
    validate_budget()
    validate_required_verification()
    validate_fix_policy()
```

流程：

```text
Planner Proposal
 ↓
TaskGraphValidator
 ↓
Valid?
 ├─ Yes → Execute
 └─ No  → Repair once
          ↓
       still invalid?
          ↓
     fallback planner
```

## 4.6 Fallback Planner

保留 deterministic fallback：

```text
security-sensitive → Security
runtime/reliability → Reliability
high-risk findings → Critic + Verifier
verified + fix-policy → Fix
```

## 4.7 Planner 的预算

新增：

```text
max_planner_attempts = 2
max_graph_nodes = 12
max_graph_depth = 6
max_parallel_width = 4
```

## Phase 1 测试

必须覆盖：

```text
security-only → 不调用 reliability
reliability-only → 不调用 security
clean → 不进入 fix
high-risk → verifier 必须存在
invalid DAG → validator reject
cycle → reject
unknown agent → reject
budget overflow → reject
planner failure → deterministic fallback
```

## Phase 1 验收

必须证明：

```text
不同 PR
→ 生成不同 TaskGraph
```

而不是仅仅改变 Specialist list。

---

# 5. Phase 2：Targeted Result-driven Replan

## 5.1 当前问题

目前 Critic 的 ReplanRequest 仍过于粗粒度。

目标：

> **Replan 必须明确：哪个问题、哪个 Agent、缺什么证据、为什么要重跑、重跑范围是什么。**

## 5.2 新增 ReplanRequest Model

```python
@dataclass
class ReplanRequest:
    request_id: str
    source_agent: str
    target_agent: str | None
    target_capability: str | None
    finding_id: str | None
    reason_code: str
    reason_summary: str
    requested_action: str
    required_evidence: list[str]
    context_refs: list[str]
    priority: int
```

示例：

```json
{
  "source_agent": "critic-agent",
  "target_agent": "security-agent",
  "finding_id": "F17",
  "reason_code": "MISSING_SOURCE_SINK_EVIDENCE",
  "requested_action": "trace_dataflow",
  "required_evidence": [
    "external input source",
    "sink reachability"
  ]
}
```

## 5.3 Replan Target Resolver

新增：

```text
evoagent/loop_agents/replan.py
```

逻辑：

```text
explicit target_agent
    ↓
use target
else
    ↓
match target_capability
    ↓
AgentRegistry
    ↓
choose eligible agent
```

不能再随便找第一个 `review.*` node。

## 5.4 Replan 应修改 Graph，而不是 reset 原节点

原图：

```text
Security
   ↓
Critic
   ↓
Verifier
```

Critic 要补 SQL 数据流证据：

```text
Security
   ↓
Critic
   ↓
Security-Recheck(F17)
   ↓
Verifier
```

新增节点，而不是简单把原 Security 状态重新设为 PENDING。

## 5.5 Replan Budget

```text
max_replans_per_review = 3
max_replans_per_finding = 2
max_same_agent_replans = 2
max_graph_revision
```

## 5.6 Prevent Replan Loop

Fingerprint：

```text
target_agent
+ finding_id
+ requested_action
+ context_refs
```

重复两次后：

```text
NO_PROGRESS
→ stop / fallback verifier
```

## Phase 2 测试

覆盖：

```text
Critic→Security recheck
Verifier→Security additional evidence
Verifier→Reliability targeted test
Fix→Fix retry
duplicate replan blocked
unknown target capability fallback
budget exhaustion stop
```

## Phase 2 验收

Trace 必须出现：

```text
graph_revision=1
→ critic artifact
→ replan_request=R1
→ graph_revision=2
→ inserted security-recheck-F17
```

---

# 6. Phase 3：Parallel TaskGraph Scheduler

## 6.1 当前问题

即使 `next_ready()` 同时返回多个节点，Coordinator 仍可能逐个执行。

目标：

> **所有无依赖且满足资源限制的节点并行 fan-out，完成后 fan-in。**

## 6.2 新增 Scheduler

```text
evoagent/loop_agents/scheduler.py
```

接口：

```python
class TaskGraphScheduler:
    def ready_nodes(graph): ...
    def schedule(graph, budget): ...
    def wait_batch(batch): ...
    def reconcile(results): ...
```

## 6.3 调度流程

```text
TaskGraph
 ↓
find READY nodes
 ↓
apply concurrency budget
 ↓
A2A submit batch
 ↓
RUNNING
 ↓
wait/poll
 ↓
COMPLETED / FAILED
 ↓
fan-in
 ↓
unlock dependent nodes
```

## 6.4 示例

```text
Security ─────┐
              ├→ Critic
Reliability ──┘
```

应表现为：

```text
t0:
Security RUNNING
Reliability RUNNING

t1:
Security COMPLETED
Reliability COMPLETED

t2:
Critic READY
```

## 6.5 并发预算

```text
max_parallel_agents
max_parallel_remote_calls
max_parallel_tool_calls
per_agent_concurrency
```

Fix 默认 concurrency=1。

## 6.6 Fail-fast vs Continue

不同节点加入：

```text
critical = true/false
```

例如：

```text
Security unavailable on high-risk PR
→ fail-safe / fallback

Reliability unavailable on low-risk PR
→ continue with warning
```

## Phase 3 测试

覆盖：

```text
Security + Reliability parallel
dependency blocks Critic
one Specialist timeout
one Specialist failure
partial completion
parallel budget=1
parallel budget=2
A2A timeout/fallback
```

## Phase 3 验收

至少证明：

```text
Parallel variant P95 latency
<
Sequential variant P95 latency
```

且质量不退化。

---

# 7. Phase 4：Deeper Local Agent Loops

重点不是“步骤越多越好”，而是：

> **Agent 根据 Observation 决定是否继续、调用哪个 Tool、是否终止。**

## 7.1 Security Agent 深化

```text
inspect target
 ↓
rule scan
 ↓
possible finding?
 ├─ No → Final
 └─ Yes
      ↓
semantic scan
      ↓
need source/sink proof?
 ├─ Yes → trace_dataflow
 └─ No
      ↓
need dependency context?
 ├─ Yes → inspect dependency
 └─ No
      ↓
Final
```

决策依据：

```text
finding confidence
risk severity
evidence completeness
semantic disagreement
```

## 7.2 Reliability Agent 深化

```text
rule scan
 ↓
exception/concurrency/runtime issue?
 ↓
inspect execution path
 ↓
need test?
 ├─ Yes → run targeted test
 └─ No
 ↓
need logging/observability check?
 ↓
Final
```

## 7.3 Critic Agent 深化

目标：

```text
receive findings
 ↓
compare peer findings
 ↓
conflict?
 ├─ Yes → inspect conflict
 ↓
evidence sufficient?
 ├─ No → check evidence
 ↓
explanation sound?
 ↓
fix actionable?
 ↓
generate targeted ReplanRequest
 ↓
Final
```

## 7.4 Verifier Agent 深化

```text
Finding
 ↓
inspect evidence
 ↓
select verification strategy

├─ rule signature
├─ semantic reproduction
├─ targeted test
├─ compile check
├─ cross-check
└─ context inspection

 ↓
Observation
 ↓
confidence enough?
 ├─ No → another verification method
 └─ Yes
 ↓
Final VerificationDecision
```

新增：

```python
VerificationStrategySelector
```

## 7.5 Fix Agent 深化

继续增加 Patch Strategy Selection：

```text
AST
deterministic
model-generated
```

并对失败分类：

```text
compile failure
test failure
scope too large
unsafe patch
```

根据失败类型重新选择修复策略，而不是重复同一种 patch。

## 7.6 每个 Agent 的 Stop Condition

统一：

```text
goal_satisfied
confidence_threshold_met
budget_exhausted
no_progress
tool_unavailable
policy_blocked
```

## Phase 4 测试

每个 Agent 至少包含：

```text
1-step case
2-step case
3-step case
tool failure recovery
no-progress
budget exhaustion
confidence stop
```

## Phase 4 验收

不能只证明 AgentLoop 执行了，必须证明：

```text
第二步 Tool 选择取决于第一步 Observation
```

---

# 8. Phase 5：Truly Dynamic Collaboration Graph

## 8.1 当前问题

后半段仍容易形成：

```text
Specialist → Critic → Verifier → Fix
```

目标：

> **Critic、Verifier、Fix 都应成为条件节点。**

## 8.2 Graph Policy

新增：

```text
evoagent/loop_agents/graph_policy.py
```

### Critic Trigger

```text
multiple agents disagree
high-risk finding
low-confidence finding
novel rule
insufficient explanation
```

否则 skip。

### Verifier Trigger

以下情况必须：

```text
high-risk
critical finding
auto-fix candidate
conflicting findings
low-confidence
```

### Fix Trigger

只有：

```text
verified = true
AND remediation allowed
AND fix policy enabled
AND repository permission allows
```

才创建 Fix。

## 8.3 Runtime Graph Mutation

支持：

```text
add node
remove pending node
replace node
change dependency
cancel unnecessary branch
```

不修改已完成历史。

## 8.4 典型 Graph

### Clean PR

```text
Reliability
 ↓
no findings
 ↓
Final
```

### High-risk Security

```text
Security
 ↓
Critic
 ↓
Security-Recheck
 ↓
Verifier
 ↓
Fix
```

### Specialist Disagreement

```text
Security ─────┐
              ├→ Critic
Reliability ──┘
                  ↓
              Verifier
```

### Verified but Fix not allowed

```text
Security
 ↓
Verifier
 ↓
Final Report
```

## Phase 5 测试

至少：

```text
clean graph has no critic/verifier/fix
high-risk graph includes verifier
verified+fix-policy includes fix
fix-policy-disabled excludes fix
critic inserted only on condition
runtime graph mutation works
cancelled branch does not execute
```

## Phase 5 验收

必须真实出现多种 Graph Shape，而不是同一模板。

---

# 9. Phase 6：Multi-Agent Value Evaluation

这是必须完成的一项，否则只能证明“实现了”，不能证明“有价值”。

## 9.1 Evaluation Harness V4

新增：

```text
evoagent/evaluation_v4/
scripts/run_multi_agent_evaluation_v4.py
```

比较：

| Variant | 系统 |
|---|---|
| A | Single Agent |
| B | Legacy staged Multi-Agent |
| C | 6-Agent Fixed Graph |
| D | 6-Agent Dynamic Planner |
| E | D + Targeted Replan |
| F | E + Parallel Scheduler |
| G | F + Deep Local Loops |

## 9.2 保留 100-case benchmark

继续比较：

```text
Precision
Recall
F1
High-risk Recall
Clean Accuracy
Critical Misses
```

但不把它当作动态 Multi-Agent 能力的唯一证据。

## 9.3 新建 Multi-Agent Scenario Benchmark

新增：

```text
evaluation_data/multi_agent_scenarios.jsonl
```

建议至少 40～60 个案例。

### Planning

```text
security-only
reliability-only
mixed
clean
high-risk auth
test-only
```

### Replan

```text
missing evidence
conflicting evidence
low confidence
specialist miss
```

### Collaboration

```text
security/reliability disagreement
critic overturn
verifier rejection
```

### Fix

```text
compile fail
test fail
unsafe patch
successful repair
```

### Resilience

```text
timeout
agent unavailable
malformed artifact
tool failure
```

## 9.4 新增 Multi-Agent 指标

### Planning Quality

```text
Agent Routing Precision
Agent Routing Recall
Unnecessary Agent Invocation Rate
TaskGraph Node Efficiency
```

### Replan Quality

```text
Replan Trigger Precision
Replan Success Rate
Replan Recovery Rate
Repeated Replan Rate
```

### Collaboration Quality

```text
Critic Correction Rate
Verifier Correction Rate
False Positive Suppression
Recovered False Negatives
Agent Disagreement Resolution Rate
```

### Loop Quality

```text
Average Agent Steps
Useful Tool Call Ratio
No-progress Rate
Budget Exhaustion Rate
```

### Efficiency

```text
Token Cost
Tool Calls
A2A Calls
P50 latency
P95 latency
Agent utilization
```

## 9.5 必做 Ablation

```text
Dynamic Planner ON/OFF
Targeted Replan ON/OFF
Critic ON/OFF
Verifier ON/OFF
Parallel Scheduler ON/OFF
Deep Local Loop vs shallow loop
```

重点回答：

```text
Dynamic Planner 是否减少无用 Agent 调用？
Targeted Replan 是否追回漏检？
Critic 是否降低错误 Finding？
Verifier 是否压低 FP？
Parallel Scheduler 是否降低延迟？
Deep Loop 是否提升复杂案例成功率？
```

---

# 10. Phase 7：Multi-Agent Failure Injection

新增：

```text
Coordinator planning failure
invalid TaskGraph
graph cycle
Security timeout
Reliability unavailable
Critic malformed replan
Verifier low-confidence loop
Fix repeated patch failure
duplicate ReplanRequest
A2A task lost
Artifact stale
Artifact correlation mismatch
parallel branch failure
```

必须验证：

```text
Harness 仍控制预算
Safety Gate 不被绕过
Arbiter 不被绕过
Evolution 不因失败自动 Promote
```

---

# 11. Phase 8：Observability 深化

Trace 增加：

```text
planning_id
graph_id
graph_revision
node_id
agent_id
agent_version
parent_node_id
replan_request_id
loop_step
tool_name
observation_id
artifact_id
a2a_task_id
```

最终能够回答：

```text
为什么调用了 Security？
为什么没有调用 Reliability？
为什么触发 Critic？
为什么进行了第二次 Security review？
Verifier 为什么拒绝 Finding？
Fix 为什么重试？
```

---

# 12. Phase 9：Self-Evolution 与新 Multi-Agent 机制结合

自进化继续放在 Harness 层。

新增 failure attribution：

```text
PLANNER_ROUTING_MISS
PLANNER_OVER_ROUTING
GRAPH_DEPENDENCY_ERROR
REPLAN_TARGET_ERROR
REPLAN_INSUFFICIENT
CRITIC_MISS
CRITIC_FALSE_CHALLENGE
VERIFIER_MISS
VERIFIER_FALSE_REJECTION
SPECIALIST_LOOP_TOO_SHALLOW
TOOL_SELECTION_ERROR
```

Candidate 可变为：

```text
coordinator.planner@v4
critic.policy@v3
verifier.strategy@v2
security.tool-policy@v5
```

仍必须：

```text
Candidate
→ Replay
→ Gate
→ Holdout
→ Canary
→ Promote/Rollback
```

---

# 13. 推荐目录变化

```text
evoagent/
├── loop_agents/
│   ├── coordinator.py
│   ├── security.py
│   ├── reliability.py
│   ├── critic.py
│   ├── verifier.py
│   ├── fix.py
│   ├── planning/
│   │   ├── models.py
│   │   ├── planner.py
│   │   ├── validator.py
│   │   └── fallback.py
│   ├── scheduler.py
│   ├── replan.py
│   ├── graph_policy.py
│   └── models.py
│
├── evaluation_v4/
│   ├── adapters.py
│   ├── scenarios.py
│   ├── metrics.py
│   ├── ablation.py
│   └── report.py
```

---

# 14. CI 新增

建议：

```text
multi-agent-planner
multi-agent-taskgraph
multi-agent-targeted-replan
multi-agent-parallel-scheduler
multi-agent-local-loop
multi-agent-dynamic-graph
multi-agent-eval-v4
multi-agent-ablation
multi-agent-failure-injection
```

---

# 15. Hard Assertions

## Planner

```text
invalid graph == reject
unknown Agent == reject
graph cycle == reject
budget overflow == reject
```

## Replan

```text
targeted replan actually targets requested agent
same replan cannot loop indefinitely
```

## Scheduler

```text
dependency never violated
parallel branch artifacts correctly correlated
```

## Verifier

```text
unverified high-risk finding cannot reach Fix
```

## Fix

```text
unverified patch cannot publish
```

## Evaluation

```text
legacy benchmark no unexplained regression
critical misses cannot increase beyond threshold
HTTP/InProcess semantics remain equivalent
```

---

# 16. 推荐实施顺序

```text
Phase 0  Freeze baseline
   ↓
Phase 1  Semantic Dynamic Planner
   ↓
Phase 2  Targeted Replan
   ↓
Phase 3  Parallel Scheduler
   ↓
Phase 4  Deep Local Agent Loops
   ↓
Phase 5  Dynamic Collaboration Graph
   ↓
Phase 6  Evaluation V4 + Ablation
   ↓
Phase 7  Failure Injection
   ↓
Phase 8  Observability
   ↓
Phase 9  Evolution Attribution
```

不要同时改 Planner、Scheduler、Verifier 和 Fix。

---

# 17. 每阶段开发节奏

```text
Implement
 ↓
Unit Test
 ↓
Integration Test
 ↓
Failure Injection
 ↓
Benchmark
 ↓
Compare with previous Phase
 ↓
Commit / Freeze
 ↓
Next Phase
```

---

# 18. 最终验收标准

完成全部 6 项后，必须能够证明：

### 1. Planner 真正动态

```text
不同 PR
→ 不同 TaskGraph
```

### 2. Replan 真正针对问题

```text
Critic / Verifier 指出具体缺口
→ Coordinator 插入精确任务
```

### 3. Multi-Agent 真并行

```text
Security + Reliability
→ bounded concurrent execution
```

### 4. Local Loop 真自主

```text
Observation A → Tool X
Observation B → Tool Y
```

### 5. Graph 真动态

至少存在：

```text
Security → Final

Reliability → Verifier → Final

Security + Reliability → Critic → Verifier

Security → Critic → Security-Recheck → Verifier → Fix
```

### 6. Multi-Agent 增益有实证

能够量化回答：

```text
Dynamic Planner 是否减少无用 Agent 调用？
Targeted Replan 是否追回漏检？
Critic 是否降低错误 Finding？
Verifier 是否压低 FP？
Parallel Scheduler 是否降低延迟？
Deep Loop 是否提升复杂案例成功率？
```

---

# 19. 最终目标架构

```text
                         PR / User Goal
                               │
                               ▼
                     Coordinator Agent
                               │
                   Semantic Understanding
                               │
                               ▼
                       Dynamic Planner
                               │
                       Harness Validator
                               │
                               ▼
                      Dynamic TaskGraph
                               │
                      Parallel Scheduler
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
      Security Agent     Reliability Agent      ...
        Local Loop          Local Loop
            │                  │
            └──────────┬───────┘
                       ▼
                Intermediate State
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   Enough evidence?              Evidence gap?
        │                             │
        ▼                             ▼
    Verifier                  Targeted Replan
        │                             │
        │                      Graph Mutation
        │                             │
        └──────────────┬──────────────┘
                       ▼
                    Fix?
                       │
                       ▼
               Deterministic Arbiter
                       │
                       ▼
                   Final Result
```

旁路：

```text
Trace / Outcome / Failure
          ↓
Evolution Harness
          ↓
Candidate
          ↓
Replay / Gate / Canary / Rollback
```

---

# 20. 一句话总结

本轮优化完成后，EvoReview-Agent 的 Multi-Agent 部分应该从：

> **“6 个独立 Agent + 基础 TaskGraph + 基础 Replan”**

升级为：

> **“Semantic Planner 驱动的动态 TaskGraph + 并行 Agent 调度 + Observation 驱动的局部 Agent Loop + 精确 Result-driven Replan + 条件化协作图 + 可量化证明收益的 Multi-Agent Runtime”。**
