# EvoReview-Agent：6 核心 Agent + Loop Engineering + A2A 架构升级逐步实施计划

> 目标：在保留现有 Harness Engineering、自进化闭环、A2A 基础设施与 Evaluation Harness 的前提下，将当前以 staged workflow 为主的 Multi-Agent Review 系统升级为 **6 个具有独立 Agent Loop 的核心 Agent**，形成“全局 Coordinator Loop + 局部 Agent Loop + A2A 通信 + 确定性 Harness Governance + Harness 层自进化”的生产级架构。

---

## 0. 本计划的明确架构约束

本计划严格按照已经确定的架构推进，不再增加额外核心 Agent。

### 0.1 六个核心 Agent

| 核心 Agent | 当前主要对应实现 | 升级后的职责 |
|---|---|---|
| **Coordinator Agent** | `MultiAgentCoordinator + PlannerAgent` | 全局目标理解、动态任务规划、A2A 调度、结果聚合、结果驱动 Replan |
| **Security Agent** | `SecurityRuleReviewer` / Security Specialist | 安全风险分析、工具调用、证据收集、多步安全 Review |
| **Reliability Agent** | `ReliabilityRuleReviewer` / Reliability Specialist | 稳定性、异常、并发、可观测性、回归风险 Review |
| **Critic Agent** | `CriticAgent + ReflectionAgent` | 对 Finding 提出质疑、寻找遗漏、要求补充证据、形成 Replan Request |
| **Verifier Agent** | `EvidenceAgent + VerifierAgent` | 独立证据验证、复现、静态/动态验证、Finding 最终可信度判断 |
| **Fix Agent** | `FixAgent + SafeFixer + RepairVerifier` | 修复规划、生成 Patch、编译/测试、失败后 Replan、生成 verified patch |

### 0.2 不做成核心 Agent 的组件

#### Arbiter
继续作为 **Harness 层确定性 Gate**，而不是自治 Agent。

原因：
- 最终 accept/reject 应可解释、可审计；
- 不应由 LLM 自主改变安全阈值；
- 可保证 deterministic replay；
- 便于与 Runtime Policy / Safety Floor 对接。

最终链路：

```text
Verifier Agent
      ↓
Fix Agent（可选）
      ↓
Deterministic Arbiter / Safety Gate
      ↓
Final Review Report / Fix Artifact
```

#### Evolution
继续属于 **Harness / Control Plane**。

```text
Production Trace / Outcome
        ↓
Failure Mining
        ↓
Candidate Generation
        ↓
Replay Evaluation
        ↓
Safety Gate
        ↓
Canary
        ↓
Promote / Rollback
```

可以未来加入 `Evolution Agent` 负责提出候选假设，但 **不属于本次 6 核心 Agent**，也不能自行 Promote。

### 0.3 `semantic-review` 与 `llm-review`

为了保持“六核心 Agent”边界：
- `semantic-review` 不再作为额外顶层 Agent；
- `llm-review` 不再作为额外顶层 Agent；
- 二者转换为 **Tool / Skill / Model Capability**；
- Security / Reliability / Critic / Verifier 根据权限调用。

例如：

```text
Security Agent
├── security_rule_scan
├── semantic_scan
├── code_context_search
└── llm_reasoning

Reliability Agent
├── reliability_rule_scan
├── semantic_scan
├── targeted_test
└── llm_reasoning
```

---

# 1. 当前项目基线与迁移原则

当前系统已经拥有以下基础，不需要推倒重建：

1. `AgentRuntime`
   - node budget
   - retries
   - cancellation
   - checkpoints
   - recovery

2. `AgentLoop`
   - step budget
   - wall-clock budget
   - tool-call budget
   - observation
   - no-progress detection
   - tool failure classification

3. `GovernedToolRegistry`
   - Tool schema validation
   - Policy
   - Budget
   - Circuit Breaker
   - Sandbox / Approval
   - Timeout
   - Audit

4. `MultiAgentCoordinator`
   - Planner
   - specialist fan-out / fan-in
   - deliberation
   - evidence
   - verifier
   - arbiter

