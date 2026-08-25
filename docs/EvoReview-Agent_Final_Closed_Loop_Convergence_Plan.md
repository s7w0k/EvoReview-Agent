# EvoReview-Agent 最终闭环收口实施计划
## Deployment Integration + Durable Control Plane + Green CI

> **目标**：当前项目已经具备较完整的 Harness、Replay、Procedure Evolution、Runtime Policy Evolution、Canary、Outcome 与 Lineage 能力。本阶段不再扩展新能力，而是完成最后三个决定项目能否真正称为 **Closed-loop Self-Evolving Agent System** 的收口任务：
>
> 1. 让 `PolicyDeploymentManager` 真正接管 `ReviewService` 的 Runtime Policy 选择；
> 2. 让 Runtime Policy / Deployment / Trace / Replay / Evolution Governance 等控制面状态真正持久化；
> 3. 让 CI 全绿，并通过 Service-level E2E 证明真实闭环。

---

# 1. 最终 Definition of Done

完成后，真实生产路径必须是：

```text
POST /v1/reviews
        ↓
ReviewService
        ↓
DiffParser
        ↓
RiskProfiler
        ↓
PolicyDeploymentManager.resolve_policy(...)
        ↓
baseline / candidate stable lane
        ↓
ExecutionPolicy
        ↓
ReviewExecutionContext
        ↓
ReviewHarness
        ↓
MultiAgentCoordinator
        ↓
GovernedToolRegistry
        ↓
Recovery / DecisionTrace / ReplaySnapshot
        ↓
Production Outcome
```

同时，Runtime Policy Evolution 必须形成：

```text
Experience
↓
Hypothesis
↓
Runtime Policy Candidate
↓
Replay
↓
Validation / Holdout
↓
Hard Safety Gate
↓
DeploymentManager.create()
↓
REPLAY_PASSED → SHADOW → CANARY
↓
5% → 10% → 25% → 50% → 100%
↓
PROMOTED
↓
新 /v1/reviews 真实使用 candidate policy
↓
Production Outcome
↓
Regression Monitor
↓
KEEP / ROLLBACK
↓
新 /v1/reviews 使用正确 active policy
```

以下状态服务重启后必须仍然存在：Runtime Policy Version、Active Policy、Deployment、Canary Stage、Exposure、DecisionTrace、ReplaySnapshot、Procedure Version、Failure/Recovery Event、Tool Audit、Evolution Candidate/Evaluation/Lineage/Budget、Production Outcome。

CI 必须全部为绿色：

```text
unit                  PASS
lint                  PASS
typecheck             PASS
coverage              PASS
sqlite-integration    PASS
postgres-integration  PASS
redis-integration     PASS
harness-e2e           PASS
evolution-regression  PASS
closed-loop-e2e       PASS
```

---

# 2. 实施阶段总览

| Phase | 任务 | 优先级 |
|---|---|---:|
| 0 | 修 CI 基础配置问题 | P0 |
| 1 | DeploymentManager 接管 ReviewService | P0 |
| 2 | Runtime Policy Repository 持久化 | P0 |
| 3 | Deployment / Exposure 持久化 | P0 |
| 4 | DecisionTrace / Replay 持久化 | P0 |
| 5 | Evolution Governance / Outcome 持久化 | P0 |
| 6 | Service-level Deployment API | P1 |
| 7 | Restart Recovery | P1 |
| 8 | CI 全绿 | P1 |
| 9 | Production Closed-loop E2E | P1 |
| 10 | README / Resume Evidence | P2 |

本阶段明确不再新增 Specialist、语言、Review Rule、复杂 RAG、新模型 Provider 或 UI 大改。

---

# 3. Phase 0：先修 CI

## 3.1 统一 Dev Dependencies

在 `pyproject.toml` 增加：

```toml
[project.optional-dependencies]
dev = [
    "pytest",
    "coverage",
    "ruff",
    "mypy",
    "psycopg[binary]",
    "redis",
]
```

CI 所有 Python Job 统一：

```bash
python -m pip install -e ".[dev]"
```

