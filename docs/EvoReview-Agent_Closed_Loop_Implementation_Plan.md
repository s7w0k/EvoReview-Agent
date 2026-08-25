# EvoReview-Agent 真正闭环实施计划
## Harness 主链路集成 + Self-Evolution 生产闭环

> **项目目标**
>
> 以 Code Review 作为真实业务 workload，把当前已经具备的 Agent Runtime、Policy、Tool Governance、Recovery、Replay、Procedure Skill、Policy Evolution、Canary、Lineage 等能力，全部接入同一条真实执行链路和同一条真实进化链路。
>
> 完成本计划后，项目应从：
>
> **“包含大量 Harness / Self-Evolution 组件的 Agent 项目”**
>
> 升级为：
>
> **“具备完整 Agent Execution Closed Loop 与 Self-Evolution Closed Loop 的生产导向 Agent Runtime。”**

---

# 1. 最终架构目标

## 1.1 Agent Execution Closed Loop

真实 `/v1/reviews` 请求必须经过：

```text
POST /v1/reviews
        ↓
ReviewService
        ↓
DiffParser
        ↓
RiskProfiler
        ↓
PolicyResolver
        ↓
ExecutionPolicy
        ↓
ReviewHarness(policy)
        ↓
MultiAgentCoordinator(policy)
        ↓
Dynamic Agent Routing
        ↓
AgentLoop(policy)
        ↓
GovernedToolRegistry
        ↓
Schema Validation
        ↓
Tool Policy
        ↓
Budget
        ↓
Approval / Sandbox
        ↓
Circuit Breaker
        ↓
Tool Execution
        ↓
Observation
        ↓
FailureClassifier
        ↓
RecoveryPlanner
        ↓
RecoveryExecutor
        ↓
DecisionTrace
        ↓
ReplaySnapshot
        ↓
Persisted Task Outcome
```

要求：

- 所有 Policy 真正参与 Runtime；
- 所有 Agent Tool 真正经过 GovernedToolRegistry；
- 所有可恢复 Failure 真正进入 Recovery；
- 每个真实任务自动生成 DecisionTrace；
- 每个真实任务自动生成可用于后续 Evolution 的 ReplaySnapshot。

---

## 1.2 Self-Evolution Closed Loop

```text
Production Review
        ↓
Trace / Failure / Finding / Feedback / Cost / Latency
        ↓
Experience
        ↓
Reflection
        ↓
Hypothesis
        ↓
Candidate Generator
      /        |         \
     ↓         ↓          ↓
 Prompt    Procedure   Runtime Policy
Candidate   Candidate    Candidate
     \         |          /
      └────────┼─────────┘
               ↓
       Replay Dataset
               ↓
 Baseline vs Candidate Replay
               ↓
       Validation Split
               ↓
         Holdout Split
               ↓
       Hard Safety Gate
               ↓
         Shadow / Canary
               ↓
    Deployment Manager
               ↓
            Activate
               ↓
      Production Outcome
               ↓
       Regression Monitor
               ↓
         Keep / Rollback
               ↓
     Persistent Lineage
               ↓
        New Experience
```

最终系统必须能够回答：

1. 为什么发生了这次进化？
2. 进化来源于哪些 Experience / Failure / Feedback？
3. 生成了什么 Hypothesis？
4. Candidate 修改了什么？
5. Candidate 与 Baseline 在相同历史 workload 上差多少？
6. 是否存在高风险 Recall、Reliability、Critical Miss 等退化？
7. 为什么 Candidate 被允许进入 Canary？
8. Canary 实际用了多少生产流量？
9. Candidate 上线后真实效果如何？
10. 为什么 Promote 或 Rollback？
11. Outcome 是否进入下一轮 Experience？

---

# 2. 当前阶段的开发原则

## 原则 1：不再横向堆功能

在真正闭环完成前，不优先增加：

- 新 Specialist Agent；
- 新编程语言；
- 新 Code Review Rule；
- 更复杂 Web UI；
- 更多模型供应商；
- 更复杂 RAG；
- 大量 Prompt 调参。

本阶段唯一目标：

> **Integration + Persistence + Safety Correctness + Closed Loop**

---

## 原则 2：模块存在不等于能力完成

一个模块只有在：

```text
真实 Review 请求会经过它
```

时，才能视为真正完成。

例如：

```text
RiskProfiler.py 存在
```

不等于：

```text
生产 Review 使用 RiskProfiler 决定 Runtime Policy
```

---

## 原则 3：所有治理状态必须持久化

以下状态不得只存在于内存 dict：

- active runtime policy；
- policy version；
- policy deployment；
- replay snapshot；
- replay run；
- tool audit；
- failure / recovery events；
- decision trace；
- procedure version；
- procedure deployment；
- evolution lineage；
- candidate evaluation；
- canary exposure；
- evolution budget usage。

---

## 原则 4：Safety 默认 fail-closed

对以下能力：

```text
approval
sandbox
side-effect
tenant isolation
repository isolation
risk floor
critical verification
```

