# EvoReview-Agent：Harness 与 Self-Evolution 深化实施计划

> 项目定位：以 Code Review 为真实业务载体，重点展示 **Agent Runtime / Harness Engineering / Controlled Self-Evolution** 能力，而不是单纯追求代码审查规则数量或模型效果。
>
> 目标形态：
>
> **Policy-driven Agent Runtime + Replayable Harness + Failure Recovery + Procedure Skill Evolution + Runtime Policy Evolution + Evidence-driven Evolution Governance**

---

# 1. 当前项目能力基线

EvoReview-Agent 已经具备较完整的 Agent 与自进化基础，当前主要能力包括：

- 自研 `AgentRuntime`
  - 最大执行步数控制
  - Timeout
  - Retry
  - Cancel
  - Checkpoint
  - Resume
  - Event Sink
- `ToolRegistry`
  - 工具注册
  - 参数 Schema 校验
  - Observation 回传
- `AgentLoop`
  - Plan / Tool / Observe / Final 闭环
- `ReviewHarness`
  - 状态持久化
  - Checkpoint 恢复
  - Review 流程编排
- Multi-Agent 协作
  - Planner
  - Specialist
  - Critic
  - Reflection
  - Evidence
  - Verifier
  - Arbiter
- Memory
  - Working
  - Episodic
  - Semantic
  - Procedural
- Evolution Controller
  - Evolution Job
  - Lease
  - Retry
  - Pause / Resume
  - Crash Recovery
- Evolution Gate
  - Validation
  - Holdout
  - Catastrophic Forgetting
  - Generalization
  - Shadow
  - Canary
  - Rollback
- Prompt / Rule Skill Evolution

当前已经不是简单的“LLM + Prompt + Tools”项目，而是具备较完整 Agent 基础设施的工程项目。

但如果希望在简历和面试中突出：

> **我真正做深了 Harness Engineering 和 Self-Evolution**

下一阶段重点不应继续堆业务规则，而应深入 Runtime Governance 与 Evolution Governance。

---

# 2. 最终目标架构

建议最终演进为以下闭环：

```text
                    ┌────────────────────┐
                    │      User Task     │
                    └─────────┬──────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │    Risk Profiler   │
                    └─────────┬──────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │    Harness Policy Engine │
                 │                          │
                 │  Agent Topology          │
                 │  Tool Permission         │
                 │  Step / Token Budget     │
                 │  Retry Policy            │
                 │  Verification Policy     │
                 └────────────┬─────────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │   Agent Runtime    │
                    │ Plan/Tool/Observe  │
                    └─────────┬──────────┘
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
          Trace           Tool Call         Failure
             │                │                │
             └────────────────┼────────────────┘
                              ▼
                    ┌────────────────────┐
                    │ Experience Store   │
                    └─────────┬──────────┘
                              ▼
                       Reflection
                              ▼
                       Hypothesis
                              ▼
                   Candidate Generator
                  /          |           \
                 /           |            \
                ▼            ▼             ▼
             Prompt      Procedure      Runtime
            Candidate      Skill         Policy
                 \           |             /
                  \          |            /
                   └─────────┼───────────┘
                             ▼
                      Replay Harness
                             ▼
                 Validation / Holdout
                             ▼
                 Regression / Safety
                             ▼
                    Shadow / Canary
                             ▼
                         Activate
                             ▼
                   Production Outcome
                             │
                             └──────────────► Experience
```

这个闭环应成为项目最核心的技术叙事。

---

# 3. 总体实施优先级

| 阶段 | 模块 | 优先级 | 核心价值 |
|---|---|---:|---|
| Phase 1 | Runtime Policy Model | P0 | Harness |
| Phase 2 | Policy-driven Runtime | P0 | Harness |
| Phase 3 | Failure Taxonomy & Recovery | P0 | Harness |
| Phase 4 | Replay Harness | P0 | Harness + Evolution |
| Phase 5 | Procedure Skill Evolution | P1 | Self-Evolution |
| Phase 6 | Runtime Policy Evolution | P1 | Self-Evolution |
| Phase 7 | Evolution Attribution & Lineage | P1 | Evolution Governance |
| Phase 8 | Benchmark / CI / Resume Packaging | P1 | 项目可信度 |

建议严格按照顺序实施。

原因是：

```text
Runtime Policy
    ↓
Failure Recovery
    ↓
Replay
    ↓
Procedure Evolution
    ↓
Policy Evolution
```

后面的能力依赖前面的 Harness 基础。

---

# 4. Phase 1：建立统一 Runtime Policy Model

## 4.1 目标

将当前散落在配置文件和 Runtime 中的：

```text
max_steps
timeout
retry
agent selection
tool permission
verification depth
```

统一抽象成：

```text
ExecutionPolicy
```

从“静态配置驱动 Runtime”升级为：

> **Policy-driven Runtime**

---

## 4.2 新增目录

建议新增：

```text
evoagent/policy/
├── __init__.py
├── models.py
├── risk.py
├── resolver.py
├── tool_policy.py
├── agent_policy.py
└── defaults.py
```

---

## 4.3 ExecutionPolicy 数据结构

在：

```text
evoagent/policy/models.py
```

新增：