5. A2A
   - `AgentCard`
   - `A2ATask`
   - `A2AMessage`
   - `A2AArtifact`
   - `TaskStatus`
   - HTTP + JSON-RPC
   - InProcess transport
   - retry / breaker / fallback
   - Agent Registry
   - Remote Security / Reliability production integration

6. Fix
   - `SafeFixer`
   - AST / line-level fix
   - `RepairVerifier`
   - compile / test gate
   - atomic commit
   - draft PR

7. Self-Evolution
   - Experience / Failure
   - Candidate
   - Replay
   - Safety Gate
   - Candidate Freeze
   - Canary
   - Rollback

因此本次升级原则是：

> **复用已有 Runtime / A2A / Governance / Evolution，只替换“Agent 行为模型和编排方式”。**

---

# 2. 目标总体架构

```text
                         User / GitHub Webhook / API
                                  │
                                  ▼
                             ReviewService
                                  │
                                  ▼
                           ReviewHarness
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │    Coordinator Agent    │
                    │                         │
                    │ Understand              │
                    │ → Plan                  │
                    │ → Delegate              │
                    │ → Observe               │
                    │ → Replan                │
                    │ → Finalize              │
                    └────────────┬────────────┘
                                 │
                    A2A Task / Message / Artifact
                                 │
             ┌───────────────────┼───────────────────┐
             ▼                   ▼                   ▼
      Security Agent      Reliability Agent      Critic Agent
      Local AgentLoop      Local AgentLoop       Local AgentLoop
             │                   │                   │
             └───────────────────┴─────────┬─────────┘
                                           │
                                           ▼
                                    Verifier Agent
                                    Local AgentLoop
                                           │
                                           ▼
                                       Fix Agent
                                    Local AgentLoop
                                           │
                                           ▼
                              Deterministic Arbiter
                                           │
                                           ▼
                                Final Report / Patch
```

旁路：

```text
             all Agent traces / outcomes / artifacts
                          │
                          ▼
                  Evolution Harness
                          │
      Candidate → Replay → Gate → Canary → Rollback
```

---

# 3. 两层 Loop Engineering

## 3.1 Global Loop：Coordinator Agent Loop

Coordinator 不再只运行固定：

```text
planner
→ specialists
→ deliberation
→ evidence
→ verifier
→ arbiter
```

而改为：

```text
Understand Goal
     ↓
Build / Update TaskGraph
     ↓
Select Agent
     ↓
A2A Delegate
     ↓
Observe Artifact
     ↓
Coverage / Conflict / Failure Check
     ↓
Need More Work?
 ┌──────┴───────┐
 Yes            No
 ↓               ↓
Replan         Finalize
```

Coordinator 的核心价值从“按固定阶段调用节点”升级为“根据当前任务状态和 Agent 返回结果动态决定下一步”。

## 3.2 Local Loop：每个 Agent 自己的 Agent Loop

统一范式：

```text
Task
 ↓
Build Initial Plan
 ↓
Choose Next Action
 ↓
Tool Call
 ↓
Observation
 ↓
Update Plan
 ↓
Goal satisfied?
 ├── No → Continue / Replan
 └── Yes → Final Artifact
```

所有 Agent 必须满足：

```text
Plan → Act → Observe → Replan → Act → ... → Final
```

而不是：

```text
input → one function → output
```

---

# 4. Loop Contract 设计

## 4.1 不记录 Chain-of-Thought

不要把模型原始思维过程存入 Trace。

只记录结构化、可审计 planning metadata：

```python
AgentPlanState(
    objective="verify SEC-SQL-CONCAT",
    subgoals=[
        "locate changed SQL construction",
        "trace input source",
        "confirm parameterization",
    ],
    completed=["locate changed SQL construction"],
    next_action="trace_data_flow",
    revision_reason="initial evidence insufficient",
)
```

允许记录：
- objective
- subgoal
- next action
- reason code
- confidence
- observation summary
- plan version

禁止依赖：
- unrestricted hidden rationale
- raw chain-of-thought

## 4.2 Agent Loop action 保持最小化

当前 `AgentLoop` 已支持：

```text
tool
final
```