无法确认安全时应：

```text
DENY
HOLD
HUMAN_REVIEW
```

而不是继续执行。

---

## 原则 5：Evolution 必须可证伪

Candidate 不能因为：

```text
绝对 utility > 0
LLM 认为它更好
某一组测试通过
```

而上线。

必须同时满足：

```text
candidate_utility - baseline_utility >= min_improvement
+
hard safety gate pass
+
validation pass
+
holdout pass
+
canary pass
```

---

# 3. 实施阶段总览

| 阶段 | 目标 | 优先级 |
|---|---|---:|
| Phase 0 | 修复当前 P0 Harness / Evolution 语义问题 | P0 |
| Phase 1 | Policy 真正接入 ReviewService 主链路 | P0 |
| Phase 2 | GovernedToolRegistry 接管所有 Agent Tool | P0 |
| Phase 3 | Recovery 接管真实 Runtime / AgentLoop Failure | P0 |
| Phase 4 | DecisionTrace + ReplaySnapshot 自动生成 | P0 |
| Phase 5 | 新 Harness / Evolution 状态持久化 | P0 |
| Phase 6 | Procedure Skill 自动生成与进化 | P1 |
| Phase 7 | Runtime Policy Evolution 真正跑历史 workload | P1 |
| Phase 8 | Canary Router + Deployment Manager | P1 |
| Phase 9 | Production Outcome → Experience 回流 | P1 |
| Phase 10 | 合并 Memory 与 usefulness feedback | P1 |
| Phase 11 | CI / Integration / Regression Benchmark | P1 |
| Phase 12 | README / Demo / 简历证据包装 | P2 |

---

# 4. Phase 0：修复 P0 语义问题

这是必须最先完成的一阶段。

---

## 4.1 Approval 必须 fail-closed

### 当前风险

```text
requires_approval = true
approval_provider = none
```

时，工具不能继续执行。

### 正确流程

```text
Tool Decision
    ↓
requires_approval?
    ├─ no → execute
    └─ yes
         ↓
approval provider exists?
         ├─ no → DENY
         └─ yes
              ↓
           approved?
            ├─ no → DENY
            └─ yes → execute
```

### 修改文件

```text
evoagent/tools/governed_registry.py
tests/test_side_effect_tool.py
tests/test_tool_policy.py
```

### 验收测试

```text
test_required_approval_without_provider_is_denied
test_required_approval_declined_is_denied
test_required_approval_success_executes
```

---

## 4.2 修复 Global Tool Budget

### 当前目标

所有真正被 dispatch 的 Tool 都必须累计：

```python
self._tool_call_counts[name] += 1
self._tool_call_counts["__all__"] += 1
```

### 推荐语义

| 情况 | 是否消耗 Tool Budget |
|---|---|
| Schema invalid | 否 |
| Policy deny | 否 |
| Approval deny | 否 |
| Tool 真正开始执行 | 是 |
| Tool timeout | 是 |
| Tool execution failed | 是 |
| Tool success | 是 |

### 验收

```text
test_total_budget_across_multiple_tools
test_failed_tool_consumes_budget
test_denied_tool_does_not_consume_budget
```

---

## 4.3 实现真实 Tool Timeout

当前 `timeout_seconds` 必须从 metadata 变成真实 Runtime Guarantee。

新增：

```text
evoagent/tools/executor.py
evoagent/tools/sandbox.py
```

接口：

```python
class ToolExecutor:
    def execute(
        self,
        tool,
        arguments,
        metadata,
        execution_context,
    ) -> ToolExecutionResult:
        ...
```

### Tool 类型

#### Safe In-process

例如：

```text
search_diff
changed_line
list_changed_files
```

允许同步调用。

#### Potentially Blocking

例如：

```text
run_tests
static_analysis
external analyzer
repo checkout
```

必须使用：

```text
subprocess / worker process
```

实现真正 timeout。

### subprocess 行为

```text
start
↓
wait(timeout)
↓
timeout
↓
terminate
↓
grace period
↓
kill
```

### 验收

```text
test_blocking_tool_terminated_after_timeout
test_timeout_generates_failure_event
test_timeout_updates_circuit_breaker
```

---

## 4.4 实现 Sandbox Enforcement

当前 `requires_sandbox=True` 必须真正产生不同执行路径。

```text
ToolDecision
      ↓
requires_sandbox?
 ├─ no → NormalExecutor
 └─ yes → SandboxExecutor
```

最低可行 Sandbox：

- 临时 Workspace；
- 不继承 Secret 环境变量；
- 网络默认关闭；
- Command allowlist；
- Timeout；
- 输出长度限制；
- 可选 Docker isolation。

推荐：

```python
@dataclass
class SandboxContext:
    task_id: str
    repository: str
    commit_sha: str
    workspace: str
    env_allowlist: list[str]
    network_enabled: bool = False
```

---

## 4.5 PolicyResolver 增加 Immutable Safety Floor

当前需要明确区分：

```text
Optimization Policy
vs
Immutable Safety Floor
```