```python
@dataclass
class ExecutionBudget:
    max_steps: int
    max_tokens: int | None
    max_cost_usd: float | None
    max_wall_time_seconds: float
    max_tool_calls: int


@dataclass
class RetryPolicy:
    max_retries: int
    backoff_seconds: float
    exponential_backoff: bool
    retryable_failures: set[str]


@dataclass
class VerificationPolicy:
    critic_required: bool
    evidence_required: bool
    verifier_required: bool
    sandbox_required: bool
    minimum_confidence: float


@dataclass
class AgentPolicy:
    enabled_agents: list[str]
    max_parallel_agents: int
    fallback_agents: list[str]


@dataclass
class ToolPermission:
    tool_name: str
    allow: bool
    max_calls: int | None
    requires_sandbox: bool
    requires_approval: bool


@dataclass
class ExecutionPolicy:
    policy_id: str
    policy_version: int

    risk_level: str

    budget: ExecutionBudget
    retry: RetryPolicy
    verification: VerificationPolicy
    agents: AgentPolicy

    tool_permissions: list[ToolPermission]

    metadata: dict[str, Any]
```

---

## 4.4 风险模型

新增：

```text
evoagent/policy/risk.py
```

实现：

```python
class RiskProfile:
    level: Literal["low", "medium", "high", "critical"]
    score: float
    reasons: list[str]
```

风险评分初期不需要 LLM。

建议先做 deterministic risk profiler：

### 文件维度

高风险目录：

```text
auth/
security/
payment/
billing/
permission/
oauth/
crypto/
deployment/
infra/
database/
```

### Diff 维度

关注：

```text
authentication
authorization
SQL
shell
subprocess
deserialization
credential
permission
network
dependency
config
```

### 修改规模

例如：

```text
changed_files > 10
changed_lines > 500
```

风险上升。

最终：

```python
risk = risk_profiler.profile(parsed_diff)
```

---

## 4.5 默认 Policy

建议定义：

### Low Risk

```yaml
max_steps: 3
max_tool_calls: 5

agents:
  - reliability

verification:
  critic: false
  evidence: false
  verifier: false
```

### Medium Risk

```yaml
max_steps: 6
max_tool_calls: 12

agents:
  - reliability
  - semantic

verification:
  critic: true
  evidence: false
  verifier: true
```

### High Risk

```yaml
max_steps: 10
max_tool_calls: 25

agents:
  - security
  - reliability
  - semantic

verification:
  critic: true
  evidence: true
  verifier: true
  sandbox: true
```

---

## 4.6 Policy Resolver

新增：

```text
policy/resolver.py
```

核心：

```python
class PolicyResolver:

    def resolve(
        self,
        task,
        risk_profile,
        tenant_config,
        repository_config,
    ) -> ExecutionPolicy:
        ...
```

优先级：

```text
System Default
     ↓
Tenant Override
     ↓
Repository Override
     ↓
Risk Profile
     ↓
Task Override
```

---

## 4.7 Runtime 接入

修改：

```text
evoagent/runtime.py
```

原本 Runtime：

```python
AgentRuntime(
    max_steps=...
    timeout=...
)
```

调整：

```python
runtime = AgentRuntime(
    execution_policy=policy
)
```

Runtime 所有限制统一从 Policy 读取。

---

## 4.8 验收测试

新增：

```text
tests/test_execution_policy.py
tests/test_risk_profiler.py
tests/test_policy_resolver.py
tests/test_runtime_policy_enforcement.py
```

必须覆盖：

- Low risk 自动降低 Agent 数量
- High risk 强制 Evidence
- Tool 超过 max_calls 被拒绝
- Step budget 被严格执行
- Tenant override 生效
- Repository override 生效
- 非法 Policy 无法启动 Runtime

---

## 4.9 Phase 1 完成标准

达到以下结果才能进入 Phase 2：

```text
所有 Runtime 核心参数不再直接散落读取 Config
```

而是统一：

```text
Task
→ RiskProfile
→ ExecutionPolicy
→ Runtime
```

---

# 5. Phase 2：Tool Governance

## 5.1 目标

把当前：

```text
ToolRegistry
```

升级成：

```text
Tool Governance Layer
```

Harness 不只是知道“有哪些工具”，还必须决定：

> 哪个 Agent、在什么任务、什么风险级别、什么预算下，可以调用哪个工具。

---

# 5.2 Tool Metadata

扩展 Tool 定义：

```python
@dataclass
class ToolMetadata:
    name: str

    risk_level: str

    side_effect: bool
    idempotent: bool
    retryable: bool

    requires_sandbox: bool
    requires_approval: bool

    timeout_seconds: float

    allowed_agents: list[str]

    tenant_scoped: bool
```

例如：

```python
read_file:
    risk = low
    side_effect = false
    idempotent = true


run_tests:
    risk = medium
    side_effect = false
    requires_sandbox = true


push_fix:
    risk = high
    side_effect = true
    idempotent = false
    requires_approval = true
```

---

# 5.3 Tool Policy Engine

新增：

```text
evoagent/policy/tool_policy.py
```

接口：

```python
class ToolPolicyEngine:

    def authorize(
        self,
        agent_id,
        tool_name,
        arguments,
        execution_policy,
        runtime_state,
    ) -> ToolDecision:
        ...
```

返回：

```python
@dataclass
class ToolDecision:
    allowed: bool
    reason: str | None
    requires_sandbox: bool
    requires_approval: bool
```

---

# 5.4 Tool Invocation Pipeline

最终统一走：

```text
Agent
 ↓
Tool Request
 ↓
Schema Validation
 ↓
Tool Policy Engine
 ↓
Budget Check
 ↓
Sandbox / Approval
 ↓
Timeout Guard
 ↓
Tool Execution
 ↓
Observation Sanitizer
 ↓
Audit Log
 ↓
Agent
```

---

# 5.5 Side-effect 防护