第一阶段不建议加入复杂的：

```text
delegate
handoff
spawn_agent
```

Coordinator 的 A2A 调用先包装成 governed tool：

```text
delegate_agent()
```

例如：

```python
{
    "action": "tool",
    "tool": "delegate_agent",
    "arguments": {
        "agent_id": "security-agent",
        "task_type": "review.security",
        "objective": "...",
        "context_refs": [...]
    }
}
```

Tool 内部：

```text
AgentRegistry
→ choose Agent
→ A2A Transport
→ task.submit
→ task.get
→ artifact.list
→ return Observation
```

这样无需立即重写 `AgentLoop` protocol。

---

# 5. TaskGraph 设计

新增：

```python
@dataclass
class AgentTaskNode:
    node_id: str
    task_type: str
    target_capabilities: list[str]
    objective: str
    dependencies: list[str]
    status: str
    attempt: int
    artifact_ids: list[str]
```

以及：

```python
@dataclass
class CoordinatorTaskGraph:
    graph_id: str
    nodes: dict[str, AgentTaskNode]
    revision: int
```

初始图可能是：

```text
Security ────┐
             ├── Critic ── Verifier ── Fix(optional)
Reliability ─┘
```

但不能写死。

例如只有 security-sensitive diff：

```text
Security → Critic → Verifier
```

普通低风险 diff：

```text
Reliability → Verifier
```

Critic 发现安全证据不足：

```text
Security
  ↓
Critic
  ↓
Security-Recheck
  ↓
Verifier
```

Verifier 发现 patch 失败：

```text
Fix-v1
 ↓
Verifier
 ↓
Fix-v2
```

---

# 6. A2A 通信拓扑

第一版采用 **Hub-and-Spoke**，不要 Full Mesh。

```text
                 Coordinator
       ┌────────────┼────────────┐
       ↓            ↓            ↓
   Security    Reliability     Critic
       │            │            │
       └────────────┼────────────┘
                    ↓
                 Verifier
                    ↓
                   Fix
```

逻辑上所有任务都由 Coordinator 路由。

不要让 Agent 任意形成直接 mesh 通信。如果 Critic 需要 Security 重查：

```text
Critic
→ ReplanRequest Artifact
→ Coordinator
→ A2A task
→ Security
```

优点：
- 避免 Agent 环路；
- 权限集中；
- 全部通信可审计；
- Coordinator 拥有全局状态；
- 更容易 Replay；
- 更适合 Evolution Harness 做 attribution。

---

# 7. A2A Task Type 扩展

保留当前 `A2ATask` schema，先不修改 wire-level model。

增加 task types：

```text
review.security
review.reliability
critique.findings
verify.findings
fix.generate
```

对应 Artifact：

```text
security-findings
reliability-findings
critique-report
verification-report
fix-patch
```

Critic 输出至少包含：

```text
accepted_findings
rejected_findings
questions
missing_evidence
replan_requests
```

Verifier 输出至少包含：

```text
finding_id
verified
evidence
verification_method
confidence
failure_reason
```

Fix 输出至少包含：

```text
patch
changed_files
verification
test_results
risk_summary
```

---

# 8. AgentCard 扩展

当前已知 Remote Agent 主要是 Security / Reliability。扩展：

```python
_KNOWN = {
    "security-agent": ...,
    "reliability-agent": ...,
    "critic-agent": ...,
    "verifier-agent": ...,
    "fix-agent": ...,
}
```

建议 capability：

```text
Security:
code-review
security-review
static-analysis
semantic-analysis

Reliability:
code-review
reliability-review
regression-analysis
test-analysis

Critic:
finding-critique
conflict-detection
review-reflection

Verifier:
finding-verification
evidence-validation
test-execution

Fix:
patch-generation
repair-verification
safe-fix
```

Coordinator 不需要注册为 Remote Agent；它是主进程 orchestration root。

---

# 9. Phase 0：冻结当前基线

## 步骤