避免 `harness-e2e` 当前调用 pytest 但没有安装 pytest 的问题。

## 3.2 Pytest Marker

在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
markers = [
  "integration: integration tests",
  "postgres: postgres integration tests",
  "redis: redis integration tests",
  "e2e: end-to-end tests",
]
```

## 3.3 PostgreSQL / Redis readiness

Postgres job 增加：

```bash
pg_isready -h localhost -p 5432
```

Redis job 增加：

```bash
redis-cli -h localhost ping
```

并明确环境变量：

```text
EVOAGENT_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/evoagent
EVOAGENT_REDIS_URL=redis://localhost:6379/0
```

## 3.4 Coverage 收敛到核心模块

不要为了全仓 85% 而降低质量标准。将 85% Gate 精确作用于核心 Harness/Evolution：

```text
evoagent/policy
evoagent/tools
evoagent/recovery
evoagent/replay
evoagent/procedure
evoagent/policy_evolution
evoagent/outcome_evolution
evoagent/decision_trace
```

## 3.5 Mypy 先覆盖核心模块

```bash
mypy evoagent/policy evoagent/tools evoagent/recovery \
     evoagent/replay evoagent/procedure evoagent/policy_evolution \
     evoagent/outcome_evolution evoagent/decision_trace
```

完成后再逐步扩大到全仓。

---

# 4. Phase 1：PolicyDeploymentManager 接管 ReviewService

这是当前最重要的改造。

当前真实路径仍然主要是：

```text
RiskProfiler → PolicyResolver → ExecutionPolicy
```

必须升级为：

```text
RiskProfiler
↓
Resolve Baseline Policy
↓
PolicyDeploymentManager.resolve_policy(...)
↓
baseline / candidate lane
↓
Safety Floor final enforcement
↓
ReviewExecutionContext
```

## 4.1 ReviewService 初始化

新增：

```python
self.policy_deployment_manager = PolicyDeploymentManager(
    repo=self.policy_deployment_repository,
    exposure_repo=self.policy_exposure_repository,
)
```

## 4.2 Baseline Bootstrap

系统启动时确保存在：

```text
baseline-low
baseline-medium
baseline-high
baseline-critical
```

如果数据库已经有 active baseline，则恢复数据库版本，不要每次启动创建新版本。

## 4.3 修改 `_resolve_execution_context()`

推荐逻辑：

```python
parsed = parse_unified_diff(diff)
risk = self.risk_profiler.profile(parsed)

baseline = self.policy_repository.active_policy(
    tenant_id, repository, risk.level
)

if baseline is None:
    baseline = self.policy_resolver.resolve(
        {"task_id": task_id, "tenant_id": tenant_id},
        risk_profile=risk,
    )
    self.policy_repository.ensure_baseline(
        baseline, tenant_id, repository, risk.level
    )

self.policy_deployment_manager.register_policy(baseline)

policy = self.policy_deployment_manager.resolve_policy(
    tenant_id,
    repository,
    risk.level,
    task_id,
)

policy = self.policy_resolver.enforce_safety_floor(policy, risk)
```

随后构造 `ReviewExecutionContext`。

## 4.4 扩展 ReviewExecutionContext

增加：

```python
deployment_id: str | None
deployment_lane: str
baseline_policy_version: int | None
candidate_policy_version: int | None
traffic_share: float | None
```

Replay、Outcome、Lineage 全部继承这些 attribution。

## 4.5 Task 保存 Policy Exposure

保存：

```json
{
  "policy_id": "runtime-high-v8",
  "policy_version": 8,
  "deployment_id": "dep-001",
  "lane": "candidate",
  "traffic_share": 0.1
}
```

## 4.6 Service E2E

新增：

```text
tests/e2e/test_service_policy_deployment.py
```

必须验证：

1. 无 deployment → baseline；
2. Canary 5% 时部分 task_id 进入 candidate；
3. 相同 task_id retry 始终同 lane；
4. Promote 后新任务 100% candidate；
5. Rollback 后新任务恢复 baseline。

---

# 5. Phase 2：Runtime Policy Repository 持久化

当前内存型 `RuntimePolicyRepository` 必须替换成真正 store-backed repository。

## 5.1 Repository Interface

```python
class RuntimePolicyRepository(Protocol):
    def save_policy(...): ...
    def get_policy(...): ...
    def list_versions(...): ...
    def get_active(...): ...
    def activate(...): ...
    def rollback(...): ...