一定要区分：

```text
read-only tool
side-effect tool
```

对非幂等 Side-effect Tool：

```text
禁止自动 retry
```

需要：

```text
Execution ID
Idempotency Key
Invocation State
```

状态：

```text
REQUESTED
AUTHORIZED
RUNNING
SUCCEEDED
FAILED
UNKNOWN
```

对于：

```text
FAILED / UNKNOWN
```

不得直接二次执行。

---

# 5.6 Tool Circuit Breaker

新增简单 Circuit Breaker：

```text
CLOSED
OPEN
HALF_OPEN
```

例如：

```text
连续 5 次 timeout
→ OPEN

60 秒后
→ HALF_OPEN

成功
→ CLOSED
```

Tool 级别维护：

```python
failure_count
last_failure_at
open_until
```

---

# 5.7 Tool Audit

建议记录：

```text
task_id
agent_id
tool_name
arguments_hash
authorized
deny_reason
started_at
finished_at
latency_ms
status
observation_hash
side_effect
```

---

# 5.8 测试

新增：

```text
tests/test_tool_policy.py
tests/test_tool_budget.py
tests/test_tool_circuit_breaker.py
tests/test_side_effect_tool.py
```

验收：

- 未授权 Agent 无法执行 Tool
- Low Risk Policy 禁止高风险 Tool
- Tool 超预算后停止
- 非幂等工具失败后不自动 retry
- Circuit Breaker 正确开启
- 所有调用存在 Audit

---

# 6. Phase 3：Failure Taxonomy 与 Recovery Policy

## 6.1 目标

将当前：

```text
Exception → retry
```

升级为：

```text
Failure
→ Classification
→ Recovery Strategy
```

---

# 6.2 Failure Taxonomy

新增：

```text
evoagent/recovery/
├── __init__.py
├── failures.py
├── classifier.py
├── planner.py
├── executor.py
└── compensation.py
```

定义：

```python
class FailureType(Enum):

    MODEL_TIMEOUT
    MODEL_RATE_LIMIT
    MODEL_INVALID_OUTPUT
    MODEL_CONTEXT_OVERFLOW

    TOOL_TIMEOUT
    TOOL_UNAVAILABLE
    TOOL_INVALID_ARGUMENT
    TOOL_PERMISSION_DENIED
    TOOL_SIDE_EFFECT_UNKNOWN

    AGENT_INVALID_STATE
    AGENT_NO_PROGRESS
    AGENT_HALLUCINATION

    CHECKPOINT_FAILURE
    STORAGE_FAILURE

    BUDGET_EXCEEDED
    POLICY_VIOLATION

    UNKNOWN
```

---

# 6.3 RecoveryAction

```python
class RecoveryAction(Enum):

    RETRY
    RETRY_WITH_BACKOFF
    SWITCH_MODEL
    SWITCH_TOOL
    COMPRESS_CONTEXT
    REPLAN
    FALLBACK_AGENT
    SKIP
    COMPENSATE
    HUMAN_REVIEW
    ABORT
```

---

# 6.4 Recovery Planner

接口：

```python
RecoveryPlanner.plan(
    failure,
    execution_policy,
    runtime_state
)
```

例如：

### Model Timeout

```text
第一次
→ retry

第二次
→ fallback model

仍失败
→ abort
```

### Context Overflow

```text
compress context
→ retry
```

### Specialist Failure

```text
retry
→ fallback specialist
→ planner replan
```

### Tool Timeout

```text
retryable read tool
→ retry

non-idempotent side-effect tool
→ human review
```

### Budget Exceeded

```text
禁止 retry
→ graceful stop
```

---

# 6.5 No-progress Detection

这是 Harness 很值得做的能力。

检测 Agent 是否陷入：

```text
search → search → search
```

或者：

```text
tool A
tool A
tool A
```

记录最近 N 个 Action：

```python
ActionFingerprint
```

若连续重复：

```text
same tool + same arguments
```

则判定：

```text
AGENT_NO_PROGRESS
```

Recovery：

```text
Reflection
→ Replan
```

仍无进展：

```text
Abort
```

---

# 6.6 Compensation

对于 Side-effect 工具建议支持：

```python
CompensationHandler
```

例如未来：

```text
create PR comment
→ rollback = delete/update comment

create branch
→ rollback = delete branch
```

不是所有工具都必须实现 compensation。

Metadata：

```python
compensatable: bool
compensation_tool: str | None
```

---

# 6.7 Failure Event

所有 Failure 统一记录：

```python
FailureEvent(
    failure_id,
    task_id,
    agent_id,
    node,
    failure_type,
    message,
    recoverable,
    recovery_action,
    attempt,
    resolved,
)
```

这些数据后面直接进入 Self-Evolution Experience。

---

# 6.8 验收

测试：

```text
tests/test_failure_classifier.py
tests/test_recovery_planner.py
tests/test_no_progress.py
tests/test_compensation.py
```

重点演示 Case：

```text
模型超时
→ retry
→ provider fallback
→ 成功
```

```text
context overflow
→ compress
→ retry
```

```text
specialist crash
→ replacement agent
```

```text
重复工具调用
→ no-progress
→ replan
```

这些都是面试中非常好展示的 Harness Case。

---

# 7. Phase 4：Replay Harness

这是整个项目下一阶段最重要的模块之一。

---

# 7.1 目标

支持：

```text
历史 Agent Task
→ Snapshot
→ Replay
→ Baseline/Candidate Compare
```

它将成为 Harness 与 Self-Evolution 的桥梁。

---

# 7.2 新增目录

