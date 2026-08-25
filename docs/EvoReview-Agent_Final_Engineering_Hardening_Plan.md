# EvoReview-Agent 最终工程化收尾实施计划
## Green CI + Durable Control Plane + Benchmark / Demo + Production-grade Evidence

> 目标：当前项目的 Harness、自进化、Canary、Outcome 与 Lineage 已经基本闭环。接下来不再横向增加功能，而是完成 CI 全绿、控制面数据库化、真实 Benchmark、可复现 Demo 与 README/简历证据固化。

---

# 1. 最终 Definition of Done

完成后必须同时满足四点：

1. 真实 Review 主链路继续保持：
   `Review → RiskProfiler → PolicyDeploymentManager → Baseline/Candidate → ExecutionPolicy → Harness → GovernedToolRegistry → Recovery → Trace → Replay → Outcome`。
2. 自进化链路真实完成：
   `Outcome → Experience → Hypothesis → Candidate → Replay → Gate → Canary → Promote/Rollback → New Review`。
3. 关键控制面状态可在 SQLite/PostgreSQL 中持久化，并在服务重启后正确恢复。
4. GitHub Actions 所有核心质量门禁全部为绿色。

---

# 2. 阶段总览

| Phase | 任务 | 优先级 |
|---|---|---:|
| 0 | 修复 CI 依赖与测试执行配置 | P0 |
| 1 | Ruff / Mypy / Coverage 收口 | P0 |
| 2 | SQLite / PostgreSQL / Redis Integration 收口 | P0 |
| 3 | Harness / Evolution / Closed-loop E2E 全绿 | P0 |
| 4 | 抽象 Durable Control Plane | P0 |
| 5 | SQLite Control Plane Adapter | P1 |
| 6 | PostgreSQL Control Plane Adapter | P1 |
| 7 | JSONFileStore 退居 Dev/Test Fallback | P1 |
| 8 | Benchmark Dataset 与 Baseline | P1 |
| 9 | Evolution Regression Benchmark | P1 |
| 10 | Production Demo | P1 |
| 11 | Observability / Evidence Export | P2 |
| 12 | README / Resume Packaging | P2 |

---

# 3. Phase 0：修复 CI 基础设施

## 3.1 统一开发依赖

修改 `pyproject.toml`：

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8",
  "coverage>=7",
  "ruff>=0.6",
  "mypy>=1.11",
  "psycopg2-binary>=2.9",
  "redis>=5"
]
```

如果项目已统一到 `psycopg[binary]`，则不要并存 `psycopg2-binary`。

## 3.2 所有 CI Job 统一安装

统一执行：

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

不要再让不同 Job 各自零散安装依赖，避免出现 `No module named pytest`。

## 3.3 Closed-loop E2E

保证以下测试真正执行：

```bash
pytest tests/e2e/test_service_policy_deployment.py -q
pytest tests/e2e/test_service_self_evolution_production_loop.py -q
pytest tests/e2e/test_service_deployment_api.py -q
```

---

# 4. Phase 1：Ruff / Mypy / Coverage

## 4.1 Ruff

先覆盖核心闭环模块：

```bash
ruff check   evoagent/policy   evoagent/policy_evolution   evoagent/tools   evoagent/recovery   evoagent/replay   evoagent/procedure   evoagent/outcome_evolution   evoagent/evolution_gov   evoagent/storage   evoagent/execution   tests/e2e
```

再逐步扩大到：

```bash
ruff check evoagent tests
```

对于 observability、audit、best-effort persistence 中必要的 broad exception，保留并明确注释原因，不要为了 lint 破坏容错。

## 4.2 Mypy

第一阶段只检查闭环核心：

```bash
mypy   evoagent/policy   evoagent/policy_evolution   evoagent/tools   evoagent/recovery   evoagent/replay   evoagent/procedure   evoagent/outcome_evolution   evoagent/storage
```

优先修正：
- Optional 类型；
- Repository 接口；
- ExecutionPolicy codec；
- Outcome / Deployment / Replay Metrics；
- `Dict[str, Any]` 无限制扩散。

建议为 Repository 定义 Protocol。

## 4.3 Coverage

不要用“全仓 85%”作为唯一目标，而应针对核心 Harness / Evolution 模块：

```bash
coverage run -m pytest tests -q
coverage report   --include="evoagent/policy/*,evoagent/policy_evolution/*,evoagent/tools/*,evoagent/recovery/*,evoagent/replay/*,evoagent/procedure/*,evoagent/storage/*"   --fail-under=85
```

目标：
- Core Harness >= 85%
- Policy Evolution >= 85%
- Replay / Recovery >= 85%

---

# 5. Phase 2：Integration Tests

## 5.1 Pytest Markers

在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
markers = [
  "integration: external dependency integration tests",
  "sqlite: sqlite integration tests",
  "postgres: postgres integration tests",
  "redis: redis integration tests",
  "e2e: end-to-end tests"
]
```