```

## 5.2 SQLite / PostgreSQL 表

```sql
CREATE TABLE runtime_policy_versions (
    id INTEGER PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    repository_scope TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    parent_version INTEGER,
    content_json TEXT NOT NULL,
    status TEXT NOT NULL,
    hypothesis_id TEXT,
    created_at TEXT NOT NULL,
    activated_at TEXT,
    UNIQUE(tenant_id, repository_scope, policy_id, version)
);
```

PostgreSQL 中建议 `content_json` 使用 JSONB。

## 5.3 Policy Codec

新增：

```text
evoagent/policy/codec.py
```

统一：

```python
policy_to_dict()
policy_from_dict()
```

不要让 SQLite/Postgres adapter 各自维护序列化逻辑。

## 5.4 Atomic Activation

Promote 必须在 transaction 中完成：

```text
old active → superseded
candidate → active
deployment → promoted
commit
```

任何 scope 同时只能存在一个 active runtime policy。

---

# 6. Phase 3：Deployment / Exposure 持久化

新增表：

```text
policy_deployments
policy_exposures
```

`policy_deployments` 至少包含：

```text
deployment_id
tenant_id
repository
risk_level
baseline_policy_id
baseline_version
candidate_policy_id
candidate_version
state
stage_index
traffic_share
hypothesis_id
created_at
stage_entered_at
promoted_at
rollback_at
rollback_reason
```

`policy_exposures`：

```text
task_id
deployment_id
lane
baseline_version
candidate_version
traffic_share
created_at
```

增加唯一约束：

```text
(task_id, deployment_id)
```

这样 retry 不会重复污染 exposure metrics。

## 6.1 Restart Restore

Service 启动：

```python
self.policy_deployment_manager.restore_active_deployments()
```

恢复：

```text
CANARY deployment
traffic stage
baseline / candidate policy
active scope pointer
```

---

# 7. Phase 4：DecisionTrace / Replay 持久化

## 7.1 Decision Trace

替换当前内存 `_traces`。

表：

```text
decision_trace_events
```

字段：

```text
task_id
sequence
step_id
action_type
agent_id
policy_id
tool
arguments_hash
observation_hash
input_context_hash
token_usage
cost
latency
failure
recovery_action
data_json
created_at
```

唯一：

```text
(task_id, sequence)
```

## 7.2 ReplaySnapshot

表：

```text
replay_snapshots
replay_tool_observations
```

Snapshot 必须保存：

```text
task_id
tenant
repo
diff hash
policy id/version
deployment id/lane
prompt version
skill versions
model
expected output
created_at
```

Observation 必须保存：

```text
fingerprint
occurrence_index
```

解决相同 tool + args 多次调用时 deterministic replay 的顺序问题。

---

# 8. Phase 5：Evolution Control Plane 持久化

## 8.1 Lineage

```text
evolution_lineages
evolution_lineage_nodes
```

Node：

```text
candidate_id
stage
node_id
source_refs_json
payload_json
sequence
created_at
```

## 8.2 Evolution Budget

新增：

```text
evolution_budget_usage
```

按 tenant/date 保存：

```text
candidates
replay_cases
evaluation_cost
activations
active_experiments
```

## 8.3 Outcome

新增：

```text
production_outcomes
```

保存：

```text
task_id
tenant
repository
risk_level
kind
prompt_version
rule_skill_version
procedure_version
runtime_policy_version
deployment_id
deployment_lane
metrics_json
finding_json
created_at
```

## 8.4 Candidate / Evaluation

至少：

```text
policy_candidates
policy_candidate_evaluations
```

否则重启后无法继续 candidate lifecycle。

---

# 9. Phase 6：Service-level Deployment API

建议增加：

```text
GET  /v1/runtime-policies
GET  /v1/runtime-policies/{id}
GET  /v1/policy-deployments
GET  /v1/policy-deployments/{id}