新增：

```text
evoagent/policy/safety_floor.py
```

```python
@dataclass(frozen=True)
class SafetyFloor:
    minimum_risk_level: str
    require_critic: bool
    require_evidence: bool
    require_verifier: bool
    require_sandbox: bool
    mandatory_tool_denies: set[str]
    mandatory_approval_tools: set[str]
```

最终 Resolve：

```text
System Default
↓
Tenant Override
↓
Repository Override
↓
Task Optimization Override
↓
Risk Profile
↓
Safety Floor Enforcement
↓
Resolved Policy
```

Safety Floor 必须最后执行。

### High / Critical 要求

```text
risk level 不得降低
evidence_required 不得关闭
verifier_required 不得关闭
sandbox_required 不得关闭
side-effect approval 不得关闭
```

---

## 4.6 ToolPermission 改为 merge-by-name

不要简单：

```python
permissions.extend(override)
```

应：

```python
def merge_tool_permissions(base, override):
    merged = {p.tool_name: p for p in base}
    for p in override:
        merged[p.tool_name] = p
    return list(merged.values())
```

验收：

```text
repository deny 能覆盖 system allow
task allow 不能覆盖 immutable hard deny
```

---

## 4.7 修复 Policy Evolution Utility

正确逻辑：

```python
baseline_utility = evolution_utility(
    baseline_metrics,
    reference=baseline_metrics,
)

candidate_utility = evolution_utility(
    candidate_metrics,
    reference=baseline_metrics,
)

improvement = candidate_utility - baseline_utility
```

Candidate 必须：

```python
improvement >= min_improvement
```

ReplayComparison 增加：

```text
baseline_utility
candidate_utility
improvement
```

---

## 4.8 Candidate Mutation 必须 preserve parent fields

所有 Candidate 应基于：

```python
dataclasses.replace(parent, ...)
```

而不是重新构造整个 `ExecutionPolicy`。

避免修改：

```text
max_steps
```

时意外丢失：

```text
verification
retry
tool permissions
agents
```

---

## Phase 0 Definition of Done

- [ ] Approval fail-closed
- [ ] Global Tool Budget 生效
- [ ] Tool Timeout 真实生效
- [ ] Sandbox 真实生效
- [ ] Safety Floor 最终强制执行
- [ ] Tool permission merge 正确
- [ ] Candidate 用 improvement 判断
- [ ] Policy mutation 不丢字段

---

# 5. Phase 1：Policy 真正接入 ReviewService

---

## 5.1 新增 ReviewExecutionContext

新增：

```text
evoagent/execution/context.py
```

```python
@dataclass
class ReviewExecutionContext:
    task_id: str
    tenant_id: str
    repository: str
    pull_request: int | None

    parsed_diff: ParsedDiff

    risk_profile: RiskProfile
    execution_policy: ExecutionPolicy

    prompt_version: str | None
    skill_versions: dict[str, str]
    runtime_policy_version: int | None

    model_name: str | None
```

目的：

统一传递：

```text
task
tenant
repo
risk
policy
version
model
```

---

## 5.2 ReviewService 初始化

在 `ReviewService.__init__()` 增加：

```python
self.risk_profiler = RiskProfiler()
self.policy_resolver = PolicyResolver(...)
self.policy_repository = RuntimePolicyRepository(...)
self.recovery_manager = RecoveryManager(...)
self.trace_repository = DecisionTraceRepository(...)
self.replay_repository = ReplayRepository(...)
```

---

## 5.3 每次 Review 先 Resolve Policy

```python
parsed = parse_unified_diff(diff)

risk = self.risk_profiler.profile(parsed)

tenant_override = ...
repository_override = ...
active_policy = ...

policy = self.policy_resolver.resolve(...)
```

然后构造：

```python
context = ReviewExecutionContext(...)
```

---

## 5.4 Task 保存 Policy Snapshot

Task 创建或执行前持久化：

```text
risk_profile_json
policy_id
policy_version
policy_snapshot_json
```

Replay 必须读取：

> 当时真实使用的 Policy

而不是当前 active policy。

---

## 5.5 ReviewHarness 改造

从：

```python
ReviewHarness(
    store,
    reviewer,
    max_steps,
    timeout,
)
```

改成：

```python
ReviewHarness(
    store=store,
    reviewer=reviewer,
    execution_context=context,
    execution_policy=policy,
    recovery_manager=self.recovery_manager,
    trace_logger=...,
)
```

Runtime：

```python
AgentRuntime(execution_policy=policy)
```

---

## 5.6 MultiAgentCoordinator 感知 Policy

构造参数增加：

```text
execution_policy
execution_context
governed_tool_registry
recovery_manager
trace_context
```

---

## 5.7 Dynamic Agent Routing

实际 Specialist 列表：

```python
enabled_agents = execution_policy.agents.enabled_agents
```

例如：

```text
Low
→ reliability

Medium
→ reliability + semantic

High
→ security + reliability + semantic
```

---

## 5.8 Verification Stage 动态执行