```text
evoagent/replay/
├── __init__.py
├── models.py
├── recorder.py
├── snapshot.py
├── runner.py
├── comparator.py
├── fixtures.py
└── report.py
```

---

# 7.3 ReplaySnapshot

核心对象：

```python
@dataclass
class ReplaySnapshot:

    snapshot_id: str
    task_id: str

    repository: str
    commit_sha: str
    diff_hash: str

    prompt_version: str
    skill_versions: dict[str, str]
    policy_version: str

    model_name: str
    model_parameters: dict

    context_snapshot: dict
    memory_snapshot_ids: list[str]

    tool_observations: list[dict]

    expected_output: dict | None

    created_at: datetime
```

---

# 7.4 两种 Replay Mode

必须区分：

## Mode A：Deterministic Observation Replay

不真正再次运行工具：

```text
Tool Request
→ 查 Snapshot
→ 返回历史 Observation
```

适合：

```text
Prompt Candidate
Procedure Candidate
Runtime Policy Candidate
```

离线快速评测。

---

## Mode B：Live Tool Replay

重新调用只读工具：

```text
read_file
search_code
static analyzer
```

适合：

```text
真实环境验证
```

Side-effect Tool 默认：

```text
禁止 Live Replay
```

---

# 7.5 Replay Tool Adapter

新增：

```python
class ReplayToolRegistry(ToolRegistry):
```

工作逻辑：

```text
Agent requests tool
       ↓
fingerprint(tool + args)
       ↓
find recorded observation
       ↓
return snapshot observation
```

实现可重复实验。

---

# 7.6 Counterfactual Replay

Replay CLI：

```bash
python -m evoagent.replay \
  --task <task-id> \
  --prompt-version v12
```

或者：

```bash
python -m evoagent.replay \
  --dataset holdout.jsonl \
  --policy candidate-v4
```

支持替换：

```text
Prompt
Skill
Policy
Model
Agent topology
Context strategy
```

保持其他变量固定。

---

# 7.7 Comparator

输出至少：

```text
Finding Precision
Finding Recall
Finding F1

High-risk Recall
False Positive Rate

Tool Calls
Agent Steps
Token Usage
Estimated Cost

Latency

Failure Rate
Recovery Count

Verification Pass Rate
```

并产生：

```text
Baseline vs Candidate
```

---

# 7.8 Replay Report

示例：

```text
Candidate: procedure-auth-v3
Dataset: temporal-holdout-2026-08

Finding F1
0.801 → 0.842

High Risk Recall
0.910 → 0.947

False Positive Rate
0.083 → 0.081

Tool Calls
12.4 → 14.2

Latency
8.2s → 9.1s

Cost
$0.034 → $0.039

Decision:
PASS
```

---

# 7.9 Snapshot Persistence

建议新增存储表：

```text
replay_snapshots
replay_tool_observations
replay_runs
replay_metrics
replay_diffs
```

关键字段：

```text
snapshot_id
task_id
input_hash
prompt_version
skill_version
policy_version
model
created_at
```

---

# 7.10 Replay Acceptance Tests

新增：

```text
tests/test_replay_snapshot.py
tests/test_replay_tools.py
tests/test_replay_determinism.py
tests/test_replay_comparator.py
tests/test_counterfactual_replay.py
```

要求：

相同 Snapshot + 相同 Candidate：

```text
Tool Observation 完全一致
```

至少在 deterministic mode 下保证。

---

# 8. Phase 5：Procedure Skill Evolution

这是 Self-Evolution 从“规则变化”升级到“能力变化”的核心。

---

# 8.1 当前问题

当前 Rule Skill 本质更接近：

```text
pattern
→ finding
```

即：

```text
看到什么
```

下一阶段应该让系统学习：

```text
遇到某类任务应该如何调查
```

即：

```text
怎么做
```

---

# 8.2 Procedure Skill DSL

新增：

```text
evoagent/procedure/
├── __init__.py
├── schema.py
├── parser.py
├── validator.py
├── executor.py
└── registry.py
```

Skill 示例：

```yaml
name: auth-bypass-review
version: 3

trigger:
  paths:
    - "auth/**"
    - "security/**"

  keywords:
    - authentication
    - permission
    - token

risk_level:
  - medium
  - high

procedure:
  - tool: search_code
    args:
      query: "authorization"

  - tool: find_callers
    args:
      symbol_from: previous

  - check: permission_guard_exists

  - tool: find_tests
    args:
      symbol_from: previous

required_evidence:
  - source
  - security_guard
  - reachable_sink

budget:
  max_steps: 6
  max_tool_calls: 8
```

---

# 8.3 DSL 安全限制

这是非常重要的 Harness 设计。

禁止：

```text
任意 Python
eval
exec
shell
network
dynamic import
```

Procedure 只能：

```text
调用 ToolRegistry 中已授权的 Tool
```

即：

```text
Procedure Skill
≠ Code Plugin

Procedure Skill
= Restricted Agent Workflow DSL
```

---

# 8.4 Procedure Executor

执行：

```text
ProcedureSkill
 ↓
Schema Validate
 ↓
Tool Permission Check
 ↓
Execution Budget
 ↓
Step Executor
 ↓
Observation
 ↓
Condition Evaluate
 ↓
Next Step
```

每个 Step 都被 Harness 管控。

---

# 8.5 Evolution Source

从这些来源产生候选：

```text
Failure Cases
False Negative
Human Feedback
Critic Objections
Repeated Agent Trace
High-cost Task Trace
Successful Resolution Pattern
```