POST /v1/policy-evolution/propose
POST /v1/policy-evolution/{candidate_id}/evaluate

POST /v1/policy-deployments/{id}/shadow
POST /v1/policy-deployments/{id}/canary
POST /v1/policy-deployments/{id}/advance
POST /v1/policy-deployments/{id}/promote
POST /v1/policy-deployments/{id}/rollback
```

所有写操作必须经过：

```text
RBAC
Tenant Scope
Audit
Safety Validation
```

第一阶段可以人工推进 5%→10%→25% 等 stage，但自动 rollback 必须真实生效。

---

# 10. Phase 7：Restart Recovery

新增：

```text
tests/integration/test_control_plane_restart.py
```

Case A：

```text
baseline v1
candidate v2
start canary 10%
产生 exposure
destroy ReviewService
recreate ReviewService
恢复 DB
相同 task_id 仍同 lane
新 task 仍按 10% 分流
promote
restart
新 task 100% v2
```

Case B：

```text
candidate canary
hard safety regression
rollback
restart
candidate 仍 disabled
previous-good policy active
```

---

# 11. Phase 8：CI 全绿

最终 workflow 建议：

```text
unit
lint
typecheck-core
coverage-core
sqlite-integration
postgres-integration
redis-integration
harness-e2e
evolution-regression
closed-loop-e2e
```

`evolution-regression` 必须验证：

```text
Known Good → PASS
Known Bad → Hard Gate REJECT
```

`closed-loop-e2e` 单独运行：

```bash
pytest tests/e2e/test_service_policy_deployment.py -q
pytest tests/e2e/test_service_self_evolution_production_loop.py -q
```

---

# 12. Phase 9：最终 Service-level Production Closed Loop E2E

新增：

```text
tests/e2e/test_service_self_evolution_production_loop.py
```

必须完整验证以下 20 步：

1. 启动真实 `ReviewService`，使用临时 SQLite；
2. 发起真实 Review；
3. 自动产生 RiskProfile、Policy、DecisionTrace、ReplaySnapshot、Outcome；
4. 注入 confirmed false negative；
5. 生成 Experience；
6. 生成 Hypothesis；
7. 生成 Runtime Policy Candidate；
8. Replay baseline vs candidate；
9. Validation/Holdout + Hard Gate PASS；
10. 创建 Deployment；
11. DRAFT → REPLAY_PASSED → SHADOW → CANARY；
12. 新 Review 真实经过 `ReviewService → PolicyDeploymentManager` 进入 candidate lane；
13. 记录 Production Outcome；
14. Promote candidate；
15. 新 Review 100% 使用 candidate；
16. 重启 Service；
17. 新 Review 仍使用 candidate；
18. 创建 bad candidate，Canary 中触发 hard safety failure；
19. Auto rollback，重启后仍使用 previous-good policy；
20. 从数据库查询完整 Lineage：EXPERIENCE → REFLECTION → HYPOTHESIS → CANDIDATE → EVALUATION → DEPLOYMENT → OUTCOME。

只有这个测试通过，才能正式把项目称为 **Production Closed-loop Self-Evolving Agent System**。

---

# 13. 推荐文件级修改清单

修改：

```text
evoagent/service.py
evoagent/execution/context.py
evoagent/policy/repository.py
evoagent/policy/resolver.py
evoagent/policy_evolution/deployment.py
evoagent/policy_evolution/pipeline.py
evoagent/decision_trace/repository.py
evoagent/replay/repository.py
evoagent/replay/builder.py
evoagent/outcome_evolution/store.py
evoagent/evolution_gov/lineage.py
evoagent/evolution_gov/budget.py
.github/workflows/ci.yml
pyproject.toml
```

新增：

```text
evoagent/policy/codec.py
evoagent/storage/repositories/runtime_policy.py
evoagent/storage/repositories/policy_deployment.py
evoagent/storage/repositories/policy_exposure.py
evoagent/storage/repositories/decision_trace.py
evoagent/storage/repositories/replay.py
evoagent/storage/repositories/outcome.py
evoagent/storage/repositories/lineage.py
evoagent/storage/repositories/evolution_budget.py