```python
if policy.verification.critic_required:
    ...

if policy.verification.evidence_required:
    ...

if policy.verification.verifier_required:
    ...
```

---

## 5.9 E2E 测试

新增：

```text
tests/e2e/test_review_policy_routing.py
```

### Low Risk

验证：

```text
risk=low
only reliability specialist
critic skipped
evidence skipped
verifier skipped
```

### High Risk

验证：

```text
security agent enabled
critic enabled
evidence enabled
verifier enabled
sandbox required
```

---

# 6. Phase 2：GovernedToolRegistry 接管所有 Agent Tool

---

## 6.1 建立统一 Tool Catalog

新增：

```text
evoagent/tools/catalog.py
```

定义：

```python
@dataclass
class ToolDefinition:
    tool: AgentTool
    metadata: ToolMetadata
```

所有工具从统一 Catalog 注册。

---

## 6.2 第一批统一工具

```text
search_diff
changed_line
list_changed_files
recall_memory
read_file
search_code
find_callers
find_tests
run_static_analysis
run_tests
```

---

## 6.3 AgentLoop 传递 Agent 身份

新增：

```python
agent_id
task_id
execution_context
```

Tool 调用：

```python
tools.invoke_as(
    agent_id,
    tool_name,
    arguments,
    task_id=task_id,
)
```

---

## 6.4 Procedure Executor 也必须走 GovernedToolRegistry

Procedure 不允许旁路。

绑定：

```python
tool_invoker = lambda name, args: governed_registry.invoke_as(
    agent_id=f"procedure:{skill.name}",
    name=name,
    arguments=args,
    task_id=task_id,
)
```

---

## 6.5 Replay Live Mode 同样使用 Governance

Live Replay：

```text
ReplayRunner
↓
GovernedToolRegistry
↓
Read-only Replay Policy
```

Side-effect Tool：

```text
always deny
```

---

## 6.6 新增 Harness Metrics

```text
tool_calls_total
tool_calls_denied_total
tool_timeouts_total
tool_policy_violation_total
tool_circuit_open_total
tool_approval_requested_total
tool_approval_denied_total
```

---

# 7. Phase 3：Recovery 接管真实失败

---

## 7.1 新增 RecoveryManager

```text
evoagent/recovery/manager.py
```

```python
class RecoveryManager:
    def handle(
        self,
        exc,
        context,
        runtime_state,
        node,
        agent_id,
        tool_context=None,
    ) -> RecoveryOutcome:
        ...
```

内部：

```text
Exception
↓
FailureClassifier
↓
FailureEvent
↓
RecoveryPlanner
↓
RecoveryExecutor
↓
Persist
↓
DecisionTrace
```

---

## 7.2 Runtime Node Failure 接入 Recovery

不能统一：

```python
except Exception:
    retry
```

而应：

```text
exception
↓
RecoveryManager
↓
retry / backoff / fallback / replan / abort / human_review
```

---

## 7.3 Model Exception 标准化

增加：

```text
ModelTimeout
ModelRateLimit
ModelContextOverflow
ModelInvalidOutput
ModelUnavailable
```

避免全部使用：

```text
RuntimeError
```

---

## 7.4 Tool Failure 分类

区分：

### Recoverable Runtime Failure

```text
timeout
temporary unavailable
rate limit
circuit open
```

### Normal Observation Failure

```text
file not found
symbol not found
no callers
```

后者不一定触发 Recovery。

---

## 7.5 No-progress Detection 接入 AgentLoop

记录最近 Action：

```text
tool + canonical args
```

若连续重复：

```text
same action >= threshold
```

产生：

```text
AGENT_NO_PROGRESS
```

恢复：

```text
REPLAN
→ FALLBACK_AGENT
→ ABORT
```

---

## 7.6 Recovery Budget

新增：

```text
max_recovery_attempts
max_replans
max_model_switches
```

防止 Recovery 自身形成无限 Loop。

---

# 8. Phase 4：自动 DecisionTrace + ReplaySnapshot

---

## 8.1 每个真实任务自动创建 Trace

任务开始：

```python
trace = DecisionTrace(task_id)
```

自动记录：

```text
policy_resolution
agent_started
agent_step
tool_authorized
tool_denied
tool_started
tool_observation
tool_failed
failure_classified
recovery_planned
recovery_executed
agent_completed
task_completed
```

---

## 8.2 DecisionTrace 持久化

新增：

```text
DecisionTraceRepository
```

不再只使用：

```python
dict[str, DecisionTrace]
```

---

## 8.3 自动生成 ReplaySnapshot

Review 成功或失败时：

```text
ExecutionContext
+
DecisionTrace
+
ToolAudit
+
Task Input
+
Model / Prompt / Skill / Policy Version
↓
ReplaySnapshotBuilder
```

---

## 8.4 Replay Observation 改为有序消费

不要只按：

```text
fingerprint(tool + args)
```

返回第一条。

使用：

```text
fingerprint + occurrence index
```

或：