## 5.2 SQLite

不要再用：

```bash
pytest tests -m "not integration"
```

来冒充 SQLite integration。

建立：

```text
tests/integration/test_sqlite_store.py
tests/integration/test_control_plane_restart.py
```

执行：

```bash
pytest tests/integration -m sqlite -q
```

## 5.3 PostgreSQL

GitHub Actions 启动 PostgreSQL 16，并配置 health check。

测试应覆盖：
- task persistence；
- checkpoint；
- runtime policy；
- deployment；
- service restart；
- active policy restore。

## 5.4 Redis

测试：
- enqueue；
- lease；
- retry；
- visibility timeout；
- dead-letter；
- worker restart。

---

# 6. Phase 3：E2E 全绿

## 6.1 Harness E2E

必须真实经过：

```text
ReviewService
→ ReviewHarness
→ MultiAgentCoordinator
→ ExecutionPolicy
→ GovernedToolRegistry
→ Recovery
→ DecisionTrace
→ ReplaySnapshot
```

## 6.2 Evolution Regression

固定：
- Known Good Candidate → PASS；
- Known Bad Candidate → Hard Gate REJECT。

Known Bad 至少包含 `critical miss`。

## 6.3 Closed-loop E2E

必须验证：

```text
Baseline
→ Candidate
→ Replay
→ Canary
→ Promote
→ New Review uses Candidate
→ Restart
→ Candidate still active
→ Bad Candidate
→ Regression
→ Rollback
→ Restart
→ Previous-good policy restored
```

---

# 7. Phase 4：抽象 Durable Control Plane

新增：

```text
evoagent/storage/control_plane.py
```

接口：

```python
class ControlPlaneStore(Protocol):
    def get(self, collection, key): ...
    def put(self, collection, key, value): ...
    def delete(self, collection, key): ...
    def list(self, collection): ...
    def transaction(self): ...
```

Repository 不应感知底层是 JSON、SQLite 还是 PostgreSQL。

最终实现：

```text
JSONControlPlaneStore
SQLiteControlPlaneStore
PostgresControlPlaneStore
```

---

# 8. Phase 5：SQLite Control Plane

SQLite 作为默认开发 / 单机后端。

建议表：

```text
runtime_policy_versions
policy_deployments
policy_exposures
decision_trace_events
replay_snapshots
replay_observations
production_outcomes
evolution_lineage
evolution_budget_usage
policy_candidate_evaluations
```

关键要求：
- Promote / Rollback 事务化；
- WAL 模式；
- Exposure `(deployment_id, task_id)` 唯一；
- Replay Observation 保留 occurrence index；
- Restart 后恢复 active policy / canary stage。

增加：

```text
scripts/migrate_control_json_to_sqlite.py
```

迁移：
`JSON → validate → insert → count verify → backup original`。

---

# 9. Phase 6：PostgreSQL Control Plane

PostgreSQL 作为推荐生产后端。

重点：
- JSONB 保存 Policy / Trace / Outcome payload；
- `SELECT ... FOR UPDATE` 或 optimistic version 保护 Promote/Rollback；
- tenant + repo + risk scope 下只能有一个 ACTIVE policy；
- exposure 写入幂等；
- 多实例共享同一 active deployment。

必须增加 multi-instance test：

```text
Service A
Service B
↓
same PostgreSQL
↓
same active policy
```

---

# 10. Phase 7：JSONFileStore 降级为 Fallback

配置：

```text
CONTROL_PLANE_BACKEND=json
CONTROL_PLANE_BACKEND=sqlite
CONTROL_PLANE_BACKEND=postgres
```

推荐：
- 默认开发：sqlite；
- 生产：postgres；
- JSON：smoke/debug/test fallback。

---

# 11. Phase 8：建立 Benchmark Dataset