1. 创建 `feature/six-agent-loop`。
2. 冻结 `evaluation_data/pr_diff_100.jsonl`、SHA-256、split 和 matcher。
3. 保存 Single / Legacy Multi / Current Harness / Self-Evolved 当前结果。
4. 保存 A2A local / HTTP remote / fallback / retry / timeout / breaker baseline。
5. 新增架构开关：

```text
EVOAGENT_AGENT_ARCHITECTURE=legacy
EVOAGENT_AGENT_ARCHITECTURE=six-agent
```

默认 `legacy`。

## 验收
- 原 CI 全过；
- 100-case benchmark 可复现；
- A2A HTTP remote 可复现；
- legacy 模式行为完全不变。

---

# 10. Phase 1：建立统一 LoopAgent 抽象

新增：

```text
evoagent/loop_agents/
├── __init__.py
├── base.py
├── models.py
├── stepper.py
└── tools.py
```

接口：

```python
class BaseLoopAgent:
    agent_id: str
    capabilities: tuple[str, ...]

    def build_initial_state(self, task): ...
    def agent_step(self, state): ...
    def build_artifact(self, result): ...
    def run(self, task): ...
```

`run()` 统一调用当前 `AgentLoop.run()`。

统一 State：

```text
task
objective
plan
observations
artifacts
messages
budget
loop_step
confidence
```

硬要求：第 N+1 步必须能看到第 N 步 Observation。

## 验收
- 2-step loop；
- no-progress；
- step budget；
- tool-call budget；
- timeout；
- invalid action fail-fast；
- trace 中存在 plan revision 和 observation。

---

# 11. Phase 2：Coordinator Agent 化

新增：

```text
evoagent/loop_agents/coordinator.py
```

将：

```text
MultiAgentCoordinator + PlannerAgent
```

逐步迁移为：

```text
CoordinatorAgent
```

原 `MultiAgentCoordinator` 暂时作为 legacy adapter 保留。

## Coordinator Tools

```text
inspect_diff
profile_risk
discover_agents
delegate_agent
get_agent_task
get_agent_artifacts
cancel_agent_task
evaluate_coverage
compare_findings
```

A2A 调用必须经过 `GovernedToolRegistry`。

## 自规划

Initial State：

```text
diff
repository
risk profile
available AgentCards
execution policy
previous artifacts
```

不再默认对所有 Specialist 生成 assignment，而是生成 TaskGraph。

## Result-driven Replan 触发器

至少实现：

```text
Agent failure
Coverage gap
Finding conflict
Low confidence
Missing evidence
Budget pressure
```

示例：

```text
Security returns finding
→ Critic says evidence insufficient
→ Coordinator revises graph
→ Security-Recheck
→ Verifier
```

而不是只在 Specialist 报错时 Replan。

## Hard bounds

```text
max_global_steps
max_replans
max_agent_tasks
max_parallel_tasks
max_total_tool_calls
max_wall_time
```

全部进入 `ExecutionPolicy`。

---

# 12. Phase 3：Security Agent

新增：

```text
evoagent/loop_agents/security.py
services/security_agent/
```

现有 `SecurityRuleReviewer` 改为 Tool，而不是删除。

第一版 Tools：

```text
security_rule_scan
semantic_scan
read_changed_context
```

后续：

```text
search_symbol
trace_source_sink
inspect_dependency
```

示例 Loop：

```text
security_rule_scan
→ possible SQL injection
→ trace_source_sink
→ source→sink confirmed
→ final security finding
```

输出 `security-findings` A2A Artifact。

## 验收
- one-shot compatible；
- two-step reasoning；
- tool failure recovery；
- no-progress；
- timeout；
- HTTP A2A；
- local fallback；
- artifact schema validation。

---

# 13. Phase 4：Reliability Agent

新增：

```text
evoagent/loop_agents/reliability.py
services/reliability_agent/
```

现有 `ReliabilityRuleReviewer` 改为 Tool。

第一版 Tools：

```text
reliability_rule_scan
semantic_scan
run_targeted_test
```

后续：

```text
inspect_exception_path
inspect_logging
inspect_test_coverage
```

示例：

```text
detect empty exception
→ inspect_exception_path
→ run targeted test
→ confirm silent failure
→ final finding
```

---