```python
dict[fingerprint, deque[RecordedObservation]]
```

这样重复调用同一 Tool + args 时 Replay 仍正确。

---

## 8.5 Replay Level

定义：

### L1 Tool Replay

回放 Tool Observation。

### L2 Tool + Model Output Replay

连模型输出一起固定，用于 Harness deterministic replay。

### L3 Live Counterfactual Replay

重新调用 Candidate Prompt / Model，用于真实候选评测。

---

# 9. Phase 5：全面持久化

---

## 9.1 建议 Repository 接口

```text
evoagent/storage/repositories/
├── runtime_policy.py
├── deployment.py
├── tool_audit.py
├── failure.py
├── recovery.py
├── replay.py
├── procedure.py
├── lineage.py
├── evolution_budget.py
└── decision_trace.py
```

---

## 9.2 Runtime Policy 表

```text
runtime_policy_versions
runtime_policy_deployments
runtime_policy_overrides
```

关键字段：

```text
tenant_id
repository_scope
risk_level
policy_name
version
parent_version
content_json
status
hypothesis_id
created_at
activated_at
```

---

## 9.3 Replay

```text
replay_snapshots
replay_tool_observations
replay_runs
replay_metrics
```

---

## 9.4 Procedure

```text
procedure_skills
procedure_skill_versions
procedure_deployments
```

---

## 9.5 Evolution

```text
evolution_lineages
evolution_lineage_nodes
evolution_attributions
evolution_outcomes
```

---

## 9.6 Governance

```text
tool_invocation_audit
failure_events
recovery_events
decision_trace_events
evolution_budget_usage
```

---

## 9.7 Restart Recovery Test

流程：

```text
run partially
↓
persist
↓
destroy service
↓
recreate service
↓
resume
```

验证：

```text
policy preserved
trace preserved
checkpoint preserved
budget preserved
deployment preserved
candidate state preserved
```

---

# 10. Phase 6：Procedure Skill 真正自动进化

---

## 10.1 Production Trace Mining

新增：

```text
evoagent/procedure/miner.py
```

从成功 Trace 中抽取：

```text
tool path
task type
risk type
finding outcome
verification outcome
human feedback
```

例如：

```text
search_code
→ find_callers
→ read_file
→ find_tests
```

在 auth 类任务上频繁成功。

---

## 10.2 Procedure Pattern 条件

例如：

```text
min_support >= 5
success_rate >= 0.8
verification_pass >= 0.8
```

才产生 Candidate Source。

---

## 10.3 Reflection → Hypothesis

例：

```text
Observation:
auth 类 finding 加入 caller inspection 后，
verification pass rate 提升 18%。

Hypothesis:
在 authentication-related review 中，
在 verifier 前调用 find_callers
可减少 unsupported finding。
```

---

## 10.4 Procedure Synthesizer

新增：

```text
evoagent/procedure/synthesizer.py
```

输入：

```text
Hypothesis
Allowed Tool Catalog
Successful Trace Samples
Budget
Safety Constraints
```

输出：

```text
Procedure DSL Candidate
```

LLM 只能选择：

```text
registered tool
registered check
safe control fields
```

不能生成代码。

---

## 10.5 Procedure Candidate Lifecycle

```text
DRAFT
↓
STATIC_VALIDATED
↓
REPLAY_PASSED
↓
HOLDOUT_PASSED
↓
SHADOW
↓
CANARY
↓
ACTIVE
```

禁止：

```text
VALIDATED → ACTIVE
```

直接跳过评测。

---

## 10.6 Procedure Run Status

新增：

```text
SUCCESS
PARTIAL
FAILED
ABORTED
```

每个 Tool Step 支持：

```yaml
on_failure: abort
```

第一阶段支持：

```text
abort
continue
```

后续再加：

```text
fallback
replan
```

---

# 11. Phase 7：Runtime Policy Evolution 真正接入 Replay Dataset

---

## 11.1 可进化字段白名单

允许：

```text
enabled_agents
max_parallel_agents
max_steps
max_tool_calls
critic_required
evidence_required
verifier_required
read-only tool allowlist
retry counts
```

禁止：

```text
tenant isolation
auth
repo isolation
mandatory side-effect approval
critical risk safety floor
secret handling
sandbox network boundary
```

---

## 11.2 Candidate Mutation

使用：

```python
replace(parent, ...)
```

并保存：

```text
changed_fields
before
after
```

---

## 11.3 Candidate Signature

```text
hash(
  parent_version
  + normalized_mutation
  + scope
)
```

用于：

```text
dedupe
cooldown
repeated failure block
```

---

## 11.4 PolicyReplayRunner

实现真正生产 Runner：

```text
load replay snapshots
↓
for each snapshot
    run baseline policy
    run candidate policy
↓
aggregate metrics
```

不能再使用测试式：

```python
def runner(policy):
    return fake_metrics
```

---

## 11.5 Dataset Split

至少：

```text
train
validation
holdout
temporal_holdout
```

规则：

```text
Candidate generation 不可使用 holdout
```