目录：

```text
benchmarks/
├── security/
├── reliability/
├── correctness/
└── regression/
```

建议 30–50 个固定 Case。

分布示例：
- Security 15
- Reliability 10
- Correctness 10
- No-Issue 10

每个 Case：

```json
{
  "case_id": "SEC-001",
  "repository": "fixture/security",
  "diff": "...",
  "expected_findings": [
    {
      "rule_id": "SEC-SHELL",
      "path": "app.py",
      "line": 10,
      "severity": "high"
    }
  ],
  "risk_level": "high"
}
```

必须包含 Negative Case，否则无法评估 False Positive。

---

# 12. Phase 9：Evolution Regression Benchmark

每次 Candidate 输出：

```text
Precision
Recall
F1
High-risk Recall
Critical Misses
False Positives
Task Success Rate
Tool Calls
Agent Steps
Latency
Cost
Recovery Rate
Policy Violations
```

必须给出 Baseline / Candidate / Delta。

生成：

```text
artifacts/evolution_eval/<candidate_id>.json
artifacts/evolution_eval/<candidate_id>.md
```

Hard Gate 优先于总体 utility。

---

# 13. Phase 10：Production Demo

至少准备五个 Demo。

## Demo A：Risk-aware Harness

Low-risk：
- fewer agents；
- fewer steps；
- lower cost。

High-risk：
- security；
- critic；
- evidence；
- verifier。

## Demo B：Tool Governance

```text
side-effect tool
→ no approval
→ DENY
```

以及：

```text
blocking tool
→ timeout
→ Recovery
```

## Demo C：Self-Evolution

```text
Baseline v1
→ missed issue
→ Experience
→ Hypothesis
→ Candidate v2
→ Replay improves
→ Canary
→ Promote
→ new Review uses v2
```

## Demo D：Auto Rollback

```text
Candidate v3
→ critical miss
→ hard safety failure
→ rollback
→ v2 restored
```

## Demo E：Restart Recovery

```text
Canary 25%
→ restart
→ deployment restored
→ same task same lane
```

---

# 14. Phase 11：Observability / Evidence Export

建议指标：

```text
review_tasks_total
review_success_total
review_failure_total
agent_steps_total
tool_calls_total
tool_denied_total
tool_timeout_total
recovery_attempt_total
recovery_success_total
policy_canary_tasks_total
policy_promotions_total
policy_rollbacks_total
evolution_candidates_total
evolution_candidate_pass_total
evolution_candidate_reject_total
```

统一关联：
- task_id
- deployment_id
- candidate_id
- policy_id
- procedure_version

增加查询接口：

```text
GET /v1/tasks/{task_id}/decision-trace
GET /v1/tasks/{task_id}/replay
GET /v1/deployments/{id}/metrics
GET /v1/evolution/{candidate_id}/lineage
```

---

# 15. Phase 12：README 最终重构

README 首页建议直接定位：

# Closed-loop Self-Evolving Agent Runtime

结构：

```text
1. What is EvoReview-Agent
2. Why Harness
3. Execution Architecture
4. Self-Evolution Loop
5. Runtime Policy Canary
6. Replay & Safety Gates
7. Failure Recovery
8. Durable Control Plane
9. Benchmark Results
10. Demo
11. Running Locally
12. CI
```

必须展示：
1. Production Agent Harness 图；
2. Self-Evolution Closed Loop 图；
3. Canary Deployment 图；
4. Benchmark 表；
5. Promote / Rollback 实际输出。

Benchmark 表示例：

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| F1 | 0.82 | 0.86 | +0.04 |
| High-risk Recall | 0.91 | 0.96 | +0.05 |
| Tool Calls | 8.4 | 7.1 | -15% |
| Latency | 10.2s | 8.8s | -14% |
| Critical Miss | 0 | 0 | 0 |

---

# 16. 推荐最终 CI 结构

```text
quality
├── lint
├── typecheck
└── unit

integration
├── sqlite
├── postgres
└── redis

agent-system
├── harness-e2e
├── evolution-regression
└── closed-loop-e2e
```

Main branch 要求全部绿色。

---

# 17. 推荐 Commit 顺序