# 14. Phase 5：Critic + Reflection 合并为 Critic Agent

新增：

```text
evoagent/loop_agents/critic.py
services/critic_agent/
```

将：

```text
CriticAgent.challenge()
ReflectionAgent.reflect()
```

转为 Critic Agent 内部 deterministic tools。

Tools：

```text
check_changed_line
check_evidence_match
check_explanation_quality
check_fix_actionability
compare_peer_findings
find_conflict
```

Loop：

```text
Receive findings
→ Check evidence
→ Missing evidence?
→ Reflect
→ Produce ReplanRequest
→ Final critique artifact
```

Critic 不直接调用 Security；只向 Coordinator 发 `ReplanRequest`。

---

# 15. Phase 6：Evidence + Verifier 合并为 Verifier Agent

新增：

```text
evoagent/loop_agents/verifier.py
services/verifier_agent/
```

将当前：

```text
EvidenceAgent + VerifierAgent
```

整合为一个自治 Agent。

Tools：

```text
reproduce_changed_line
verify_rule_signature
semantic_verify
run_targeted_test
compile_check
inspect_evidence
cross_check_finding
```

Verifier 必须独立于原 Specialist 的结论。

---

# 16. Phase 7：Fix Agent

新增：

```text
evoagent/loop_agents/fix.py
services/fix_agent/
```

复用现有：

```text
FixAgent
SafeFixer
RepairVerifier
```

Tools：

```text
generate_deterministic_patch
generate_ast_patch
generate_model_patch
compile_patch
run_patch_tests
inspect_patch_diff
measure_patch_scope
publish_draft_fix
```

其中 `publish_draft_fix` 必须继续经过 Policy / Approval / Audit。

Loop：

```text
Verified Finding
→ Plan repair
→ Generate patch
→ Compile
→ Fail? Replan
→ Test
→ Fail? Replan
→ Inspect patch scope
→ Final Fix Artifact
```

发布与生成分离：

```text
Fix Agent
→ verified patch artifact
→ Harness Safety Gate
→ optional publish_draft_fix
```

绝不直接写 PR head。

---

# 17. Phase 8：A2A 从 Reviewer Adapter 升级为 Generic Agent Client

当前：

```text
RemoteReviewerAdapter(Reviewer)
```

仍偏 review-specific。

新增：

```text
evoagent/a2a/client.py
```

接口：

```python
class RemoteAgentClient:
    def submit(self, task): ...
    def get(self, task_id): ...
    def artifacts(self, task_id): ...
    def cancel(self, task_id): ...
```

保留 `RemoteReviewerAdapter` 兼容旧代码。

新 Coordinator 使用 `RemoteAgentClient`，这样 Critic / Verifier / Fix 都能复用相同 A2A substrate。

---

# 18. Phase 9：A2A Task Lifecycle 异步化

多步骤 Agent 服务化后，应从：

```text
task.submit
→ 同步 reviewer
→ COMPLETED
```

升级为：

```text
task.submit
→ PENDING
→ enqueue
→ return task id

worker
→ RUNNING
→ AgentLoop
→ COMPLETED / FAILED

Coordinator
→ task.get
→ artifact.list
```

## TaskStore

抽象：

```python
class A2ATaskStore:
    create()
    update_status()
    save_checkpoint()
    save_artifact()
    get()
```

实现：

```text
InMemoryA2ATaskStore
SqliteA2ATaskStore
PostgresA2ATaskStore
```

生产模式不得只依赖内存。

## 真取消

```text
task.cancel
→ Cancellation Token
→ AgentLoop cancel_check
→ Tool cancel
→ CANCELLED
```

必须防止取消后 worker 又写 `COMPLETED`。

第一阶段继续 polling，不同时引入 Kafka / NATS / SSE / WebSocket。

---

# 19. Phase 10：统一 Agent Service Host

将 `AgentServiceHost` 从：

```text
Reviewer → A2A Service
```

升级为：

```text
BaseLoopAgent → A2A Service
```

同时保留 `ReviewerAgentAdapter` 兼容。

执行路径：

```text
A2ATask
→ BaseLoopAgent.run()
→ AgentLoop
→ A2AArtifact
```