例如连续多次发现：

```text
security agent
经常：
search auth
→ find caller
→ inspect guard
→ inspect test
```

Reflection Engine 总结：

```text
这可能是一个可复用 Procedure
```

生成：

```text
ProcedureSkillCandidate
```

---

# 8.6 Procedure Candidate Pipeline

必须严格：

```text
Experience
 ↓
Reflection
 ↓
Hypothesis
 ↓
Procedure Candidate
 ↓
Schema Validation
 ↓
Static Safety Validation
 ↓
Replay Validation
 ↓
Holdout
 ↓
Generalization Gate
 ↓
Shadow
 ↓
Activate
```

---

# 8.7 Procedure Version

建议结构：

```python
ProcedureSkillVersion(
    skill_name,
    version,
    parent_version,
    status,
    source_hypothesis_id,
    content,
    created_at,
    activated_at,
)
```

状态：

```text
DRAFT
VALIDATED
SHADOW
ACTIVE
REJECTED
ROLLED_BACK
```

---

# 8.8 Procedure Skill 测试

新增：

```text
tests/test_procedure_schema.py
tests/test_procedure_validator.py
tests/test_procedure_executor.py
tests/test_procedure_budget.py
tests/test_procedure_evolution.py
```

重点验证：

- Skill 无法执行任意代码
- Skill 只能使用允许工具
- Tool budget 严格生效
- Replay 可以验证 Procedure
- Candidate 失败不会激活

---

# 9. Phase 6：Runtime Policy Evolution

这是整个项目最有辨识度的能力之一。

---

# 9.1 目标

让 Agent 系统不仅学习：

```text
Prompt / Rule / Procedure
```

还可以学习：

```text
如何运行 Agent 本身
```

也就是：

> **Harness Policy Evolution**

---

# 9.2 可进化 Policy 范围

初期只开放安全参数：

```text
agent routing
max_steps
max_tool_calls
verification depth
parallelism
fallback agent
tool allowlist
```

禁止自动修改：

```text
tenant isolation
auth
hard security boundary
side-effect approval
global safety policy
```

也就是说：

```text
Optimizable Policy
```

和：

```text
Immutable Safety Policy
```

必须分离。

---

# 9.3 Policy Candidate 示例

历史数据发现：

```text
Low-risk Python PR
security specialist 92% 无新增 finding
却消耗 31% token
```

生成 Hypothesis：

```text
H-102:
For low-risk Python PRs,
skip security specialist unless auth/security files changed.
```

生成 Candidate：

```yaml
policy: low-risk-python-v4

condition:
  risk: low
  language: python

agents:
  enable:
    - reliability
    - semantic

  conditional:
    security:
      when_path_matches:
        - auth/**
        - security/**
```

---

# 9.4 Policy Evolution Objective

不能只优化 F1。

推荐多目标评分：

```text
Quality
Cost
Latency
Reliability
Safety
```

公式示意：

```text
utility =
    0.40 * quality_score
  + 0.20 * high_risk_recall
  + 0.15 * reliability_score
  - 0.15 * normalized_cost
  - 0.10 * normalized_latency
```

但必须保留：

```text
Hard Safety Gates
```

例如：

```text
high-risk recall 不能下降超过 1%
critical miss 必须为 0
```

即：

```text
Safety Constraints
> Optimization Score
```

---

# 9.5 Candidate Generator

建议第一阶段采用：

```text
Template-based candidate generation
```

例如：

```text
减少某 Agent
增加某 Agent
降低 max_steps
提高 max_steps
开启 Evidence
关闭非必要 Evidence
```

不要一开始让 LLM 任意修改 YAML。

---

# 9.6 Runtime Policy Evaluation

必须通过 Replay：

```text
baseline policy
candidate policy
```

跑相同历史任务。

比较：

```text
Finding F1
High-risk Recall
False Positive
Cost
Latency
Failure Rate
Tool Calls
Agent Steps
```

---

# 9.7 Policy Canary

Candidate 通过 Replay 后：

```text
5% tasks
→ candidate

95%
→ baseline
```

持续：

```text
quality
failure
cost
latency
```

异常：

```text
Auto rollback
```

---

# 9.8 Policy Evolution 测试

新增：

```text
tests/test_policy_candidate.py
tests/test_policy_evolution_gate.py
tests/test_policy_replay.py
tests/test_policy_canary.py
tests/test_policy_auto_rollback.py
```

---

# 10. Phase 7：Evolution Attribution 与 Lineage

当前 Self-Evolution 还需要增加一个非常关键的能力：

> **为什么发生了这次进化？**

---

# 10.1 Evolution Lineage

所有进化必须形成：

```text
Experience
 ↓
Reflection
 ↓
Hypothesis
 ↓
Candidate
 ↓
Evaluation
 ↓
Deployment
 ↓
Outcome
```

不能只保存：

```text
v3 → v4
```

---

# 10.2 数据结构

建议：

```text
evolution_experiences
evolution_reflections
evolution_hypotheses
evolution_candidates
evolution_evaluations
evolution_deployments
evolution_outcomes
evolution_lineage
```

---

# 10.3 Candidate Lineage

例如：

```text
Candidate ID:
procedure-auth-v4

Source:
feedback-128
failure-case-43
trace-902

Reflection:
R-018

Hypothesis:
H-033

Parent:
procedure-auth-v3

Evaluation:
EV-443

Deployment:
DEP-91

Outcome:
OUT-91
```

---

# 10.4 Attribution Report

输出：