```text
1. ci: install unified development dependencies across jobs
2. fix(ci): stabilize pytest markers and integration test selection
3. style: make core closed-loop modules pass ruff
4. typing: make harness and evolution control plane pass mypy
5. test: raise core harness coverage above 85 percent
6. test: stabilize sqlite postgres and redis integration jobs
7. test: make harness evolution and closed-loop e2e gates green
8. refactor(storage): introduce control plane store abstraction
9. feat(storage): add sqlite durable control plane
10. feat(storage): add postgres durable control plane
11. chore(storage): move json control store to development fallback
12. feat(benchmark): add fixed code-review benchmark dataset
13. feat(evolution): export baseline-candidate regression reports
14. docs: add production closed-loop demo and benchmark evidence
```

---

# 18. Final Acceptance Test

项目正式收尾前逐项验证：

## CI
- [ ] 所有 Job 全绿

## Cold Start
- [ ] 空数据库启动
- [ ] baseline 自动 bootstrap
- [ ] review 成功

## Restart
- [ ] Canary 启动
- [ ] 服务重启
- [ ] Canary stage 恢复

## Promote
- [ ] Candidate promoted
- [ ] 服务重启
- [ ] Candidate 仍 active

## Rollback
- [ ] Bad candidate 产生 critical regression
- [ ] 自动 rollback
- [ ] 服务重启
- [ ] previous-good active

## Multi-instance
- [ ] Service A / B 共享 PostgreSQL
- [ ] 读取同一 active deployment

## Replay
- [ ] 历史 Snapshot 可重新读取
- [ ] ordered observations 顺序一致

## Benchmark
- [ ] Baseline metrics
- [ ] Candidate metrics
- [ ] Delta
- [ ] Hard Gate decision

---

# 19. 完成后的目标评分

| 维度 | 目标 |
|---|---:|
| Agent Runtime / Harness | 9.5 |
| Tool Governance | 9.3 |
| Failure Recovery | 9.1 |
| Replay / Trace | 9.3 |
| Procedure Evolution | 9.1 |
| Runtime Policy Evolution | 9.4 |
| Self-Evolution Closed Loop | 9.4 |
| Production Engineering | 9.2 |
| Observability / Evaluation | 9.0 |
| Resume Differentiation | 9.5 |
| 综合 | 约 9.3/10 |

---

# 20. 完成后的简历定位

## 项目标题

**EvoReview-Agent — Policy-driven Closed-loop Self-Evolving Agent Runtime**

## Harness

设计并实现 Policy-driven Agent Harness，根据代码变更风险动态控制 Agent 拓扑、验证深度、执行预算与 Tool 权限；统一接入 Sandbox、Approval、Circuit Breaker、Failure Taxonomy、Semantic Recovery、Checkpoint/Resume 与 Decision Trace，实现对 Agent 非确定性执行过程的运行时治理。

## Replay

构建 Replay Infrastructure，持久化 Runtime Policy、Prompt/Skill 版本、Context、Tool Observation 与 Decision Trace，在同一历史 workload 上对 Baseline 与 Candidate 进行 counterfactual evaluation，并将 High-risk Recall、Critical Miss、成本与延迟纳入 Hard Safety Gate。

## Self-Evolution

构建 Experience-driven Self-Evolution Pipeline，从生产反馈、失败和执行 Trace 中形成 Hypothesis，受限地产生 Procedure Skill 与 Runtime Policy Candidate，经 Replay/Holdout、Hard Gate、Stable-hash Canary、Production Outcome Attribution 与 Auto Rollback 完成可追踪、可证伪的闭环进化。

## Engineering

建设 SQLite/PostgreSQL Durable Control Plane 与 Redis Queue，持久化 Policy Deployment、Exposure、Replay、Outcome 与 Evolution Lineage；通过 GitHub Actions 覆盖 Unit、Lint、Type Check、Coverage、PostgreSQL/Redis Integration、Harness E2E 与 Closed-loop Regression，保证进化闭环可重启恢复并持续回归验证。

---

# 21. 最后的收尾判断标准

本轮完成后，不再以“还能增加多少功能”为判断依据，而只看：

```text
CI 是否全绿
控制面是否数据库化
Restart 是否可靠
多实例是否一致
Benchmark 是否可复现
Candidate 是否有真实指标提升
Rollback 是否恢复 previous-good
README 是否有数据和运行证据
```

如果全部成立，项目即可正式收尾，并作为一个完成度很高的：

# Agent Harness + Self-Evolution 深度简历项目

进行展示。