---

# 20. Phase 11：ReviewService 接入六 Agent

当前 Security / Reliability 已可通过 A2A Remote 接入，继续扩展：

```text
EVOAGENT_A2A_ENDPOINTS=
security,reliability,critic,verifier,fix
```

启动时：

```text
discover
→ register AgentCard
→ validate protocol
→ validate capability
→ health check
→ Coordinator snapshot
```

本地开发：

```text
Coordinator
├── InProcessA2A Security
├── InProcessA2A Reliability
├── InProcessA2A Critic
├── InProcessA2A Verifier
└── InProcessA2A Fix
```

生产：

```text
review-service
security-agent
reliability-agent
critic-agent
verifier-agent
fix-agent
```

通过 HTTP JSON-RPC。

---

# 21. Phase 12：Tool Governance 按 Agent 隔离

### Security
允许 read/search/scanner/semantic；禁止 repo write / publish。

### Reliability
允许 read/test/compile/static analysis。

### Critic
原则上 read-only。

### Verifier
允许 read/test/compile/sandbox execute。

### Fix
允许 read/temp-write/sandbox/compile/test/patch；发布单独授权。

所有 Tool 调用携带：

```text
agent_id
task_id
tenant_id
repository
correlation_id
```

---

# 22. Phase 13：Context Engineering

不能所有 Agent 拿完整 PR 上下文。

### Security
```text
risk-sensitive files
dependency context
auth/dataflow code
security artifacts
```

### Reliability
```text
exception paths
tests
logging
runtime/config
```

### Critic
```text
finding
evidence
minimum supporting context
```

### Verifier
```text
finding
critique
evidence refs
test/semantic context
```

### Fix
```text
verified findings only
target files
verification constraints
```

原则：**最小充分上下文**。

---

# 23. Phase 14：Memory Engineering

三层：

### Task-local Working Memory
```text
observations
plan revisions
artifacts
tool results
```

### Shared Task Memory
Coordinator 持有：

```text
TaskGraph
Artifact refs
Decision state
```

### Long-term Memory
只写入：

```text
confirmed outcome
accepted experience
validated skill
validated policy evidence
```

未验证 Finding 禁止自动写长期记忆。

---

# 24. Phase 15：Harness 与 Agent 边界

最终 Harness 只负责：

```text
Task lifecycle
ExecutionPolicy
Budget
Recovery
Security
Checkpoint
Trace
Replay
Final arbitration
```

Agent 负责：

```text
Planning
Tool selection
Observation
Replanning
Artifact generation
```

原则：

> **Agent decides what to do next; Harness decides what it is allowed to do.**

---

# 25. Phase 16：Deterministic Arbiter

建议将当前 `ArbiterAgent` 重定位/重命名为：

```text
FindingArbiter
FinalDecisionGate
```

避免被误解为第 7 个自治 Agent。

输入：

```text
Specialist Artifacts
Critic Artifact
Verifier Artifact
Fix Artifact(optional)
ExecutionPolicy
```

输出：

```text
accepted_findings
rejected_findings
approved_fix
decision_reason_codes
```

必须 deterministic。

---

# 26. Phase 17：Self-Evolution 与六 Agent 接轨

Evolution 仍位于 Harness 层，但 evolution unit 改为 agent-specific：

```text
security-agent.prompt@v3
security-agent.skill.sql-taint@v2
critic-agent.policy@v4
verifier-agent.prompt@v2
fix-agent.repair-strategy@v5
coordinator.planner@v3
```

## Attribution

每条 Outcome 关联：

```text
coordinator plan version
agent id
agent version
prompt version
skill versions
tools
artifacts
verification result
final outcome
```

这样才能区分：

```text
PLANNING_MISS
ROUTING_MISS
SPECIALIST_MISS
CRITIC_FALSE_REJECTION
VERIFIER_FALSE_REJECTION
FIX_FAILURE
TOOL_FAILURE
A2A_FAILURE
CONTEXT_FAILURE
BUDGET_FAILURE
```

## Candidate

例如：