```text
Evolution: procedure-auth-v3 → v4

Reason
------
Repeated false negatives on missing authorization checks.

Evidence
--------
Failure cases: 17
Human feedback: 8
Critic objections: 11

Change
------
Added caller inspection before verification.

Replay Outcome
--------------
TP +9
FP +1
High-risk recall +5.2%
Latency +4.1%

Production Outcome
------------------
Accepted findings +3.8%
User rejection -2.4%

Decision
--------
KEEP
```

---

# 10.5 Regression Attribution

如果 Candidate 退化：

```text
为什么退化？
```

应能定位：

```text
specific language
specific repository
specific rule
specific severity
specific procedure
```

例如：

```text
整体 F1 提升
但是 Java 项目下降 6%
```

必须 Gate 拒绝。

---

# 10.6 Evolution Budget

新增：

```python
EvolutionBudget:
    max_candidates_per_day
    max_replay_cases
    max_evaluation_cost
    max_active_experiments
    max_activations_per_day
```

防止：

```text
candidate explosion
```

同时加入：

```text
deduplicate
cooldown
blacklist repeated failed hypothesis
```

---

# 11. Phase 8：Memory 与 Evolution 的结合

这不是最高优先级，但完成前面之后值得做。

---

# 11.1 当前问题

Memory 检索主要是 lexical overlap。

建议升级：

```text
Metadata Filter
      ↓
BM25
      +
Embedding Search
      ↓
Reranker
      ↓
Usefulness Weight
```

---

# 11.2 Memory Metadata

新增：

```text
success_count
failure_count
last_used_at
usefulness_score
confidence
source_type
source_version
```

---

# 11.3 Memory Utility

例如：

```text
memory_score =
  relevance
  × usefulness
  × confidence
  × freshness
```

其中：

```text
usefulness =
success_count /
(success_count + failure_count + 1)
```

---

# 11.4 Evolution Feedback

Memory 如果反复：

```text
被 Agent 调用
但没有帮助
```

降低 usefulness。

如果：

```text
成功帮助发现 verified finding
```

提高 usefulness。

最终：

```text
Memory
也成为可评估的 Agent capability
```

---

# 12. Phase 9：Observability 升级为 Decision Trace

仅记录日志还不够。

Harness 需要能够解释：

> Agent 为什么做了这个决策？

---

# 12.1 Trace 层级

建议：

```text
Task
 ├── Policy Resolution
 ├── Planner
 ├── Specialist
 │    ├── Model Call
 │    ├── Tool Call
 │    ├── Observation
 │    └── Decision
 ├── Critic
 ├── Evidence
 ├── Verifier
 └── Final
```

---

# 12.2 Trace 记录

每个 Agent Step：

```text
step_id
agent_id
policy_id
input_context_hash
action_type
tool
arguments_hash
observation_hash
token_usage
cost
latency
failure
recovery_action
```

---

# 12.3 Decision Diff

Replay 时展示：

```text
Baseline:
security-agent → search_diff → final

Candidate:
security-agent → search_diff
               → find_callers
               → inspect_tests
               → final
```

这会非常直观地证明：

```text
Procedure Evolution
改变了 Agent 行为
```

---

# 13. 推荐的最终目录结构

完成重构后建议：

```text
evoagent/
│
├── runtime/
│   ├── runtime.py
│   ├── loop.py
│   ├── state.py
│   └── checkpoint.py
│
├── policy/
│   ├── models.py
│   ├── risk.py
│   ├── resolver.py
│   ├── tool_policy.py
│   └── defaults.py
│
├── recovery/
│   ├── failures.py
│   ├── classifier.py
│   ├── planner.py
│   └── compensation.py
│
├── tools/
│   ├── registry.py
│   ├── metadata.py
│   ├── circuit_breaker.py
│   └── sandbox.py
│
├── replay/
│   ├── models.py
│   ├── recorder.py
│   ├── snapshot.py
│   ├── runner.py
│   ├── comparator.py
│   └── report.py
│
├── procedure/
│   ├── schema.py
│   ├── validator.py
│   ├── executor.py
│   └── registry.py
│
├── evolution/
│   ├── controller.py
│   ├── experience.py
│   ├── reflection.py
│   ├── hypothesis.py
│   ├── candidates.py
│   ├── gates.py
│   ├── attribution.py
│   ├── lineage.py
│   └── budget.py
│
├── memory/
│   ├── manager.py
│   ├── retrieval.py
│   └── utility.py
│
├── agents/
│   ├── planner.py
│   ├── specialist.py
│   ├── critic.py
│   ├── evidence.py
│   ├── verifier.py
│   └── arbiter.py
│
└── storage/
    ├── interfaces.py
    ├── task_repository.py
    ├── memory_repository.py
    ├── evolution_repository.py
    ├── replay_repository.py
    ├── sqlite/
    └── postgres/
```

不要求一次重构全部。

建议：

```text
新增模块时逐步迁移
```

避免大爆炸式重构。

---

# 14. 数据库新增表建议

建议至少增加：

```text
runtime_policies

tool_invocations
failure_events
recovery_events

replay_snapshots
replay_tool_observations
replay_runs
replay_metrics

procedure_skills
procedure_skill_versions

evolution_lineage
evolution_attributions
evolution_outcomes

policy_candidates
policy_evaluations
```

---

# 15. 重点 API

建议增加：

## Runtime Policy

```text
GET /api/runtime/policies
GET /api/runtime/policies/{id}
```

## Replay

```text
POST /api/replay/tasks/{task_id}
GET  /api/replay/runs/{run_id}
```

## Evolution