---

## 11.6 Metrics

```text
Finding F1
High-risk Recall
Critical Miss
False Positive Rate
Task Success Rate
Failure Rate
Recovery Success Rate
Tool Calls
Agent Steps
Latency
Cost
Policy Violations
```

---

## 11.7 Gate

Hard Gate 优先于 Utility：

```text
critical_misses == 0

high_risk_recall regression <= threshold

reliability regression <= threshold

policy_violation == 0

side_effect_safety_incident == 0
```

---

# 12. Phase 8：真实 Canary Router + Deployment Manager

---

## 12.1 新增 PolicyDeploymentManager

```text
evoagent/policy_evolution/deployment.py
```

```python
class PolicyDeploymentManager:
    def resolve_policy(
        self,
        tenant_id,
        repository,
        risk_level,
        task_id,
    ) -> ExecutionPolicy:
        ...
```

---

## 12.2 Stable Canary Assignment

不要用：

```python
random.random()
```

应使用：

```text
hash(task_id + deployment_id)
```

决定 lane。

这样同一个 Task 重试时仍进入同一 lane。

---

## 12.3 Deployment State

```text
DRAFT
REPLAY_PASSED
SHADOW
CANARY
PROMOTED
ROLLED_BACK
PAUSED
```

---

## 12.4 Canary Rollout

建议：

```text
5%
→ 10%
→ 25%
→ 50%
→ 100%
```

每阶段要求：

```text
minimum sample
minimum duration
hard safety pass
```

---

## 12.5 Exposure Log

每个 Task 保存：

```text
deployment_id
lane
baseline_version
candidate_version
traffic_share
```

否则生产指标无法归因。

---

## 12.6 Auto Rollback 必须真实切回 Baseline

不能只：

```text
status = ROLLED_BACK
```

而要：

```text
candidate disabled
baseline restored as active
new tasks resolve baseline
```

---

# 13. Phase 9：Production Outcome → Experience

这是最终闭环最后一环。

---

## 13.1 Outcome 类型

### Runtime

```text
task success
task failure
latency
cost
tool calls
recovery count
```

### Quality

```text
finding accepted
finding rejected
false positive
false negative
fix accepted
```

### Safety

```text
critical miss
policy violation
sandbox violation
side-effect incident
```

---

## 13.2 Outcome Attribution

每个 Outcome 必须知道：

```text
prompt version
rule skill version
procedure version
runtime policy version
deployment lane
candidate id
```

---

## 13.3 Experience Builder

Outcome 转成：

```text
positive experience
negative experience
failure experience
cost experience
safety experience
```

---

## 13.4 Feedback Trust

单条 Feedback 不直接触发 Evolution。

使用：

```text
minimum confirmers
trusted feedback ratio
duplicate merge
cooldown
```

---

## 13.5 Lineage 最后一层

```text
OUTCOME
```

保存：

```text
production sample size
metric deltas
observation window
decision keep / rollback
```

---

## 13.6 Closed-loop Trigger

第一阶段：

```text
manual
+
scheduled scanner
```

后续再增加：

```text
event-driven
```

例如：

```text
confirmed false negative >= N
```

自动创建 Evolution Job。

---

# 14. Phase 10：合并 Memory

不要维护第二套独立 MemoryManager。

---

## 14.1 扩展原 Persistent Memory

增加：

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

## 14.2 Retrieval Pipeline

```text
Metadata Filter
↓
Lexical / BM25
+
Optional Embedding
↓
Reranker
↓
Usefulness Weight
↓
Top K
```

---

## 14.3 Outcome Feedback

若 Memory 被使用后：

```text
finding verified + accepted
```

则：

```text
helpful += 1
```

若：

```text
finding rejected
```

则：

```text
unhelpful += 1
```

---

# 15. Phase 11：CI / Integration / Regression

---

## 15.1 GitHub Actions Jobs

```text
unit
lint
typecheck
sqlite-integration
postgres-integration
redis-integration
harness-e2e
evolution-regression
```

---

## 15.2 PostgreSQL / Redis Services

CI 中使用真实：

```text
postgres
redis
```

而不是只测 SQLite。

---

## 15.3 Ruff

加入：

```text
ruff check
```

---

## 15.4 Mypy

先覆盖核心新模块：

```text
policy
tools
recovery
replay
procedure
policy_evolution
```

---

## 15.5 Coverage

核心 Harness：

```text
>= 85%
```

---

## 15.6 Evolution Regression Fixtures

维护：

```text
tests/fixtures/evolution/
```

至少有：

### Known Good Candidate

必须：

```text
PASS
```

### Known Bad Candidate

必须：

```text
Hard Gate Reject
```

---

# 16. 最关键的 Full Closed-loop E2E Test

新增：

```text
tests/e2e/test_full_self_evolution_closed_loop.py
```

必须覆盖以下步骤。

## Step 1

创建 baseline：

```text
runtime-high-v1
```

## Step 2

执行真实 Review Task。

必须产生：