```text
3 次 PLANNING_MISS
→ Coordinator planner candidate

多次 SQL 漏检
→ Security skill candidate

Verifier 误接收 FP
→ Verifier policy/prompt candidate
```

仍然必须：

```text
Candidate
→ Replay
→ Holdout
→ Safety Gate
→ Freeze
→ Canary
→ Promote / Rollback
```

任何 Agent 不得自行部署候选。

---

# 27. Phase 18：Evaluation Harness V4

建议比较：

| Variant | 含义 |
|---|---|
| A | Single Agent baseline |
| B | 当前 staged Multi-Agent |
| C | 6-Agent Local/InProcess A2A |
| D | 6-Agent HTTP A2A |
| E | 6-Agent + Self-Evolution Candidate |

保留 frozen 100-case synthetic-controlled benchmark：

```text
Precision
Recall
F1
High-risk recall
Clean accuracy
Critical misses
```

## 新增 Loop 指标

Coordinator：

```text
TaskGraph revisions
replan count
delegated tasks
unnecessary delegations
coverage completion
```

Agent：

```text
average loop steps
tool calls
successful replans
no-progress rate
budget exhaustion rate
```

A2A：

```text
remote success
timeout
retry
fallback
circuit open
task latency
artifact latency
```

Collaboration：

```text
critic overturn rate
verifier rejection rate
specialist disagreement
fix retry count
```

## 动态案例

新增：

```text
evaluation_data/agent_loop_scenarios.jsonl
```

至少覆盖：

1. Specialist 首轮证据不足；
2. Critic 请求补证；
3. Verifier conflict；
4. Security timeout；
5. Reliability unavailable；
6. Fix compile fail→replan→success；
7. Fix test failure；
8. repeated tool no-progress；
9. context overflow；
10. budget exhaustion。

Remote Variant 必须真实走 HTTP，不能用 InProcess transport 代替。

---

# 28. Phase 19：Failure Injection

必须覆盖：

```text
HTTP timeout
HTTP 500
malformed JSON-RPC
invalid artifact
AgentLoop timeout
AgentLoop no-progress
tool failure
tool timeout
Agent crash
Coordinator crash
TaskStore restart
cancel during RUNNING
duplicate submit
stale artifact
wrong correlation_id
```

Invariant：

```text
失败不能绕过 Arbiter
失败不能绕过 Safety Gate
失败不能自动 Promote evolution candidate
```

---

# 29. Phase 20：Observability

每条 trace 至少：

```text
trace_id
task_id
correlation_id
parent_task_id
agent_id
agent_version
loop_step
plan_revision
tool_name
a2a_target
artifact_id
policy_id
```

Span：

```text
review
└── coordinator.loop
    ├── coordinator.plan
    ├── a2a.security
    │   └── security.loop
    │       ├── tool.rule_scan
    │       └── tool.semantic_scan
    ├── a2a.critic
    │   └── critic.loop
    ├── a2a.verifier
    │   └── verifier.loop
    └── a2a.fix
        └── fix.loop
```

---

# 30. Phase 21：CI

新增：

```text
six-agent-unit
six-agent-loop-contract
six-agent-a2a-contract
six-agent-local-integration
six-agent-http-integration
six-agent-failure-injection
six-agent-eval-v4
six-agent-evolution-regression
```

Hard assertions：

```text
legacy benchmark no regression
critical misses not increase
holdout non-regression
HTTP remote actually executed
A2A fallback attribution correct
trace coverage = 100%
artifact correlation coverage = 100%
```

---

# 31. Phase 22：Docker Compose

最终：

```text
review-service
security-agent
reliability-agent
critic-agent
verifier-agent
fix-agent
postgres
redis(optional)
```

第一版不要求 Kubernetes。

目标：

```bash
docker compose up
```

可运行真实跨进程六 Agent workflow。

---

# 32. Phase 23：Legacy 迁移

必须双轨：

```text
Legacy
↓
Six-Agent Shadow
↓
Canary
↓
Default Six-Agent
↓
Legacy compatibility only
↓
Remove obsolete nodes
```

不要一次删除旧：