```text
GET  /api/evolution/candidates
GET  /api/evolution/candidates/{id}
POST /api/evolution/candidates/{id}/evaluate
POST /api/evolution/candidates/{id}/activate
POST /api/evolution/candidates/{id}/rollback
```

## Lineage

```text
GET /api/evolution/lineage/{candidate_id}
```

---

# 16. 测试体系

建议最终测试分层：

```text
tests/
├── unit/
├── integration/
├── runtime/
├── harness/
├── replay/
├── evolution/
├── policy/
├── recovery/
└── e2e/
```

---

# 17. 必须建立的 10 个 Demo Case

为了简历和面试，建议专门维护：

```text
examples/scenarios/
```

---

## Case 1：Risk-driven Routing

Low Risk：

```text
1 specialist
```

High Risk：

```text
3 specialists
+ critic
+ evidence
+ verifier
```

---

## Case 2：Tool Permission

普通 Agent 请求高风险 Tool：

```text
DENIED
```

---

## Case 3：Budget Enforcement

Agent 无限搜索：

```text
max_tool_calls exceeded
→ graceful stop
```

---

## Case 4：Model Failure Recovery

```text
primary model timeout
→ retry
→ fallback provider
```

---

## Case 5：No-progress Recovery

```text
repeated tool call
→ detected
→ replan
```

---

## Case 6：Checkpoint Resume

Runtime 中断：

```text
process restart
→ resume from checkpoint
```

---

## Case 7：Replay

历史 Task：

```text
baseline prompt
vs
candidate prompt
```

展示完整行为 Diff。

---

## Case 8：Procedure Skill Evolution

```text
repeated failure
→ hypothesis
→ procedure candidate
→ replay
→ activate
```

---

## Case 9：Runtime Policy Evolution

```text
low-risk PR
减少无效 specialist
```

结果：

```text
quality =
cost ↓
latency ↓
```

---

## Case 10：Automatic Rollback

Candidate Canary 出现：

```text
high-risk recall regression
```

系统：

```text
auto rollback
```

---

# 18. CI/CD

当前仓库应尽快增加：

```text
.github/workflows/
```

至少：

```text
ci.yml
postgres-integration.yml
redis-integration.yml
evolution-regression.yml
```

---

## CI

每次 PR：

```text
ruff
mypy
unit tests
integration tests
```

---

## PostgreSQL / Redis

GitHub Actions Service：

```text
PostgreSQL
Redis
```

跑真实 Integration。

---

## Evolution Regression

固定：

```text
tests/fixtures/evolution_benchmark.jsonl
```

自动跑：

```text
baseline
current
```

如：

```text
high-risk recall regression > threshold
```

CI 失败。

---

# 19. Benchmark 设计

虽然项目重点不是 Code Review 效果，但必须有一定 Benchmark 支撑工程能力。

建议至少做：

```text
Baseline
Rules only

B1
Rules + AST

B2
LLM Agent

B3
Multi-Agent

B4
Multi-Agent + Harness Policy

B5
+ Procedure Skill

B6
+ Evolution
```

报告：

```text
Precision
Recall
F1
High-risk Recall
False Positive Rate
Latency
Token
Cost
Tool Calls
Failure Rate
Recovery Success Rate
```

这比只展示 F1 更符合 Harness 项目定位。

---

# 20. Harness 专属指标

这是你项目非常值得突出的一点。

建议增加：

```text
Task Success Rate

Recovery Success Rate

Resume Success Rate

Invalid Tool Call Rate

Policy Violation Rate

Average Tool Calls

Average Agent Steps

No-progress Rate

Fallback Rate

Checkpoint Recovery Time

Cost per Successful Task

P95 Latency

Critical Failure Rate
```

---

# 21. Evolution 专属指标

建议增加：

```text
Candidate Acceptance Rate

Candidate Rejection Rate

Rollback Rate

Regression Detection Rate

Evolution Improvement Rate

Average Improvement per Accepted Candidate

Evolution Cost

Replay Cost

Shadow Failure Rate

Production Survival Rate

Time-to-Rollback

Catastrophic Forgetting Incidents
```

---

# 22. 开发顺序

建议按照下面顺序真实开发。

---

## Milestone 1：Policy Infrastructure

完成：

```text
ExecutionPolicy
RiskProfiler
PolicyResolver
Runtime integration
```

验收：

```text
Runtime 不再直接依赖散落配置。
```

---

## Milestone 2：Tool Governance

完成：

```text
ToolMetadata
ToolPolicy
Budget
CircuitBreaker
Audit
```

验收：

```text
任何 Tool 调用均经过 Harness。
```

---

## Milestone 3：Recovery Harness

完成：

```text
Failure taxonomy
Recovery planner
No-progress
Fallback
Compensation
```

验收：

```text
不同 Failure 具有不同恢复路径。
```

---

## Milestone 4：Replay Harness

完成：

```text
Snapshot
Recorder
ReplayToolRegistry
Runner
Comparator
Report
```

验收：

```text
任意历史 Task 可进行 deterministic replay。
```

---

## Milestone 5：Procedure Skill

完成：

```text
DSL
Validator
Executor
Version
Replay evaluation
```

验收：

```text
系统可学习“如何解决问题”。
```

---

## Milestone 6：Policy Evolution

完成：

```text
Policy Candidate
Replay
Gate
Canary
Rollback
```

验收：

```text
Harness 自身可在安全边界内优化。
```

---

## Milestone 7：Evolution Governance

完成：

```text
Lineage
Attribution
Budget
Production Outcome
```

验收：