```text
RiskProfile
ExecutionPolicy
DecisionTrace
ToolAudit
ReplaySnapshot
Outcome
```

## Step 3

注入：

```text
confirmed false negative
```

## Step 4

生成：

```text
Experience
```

并持久化。

## Step 5

生成：

```text
Reflection
Hypothesis
```

## Step 6

生成：

```text
Procedure Candidate
```

或：

```text
Runtime Policy Candidate
```

## Step 7

Replay：

```text
Baseline
vs
Candidate
```

使用相同 snapshots。

## Step 8

验证 Known-good：

```text
PASS
```

Known-bad：

```text
REJECT
```

## Step 9

Candidate 进入 Canary。

## Step 10

真实新 Task 根据 stable hash 被分配到：

```text
baseline lane
candidate lane
```

## Step 11

记录 Production Outcome。

## Step 12

若 Candidate 稳定：

```text
PROMOTE
```

## Step 13

新 Review 请求必须使用：

```text
new active version
```

## Step 14

模拟回归：

```text
critical miss
```

## Step 15

自动：

```text
ROLLBACK
```

## Step 16

新 Review 再次使用：

```text
baseline / previous active version
```

## Step 17

查询 Lineage。

必须包含：

```text
Experience
Reflection
Hypothesis
Candidate
Evaluation
Deployment
Outcome
```

完整七阶段。

---

# 17. 推荐最终目录

```text
evoagent/
│
├── execution/
│   ├── context.py
│   └── orchestrator.py
│
├── policy/
│   ├── models.py
│   ├── defaults.py
│   ├── risk.py
│   ├── resolver.py
│   ├── safety_floor.py
│   └── tool_policy.py
│
├── tools/
│   ├── catalog.py
│   ├── governed_registry.py
│   ├── executor.py
│   ├── sandbox.py
│   ├── circuit_breaker.py
│   ├── invocation.py
│   └── audit.py
│
├── recovery/
│   ├── failures.py
│   ├── classifier.py
│   ├── planner.py
│   ├── executor.py
│   ├── manager.py
│   ├── no_progress.py
│   └── compensation.py
│
├── replay/
│   ├── models.py
│   ├── recorder.py
│   ├── snapshot.py
│   ├── runner.py
│   ├── comparator.py
│   ├── dataset.py
│   └── report.py
│
├── procedure/
│   ├── schema.py
│   ├── validator.py
│   ├── executor.py
│   ├── registry.py
│   ├── miner.py
│   ├── synthesizer.py
│   └── evolution.py
│
├── policy_evolution/
│   ├── candidate.py
│   ├── objective.py
│   ├── replay_eval.py
│   ├── gate.py
│   ├── canary.py
│   ├── deployment.py
│   ├── rollback.py
│   └── pipeline.py
│
├── evolution_gov/
│   ├── lineage.py
│   ├── attribution.py
│   ├── regression.py
│   └── budget.py
│
├── decision_trace/
│   ├── trace.py
│   ├── context.py
│   └── repository.py
│
└── storage/
    ├── interfaces.py
    ├── repositories/
    ├── sqlite/
    └── postgres/
```

---

# 18. 推荐数据库 Migration

```text
migrations/
001_runtime_policy.sql
002_tool_audit.sql
003_failure_recovery.sql
004_replay.sql
005_procedure.sql
006_evolution_lineage.sql
007_policy_deployment.sql
008_decision_trace.sql
009_memory_metadata.sql
```

---

# 19. 推荐开发 Milestone 与 Commit

## Milestone 0

```text
fix(harness): fail closed on missing side-effect approval
fix(tools): enforce global tool-call budget
feat(tools): add timeout and sandbox executors
fix(policy): enforce immutable safety floor
fix(policy): merge tool permissions by name
fix(evolution): compare candidate utility against baseline
fix(evolution): preserve policy fields during mutation
```

## Milestone 1

```text
feat(service): resolve execution policy per review
feat(harness): execute review under resolved policy
feat(agents): route specialists by policy
```

## Milestone 2

```text
feat(tools): route all agent tools through governed registry
feat(agent-loop): propagate agent identity to tool calls
feat(procedure): govern procedure tool execution
```

## Milestone 3

```text
feat(recovery): wire failure classifier into runtime
feat(recovery): execute semantic recovery strategies
feat(agent-loop): detect no-progress loops
```

## Milestone 4

```text
feat(trace): persist decision traces
feat(replay): capture snapshots from production reviews
feat(replay): support sequenced deterministic observations
```

## Milestone 5

```text
feat(storage): persist runtime policies and deployments
feat(storage): persist replay and tool audit
feat(storage): persist evolution lineage and budgets
feat(storage): persist procedure lifecycle
```

## Milestone 6

```text
feat(procedure): mine reusable workflows from traces
feat(procedure): synthesize constrained procedure candidates
feat(evolution): evaluate procedure candidates through replay
```

## Milestone 7

```text
feat(policy-evolution): replay runtime-policy candidates
feat(policy-evolution): apply hard safety gates
feat(policy-evolution): persist evaluation results
```