tests/e2e/test_service_policy_deployment.py
tests/e2e/test_service_self_evolution_production_loop.py
tests/integration/test_control_plane_restart.py
```

---

# 14. 推荐 Migration 顺序

```text
010_runtime_policy_versions.sql
011_policy_deployments.sql
012_policy_exposures.sql
013_decision_trace_events.sql
014_replay_snapshots.sql
015_replay_observations.sql
016_production_outcomes.sql
017_evolution_lineage.sql
018_evolution_budget.sql
019_policy_candidate_evaluations.sql
```

不要一次做一个超大 Migration。

---

# 15. 推荐 Commit 顺序

```text
ci: install unified dev dependencies for validation jobs

feat(deployment): route review tasks through policy deployment manager

feat(storage): persist runtime policy versions

feat(storage): persist canary deployments and exposure lanes

feat(trace): persist decision trace events

feat(replay): persist replay snapshots and ordered observations

feat(evolution): persist production outcomes and lineage

feat(evolution): persist evolution budgets and policy evaluations

feat(service): restore active runtime control plane on startup

test(e2e): verify service-level canary promote and rollback

test(e2e): verify closed loop survives service restart

ci: enforce full green closed-loop quality gates
```

---

# 16. 最终验收 Checklist

## Deployment

- [ ] ReviewService 使用 DeploymentManager 选择 Policy
- [ ] Canary task 真进入 Candidate Policy
- [ ] stable hash retry 不换 lane
- [ ] Promote 后新任务 100% Candidate
- [ ] Rollback 后 Candidate 无流量
- [ ] Restart 后 Deployment 状态不丢

## Persistence

- [ ] Runtime Policy 持久化
- [ ] Deployment 持久化
- [ ] Exposure 持久化
- [ ] Trace 持久化
- [ ] Replay 持久化
- [ ] Outcome 持久化
- [ ] Lineage 持久化
- [ ] Candidate Evaluation 持久化
- [ ] Evolution Budget 持久化

## CI

- [ ] Unit green
- [ ] Ruff green
- [ ] Mypy core green
- [ ] Coverage core >= 85%
- [ ] SQLite integration green
- [ ] PostgreSQL integration green
- [ ] Redis integration green
- [ ] Harness E2E green
- [ ] Evolution Regression green
- [ ] Service Closed-loop E2E green

---

# 17. 完成后的项目定位

完成本计划后，可以准确描述为：

> **A production-oriented closed-loop self-evolving agent runtime, where live review traffic is governed by persistent risk-aware runtime policies, canary-deployed policy candidates are evaluated through replay and hard safety gates, and production outcomes continuously feed an auditable evolution lineage with durable rollback and restart recovery.**

中文：

> **以 Code Review 为业务载体，自研具备持久化控制面的 Policy-driven Agent Harness，通过风险感知 Runtime Policy、Tool Governance、Recovery 与 Replay 管控 Agent 执行，并实现 Runtime Policy Candidate 的 Replay/Hard Gate/Canary/Promote/Rollback 生产闭环；生产 Outcome 持续回流 Experience 与 Evolution Lineage，整个控制面支持服务重启恢复。**

---

# 18. 最终判断标准

本阶段不再看“新增了多少模块”，只看六件事：

1. 真实 `/v1/reviews` 是否真的使用 Candidate Policy；
2. 服务重启后 Canary 是否仍存在；
3. Promote 后新请求是否真的使用新版本；
4. Rollback 后新请求是否真的恢复 previous-good version；
5. Trace / Replay / Outcome / Lineage 是否都能从数据库重新读取；
6. GitHub Actions 是否全部绿色。

当六项全部成立时，EvoReview-Agent 才真正完成从：

```text
Self-Evolution Architecture
```

到：

```text
Production Closed-loop Self-Evolving Agent Runtime
```

的最后一次质变。