```text
每次进化可回答：
为什么进化？
改了什么？
为什么被接受？
生产效果如何？
```

---

## Milestone 8：Benchmark + CI + README

完成：

```text
GitHub Actions
Harness Benchmark
Evolution Benchmark
Architecture Diagram
Demo
```

---

# 23. 每个 Milestone 的建议 Commit

建议后续不要再采用一次性大 Commit。

---

## M1

```text
feat(policy): introduce execution policy model
feat(policy): add risk profiler
feat(runtime): enforce execution policy
test(policy): add policy resolution coverage
```

---

## M2

```text
feat(tools): add tool metadata and permissions
feat(tools): add circuit breaker
feat(tools): persist tool audit events
```

---

## M3

```text
feat(recovery): introduce failure taxonomy
feat(recovery): add recovery planner
feat(runtime): detect no-progress agent loops
```

---

## M4

```text
feat(replay): add task snapshots
feat(replay): add deterministic tool replay
feat(replay): add candidate comparator
```

---

## M5

```text
feat(skill): add procedure skill DSL
feat(skill): add procedure executor
feat(evolution): support procedure candidates
```

---

## M6

```text
feat(evolution): support runtime policy candidates
feat(evolution): evaluate policies through replay
feat(canary): add policy auto rollback
```

---

# 24. README 最终应该重点展示什么

不要把 README 写成：

```text
支持几十个功能
```

而应围绕三个问题：

---

## 24.1 Agent 如何被约束

展示：

```text
Policy
Budget
Tool Governance
Failure Recovery
Checkpoint
Replay
```

---

## 24.2 Agent 如何进化

展示：

```text
Experience
→ Reflection
→ Hypothesis
→ Candidate
→ Replay
→ Gate
→ Canary
→ Activate
```

---

## 24.3 如何证明进化有效

展示：

```text
Baseline
vs
Candidate
```

并给出：

```text
quality
cost
latency
failure
```

对比。

---

# 25. 简历最终可以写出的核心能力

当以上能力完成后，可将项目描述为：

> **设计并实现 Policy-driven Agent Runtime，通过风险评估动态控制 Agent 拓扑、Tool 权限、执行预算和验证深度，并构建 Failure Taxonomy、Checkpoint/Resume、Fallback、Circuit Breaker 与 No-progress Recovery 等 Harness 能力，实现对非确定性 Agent 执行过程的可靠约束。**

第二条：

> **构建 Replay-based Self-Evolution Pipeline，将线上 Trace、失败案例和用户反馈转化为 Experience 与 Hypothesis，通过历史任务 Deterministic Replay、Validation/Holdout、Regression Gate、Shadow/Canary 和自动回滚实现 Prompt、Procedure Skill 与 Runtime Policy 的受控进化。**

第三条：

> **设计受限 Procedure Skill DSL，使 Agent 可从历史成功/失败轨迹中沉淀可复用工具调用策略，并由 Tool Governance 和 Runtime Policy 约束其执行权限、成本与副作用，避免自进化过程执行任意代码。**

这三条会比：

```text
实现多 Agent 协作
实现 Prompt 优化
实现 RAG
```

更能体现 Agent Infra / Harness Engineering 深度。

---

# 26. 面试时最值得重点讲的四个技术点

按照优先级：

## 1. Policy-driven Agent Runtime

核心问题：

> Agent 是不确定系统，Harness 如何将不确定执行控制在可预测边界内？

---

## 2. Replay Harness

核心问题：

> 如何证明一次 Prompt / Skill / Policy 修改真的优于旧版本？

答案：

```text
counterfactual deterministic replay
```

---

## 3. Procedure Skill Evolution

核心问题：

> 自进化到底进化了什么？

回答：

```text
不是只修改 Prompt，
而是沉淀新的 Agent Workflow。
```

---

## 4. Runtime Policy Evolution

核心问题：

> Agent 系统是否能优化自己的执行方式？

回答：

```text
可以，但只能在 immutable safety boundary 内优化。
```

---

# 27. 不建议优先投入的方向

在上述主线完成前，建议暂缓：

```text
更多 Code Review Rules
更多 Agent
更多语言支持
复杂 Web UI
复杂知识库 RAG
大量 Prompt 调参
大量模型适配
```

这些功能可以让项目：

```text
更大
```

但不会让：

```text
Harness 更深
Self-Evolution 更深
```

---

# 28. 最终完成标准

当项目满足下面 8 条，可以认为 Harness 与 Self-Evolution 已经真正做深：

- [ ] Agent Runtime 完全由 Execution Policy 驱动
- [ ] Tool 调用全部受到权限、预算、风险和副作用控制
- [ ] Failure 具有分类、恢复和降级策略
- [ ] 任意历史 Task 可以 Replay
- [ ] Evolution Candidate 必须经过 Replay / Holdout / Gate
- [ ] Agent 可以进化 Procedure Skill
- [ ] Harness 可以在安全边界内进化 Runtime Policy
- [ ] 每次 Evolution 都存在完整 Lineage 与 Production Outcome

最终项目就不再只是：

> Self-Evolving Code Review Agent

而可以更准确地定位成：

> **A production-oriented self-evolving agent system with a policy-driven execution harness, replay-based evaluation, controlled capability evolution, and runtime policy optimization.**

Code Review 是业务场景。

真正需要在简历中展示的是：

```text
Agent Runtime
Harness Governance
Replay Infrastructure
Evolution Control Plane
Capability Evolution
Runtime Policy Evolution
```

这才是 EvoReview-Agent 最有技术辨识度的发展路线。