## Milestone 8

```text
feat(deployment): add stable canary routing
feat(deployment): promote active runtime policies
feat(deployment): auto rollback regressed candidates
```

## Milestone 9

```text
feat(evolution): convert production outcomes to experiences
feat(lineage): connect outcomes to candidate lineage
feat(evolution): feed outcomes into next evolution cycle
```

## Milestone 10

```text
ci: add postgres and redis integration jobs
ci: add harness e2e tests
ci: add evolution regression gates
docs: add closed-loop architecture and evidence
```

---

# 20. 最终 Definition of Done

## Harness Closed Loop

- [ ] `/v1/reviews` 自动生成 RiskProfile
- [ ] 每个 Review Resolve ExecutionPolicy
- [ ] AgentRuntime 使用该 Policy
- [ ] Coordinator 根据 Policy 动态选择 Agent
- [ ] Critic/Evidence/Verifier 根据 Policy 动态执行
- [ ] 所有 Agent Tool 经过 GovernedToolRegistry
- [ ] Approval fail-closed
- [ ] Sandbox 真实 Enforcement
- [ ] Tool Timeout 真实 Enforcement
- [ ] Global Tool Budget 生效
- [ ] Failure 进入分类与恢复流程
- [ ] No-progress 触发 Replan / Fallback
- [ ] DecisionTrace 自动生成
- [ ] ReplaySnapshot 自动生成
- [ ] 全部关键状态持久化
- [ ] 服务重启后可恢复

## Self-Evolution Closed Loop

- [ ] Production Outcome 自动转 Experience
- [ ] Experience → Reflection
- [ ] Reflection → Hypothesis
- [ ] Hypothesis → Procedure Candidate
- [ ] Hypothesis → Runtime Policy Candidate
- [ ] Candidate 只能修改白名单字段
- [ ] Safety Boundary 不可被 Evolution 修改
- [ ] Candidate 使用真实 Replay Dataset
- [ ] Baseline / Candidate 使用相同 Snapshot
- [ ] 使用 utility improvement
- [ ] Validation / Holdout 分离
- [ ] Hard Safety Gate 生效
- [ ] Canary 具有真实流量路由
- [ ] Candidate 能真实切换 Active Version
- [ ] Production Outcome 能归因 Candidate
- [ ] Regression 能真实触发 Auto Rollback
- [ ] Lineage 全链路持久化
- [ ] Outcome / Rollback 继续形成下一轮 Experience

---

# 21. 完成后的项目技术定位

完成本文档后，可以准确描述为：

> **A production-oriented self-evolving agent runtime with policy-driven execution, governed tool use, semantic failure recovery, replay-based counterfactual evaluation, restricted procedure evolution, runtime-policy optimization, stable canary deployment, persistent attribution, and automatic rollback.**

中文：

> **基于代码审查业务场景，自研 Policy-driven Agent Harness，对 Agent 的执行拓扑、Tool 权限、执行预算、失败恢复、Sandbox 与验证深度进行统一治理；并构建 Production Experience → Hypothesis → Procedure / Runtime Policy Candidate → Replay → Holdout → Gate → Canary → Production Outcome → Rollback 的受控自进化闭环。**

---

# 22. 完成后的简历描述建议

### Agent Harness

> 设计并实现 Policy-driven Agent Runtime，根据代码变更风险动态控制 Agent 拓扑、执行预算、Tool 权限和验证深度；构建 Tool Governance、Sandbox、Circuit Breaker、Checkpoint/Resume、Failure Taxonomy、No-progress Detection 与 Recovery Policy，实现对非确定性 Agent 执行过程的可靠约束。

### Replay Infrastructure

> 构建生产任务 Replay Harness，持久化 Prompt/Skill/Policy 版本、Context、Tool Observation 与 Decision Trace，支持历史 workload 的 deterministic / counterfactual replay，用于 Agent 行为回归分析及 Evolution Candidate 的 Baseline 对照评测。

### Self-Evolution

> 构建 Experience-driven Self-Evolution Pipeline，从生产失败、用户反馈与 Agent Trace 中生成 Hypothesis，并受限地产生 Procedure Skill 与 Runtime Policy Candidate，通过 Validation/Holdout、Hard Safety Gate、Canary、Production Outcome Attribution 与 Auto Rollback 实现可追踪、可证伪的受控自进化。

---

# 23. 最终判断标准

开发完成后，不应再出现：

```text
模块 A 有测试
模块 B 也有测试
但真实 Review 不会经过 A/B
```

而应该真实形成：

```text
真实用户请求
↓
完整 Harness
↓
真实 Trace
↓
真实 Experience
↓
真实 Evolution Candidate
↓
真实 Replay
↓
真实 Canary
↓
真实 Active Version
↓
真实 Production Outcome
↓
真实 Rollback / Keep
↓
新一轮 Experience
```

达到这一状态后，EvoReview-Agent 才可以真正称为：

# **Closed-loop Self-Evolving Agent System**