```text
PlannerAgent
CriticAgent
ReflectionAgent
EvidenceAgent
VerifierAgent
FixAgent
```

先将其转换为新 Agent 内部 deterministic Tool / compatibility layer。

---

# 33. 推荐目录

```text
evoagent/
├── loop_agents/
│   ├── base.py
│   ├── models.py
│   ├── coordinator.py
│   ├── security.py
│   ├── reliability.py
│   ├── critic.py
│   ├── verifier.py
│   └── fix.py
│
├── a2a/
│   ├── models.py
│   ├── protocol.py
│   ├── transport.py
│   ├── http_transport.py
│   ├── inprocess_transport.py
│   ├── client.py
│   ├── service.py
│   ├── registry.py
│   ├── resilience.py
│   └── governance.py
│
├── runtime.py
├── harness.py
├── evolution_controller.py
└── ...
```

服务：

```text
services/
├── security_agent/
├── reliability_agent/
├── critic_agent/
├── verifier_agent/
└── fix_agent/
```

Coordinator 留在主 `review-service`。

---

# 34. 六 Agent 最终职责边界

### Coordinator
做：understand / plan / delegate / observe / replan / finalize。  
不做：直接安全扫描、直接测试、直接修代码。

### Security
做：security analysis / security tools / evidence。  
不做：最终裁决和直接发布修复。

### Reliability
做：reliability / regression / runtime/test analysis。

### Critic
做：challenge / reflect / find gaps / request replan。  
不做：最终裁决。

### Verifier
做：independent verification / reproduction / evidence validation。

### Fix
做：patch / compile / test / repair replan。  
不做：绕过 Harness 发布。

---

# 35. 推荐实施顺序

严格按：

```text
0 Baseline freeze
↓
1 Common LoopAgent abstraction
↓
2 Coordinator Agent
↓
3 Security Agent
↓
4 Reliability Agent
↓
5 Critic Agent
↓
6 Verifier Agent
↓
7 Fix Agent
↓
8 Generic A2A Client
↓
9 Async A2A lifecycle
↓
10 ReviewService integration
↓
11 Tool governance
↓
12 Context / Memory
↓
13 Harness boundary
↓
14 Evolution attribution
↓
15 Evaluation V4
↓
16 CI / Docker
↓
17 Shadow / Canary migration
```

每阶段：

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
Architecture Review
↓
Next Phase
```

---

# 36. 最重要的 10 个验收标准

1. 6 个核心 Agent 都有自己的 Agent Loop。
2. Agent 后续决策确实依赖前序 Observation。
3. Coordinator 能基于结果修改 TaskGraph，不只是失败替换。
4. Security / Reliability / Critic / Verifier / Fix 都能经 A2A 调用。
5. HTTP Remote 模式真实经过网络 Transport。
6. Fix 能完成 `patch → test → failure → replan → patch`。
7. Arbiter / Safety Gate 保持 deterministic。
8. Evolution 仍经过 Replay / Holdout / Canary / Rollback。
9. Agent / Tool / A2A 行为全部可 trace attribution。
10. Frozen benchmark 无不可解释退化。

---

# 37. 最终架构层级

改造前：

```text
Fixed Stage Workflow
+ Dynamic Specialist Routing
+ Partial Agent Loop
+ A2A Remote Specialists
```

改造后：

```text
Goal
↓
Coordinator Autonomous Planning Loop
↓
Dynamic TaskGraph
↓
A2A Agent Delegation
↓
Independent Specialist Agent Loops
↓
Critic / Verification Agent Loops
↓
Repair Agent Loop
↓
Deterministic Harness Arbitration
↓
Production Outcome
↓
Harness-level Evolution Loop
```

即：

> **Global Agent Loop + Local Agent Loops + Governed A2A + Deterministic Harness + Closed-loop Evolution**

---

# 38. 一句话目标架构

```text
Coordinator 负责“下一步应该让谁做什么”；
各 Agent 负责“如何把自己的任务做完”；
A2A 负责“Agent 之间如何交换任务与产物”；
Harness 负责“它们允许做什么、失败怎么办、最终是否采纳”；
Evolution Harness 负责“下一次如何做得更好”。
```
