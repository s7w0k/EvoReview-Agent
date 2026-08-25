# Closed-loop Self-Evolving Agent Runtime

EvoReview-Agent 是**以 Code Review 为业务载体、自研 Agent Harness + 持久化控制面**的自进化运行时。它不只做代码评审：请求进来后由风险感知的 `PolicyDeploymentManager` 选择稳定的 Runtime Policy lane（Baseline / Candidate），经由 Safety Floor 门禁得到执行上下文；每次评审的结果（决策 Trace、Replay 快照、工具 Governance、Recovery 事件、生产 Outcome）全部持久化，并回流成 Experience → Hypothesis → Candidate → Replay → Canary → Promote / Rollback 的闭环。控制面支持 SQLite / PostgreSQL 双实现，服务重启后 Canary 分流、Promote 与 Rollback 状态都不丢失。

---

## 1. What is EvoReview-Agent

一个可直接运行的服务：输入统一 diff 或 GitHub `pull_request` webhook，输出结构化 issue、修复建议与测试建议，并具备以下生产能力：

- 风险感知 Runtime Policy：按 diff 的 risk profile（low / high / critical）动态分配 Agent 数量、执行步数、工具调用预算与验证链（Critic / Evidence / Verifier / Sandbox）
- `AgentRuntime` 有界 Agent Loop：PENDING → PLANNING → EXECUTING → REVIEWING → SUCCESS，内置 Tool Registry、参数 Schema 校验、结构化 Observation、checkpoint、预算与断点续跑
- 自进化：确认漏报 → Experience → Hypothesis → Runtime Policy / Skill / 提示词候选 → 真实回放门禁 → Canary → Promote 或自动 Rollback
- 持久化控制面：Policy 部署、决策 Trace、Replay 快照、Outcome 与 Evolution Lineage 数据库化（SQLite / PostgreSQL），重启后状态不丢
- 完整工程：用户登录 / RBAC / 租户隔离、Webhook HMAC 签名、Redis Streams ACK / 租约 / DLQ、OpenTelemetry + Prometheus、可选自动修复分支
- 确定性可复现：内置固定 Benchmark 数据集与回归评估器；无大模型时使用确定性本地规则审查器

## 2. Why Harness

用一个完整、有边界的 Agent Runtime 替换“一次 LLM 调用就出结论”，是为了在**生产可审计、可回滚、可演进**的前提下获得更高召回：

1. **可观测**：每个决策写入 Decision Trace，每个回放存快照，每步工具调用都有结构化 Observation；
2. **可治理**：工具受参数 Schema 与 Side-effect 权限校验，阻塞调用触发超时并进入 Recovery 流程；
3. **可演进**：生产漏报经回放门禁生成候选策略，Canary 观察后决定 Promote 还是 Rollback，而不是一次性把提示词改到全量；
4. **可恢复**：执行超预算、工具阻塞、恢复失败等都有 Recovery 兜底；服务重启后 checkpoint 与部署状态 auto-recover。

## 3. Execution Architecture

```text
 HTTP / GitHub Webhook
        │
        ▼
 ReviewService ── TaskStore(SQLite / PostgreSQL / Redis)
        │
        ▼
 PolicyDeploymentManager.route() ──(baseline / candidate lane, hash(task_id + deployment_id))
        │
        ▼ Safety Floor (critic / evidence / verifier / sandbox) 门禁
 ReviewExecutionContext
        │
        ▼
 ReviewHarness (AgentRuntime / checkpoint / resume / budget / trace)
        │
        ├── DiffParser
        ├── ContextManager (unified token budget / iterative compression)
        ├── MemoryManager (working / episodic / semantic / tenant retrieval / expiry)
        ├── Tool Registry (schema validation / observation / recovery)
        └── MultiAgentCoordinator
              ├── Planner：按语言、文件与风险域分解
              ├── Specialists（并行）：Security / Reliability / LLM / dynamic Skills
              ├── Agent Loop：Plan / Tool / Observe / Final
              ├── Critic → Reflection
              ├── Evidence Agent：独立复核新增行证据
              ├── Verifier：置信度 / 证据 / 修复安全门禁
              └── Arbiter：合并冲突并裁决 findings
```

每次评审的**决策 Trace、Replay 快照、工具 Governance、Recovery 事件、生产 Outcome** 都随任务持久化。

## 4. Self-Evolution Loop

```text
Production Review（真实流量）→ RiskProfile / Policy / DecisionTrace / Outcome
  → confirmed false negative (feedback)          （漏报回流）
  → Experience → Hypothesis
  → Runtime Policy / Skill / Prompt Candidate
  → Replay（baseline vs candidate，同一批 Diff）
  → Hard Safety Gate（Recall / HR-Recall / Critical Miss 非退化）
  → 激活候选 / 记录 Evolution Lineage
```

- `POST /v1/evolution/auto`、`POST /v1/skill-evolution/auto`：从未解决反馈生成并评测候选；
- 候选必须在验证集达到最小提升，且在隐藏集通过分数、召回率、高风险召回率**非退化门禁**；
- 证据：`python scripts/run_prompt_evolution_proof.py` 输出真实版本链与 `evolution_runs`。

## 5. Runtime Policy Canary

```text
DRAFT → REPLAY_PASSED → SHADOW → CANARY（按 hash(task_id + deployment_id) 百分比稳定分流）
        → 100% Promote　／　hard-safety failure → 自动 Rollback → previous-good
```

新 Review 进入稳定 lane：Candidate 未 Promote 前只有部分流量流入，Promote 后全量接管；坏候选触发 hard gate 失败即自动回滚到 previous-good。

## 6. Replay & Safety Gates

- Replay：候选与当前策略回放同一批验证 Diff，逐条对比 findings；
- Hard Gate（确定性，来源 `artifacts/evolution_eval/`）：Critical Misses 上限、High-risk Recall 下限、Recall 与 F1 非退化；
- 隐藏集只持久化聚合指标，不暴露案例明细；评测运行、版本、指标与激活决定全部持久化，可回滚。

## 7. Failure Recovery

Execution 失败走 `RecoveryManager.handle()` → `RecoveryOutcome`：支持 RETRY / 指数退避重试、工具超时与 Side-effect 拒绝、步骤预算耗尽后的重试 / 交接流程，并写出 recovery 事件与 stats；任务失败可从最近 checkpoint 断点续跑。

## 8. Durable Control Plane

`ControlPlaneStore` Protocol（get / put / delete / list / transaction）提供三种实现：

| 实现 | 用途 |
|---|---|
| JSON（开发回退） | 本地演示，重启不持久 |
| SQLite（默认） | 单机生产，重启状态不丢 |
| PostgreSQL | 多实例一致控制面 |

统一持久化：Risk 等级、候选策略与部署状态机、Policy 曝光（exposure）、决策 Trace、Replay 快照、生产 Outcome、Evolution Lineage 与 Skill artifact。服务重启时自动恢复激活的 Canary / Promote / Rollback 状态，多实例依赖同一控制存储保持一致。

## 9. Benchmark Results

固定 Benchmark 数据集 + `scripts/run_evolution_regression_benchmark.py`（确定性、可复现）。演进策略 `local-heuristic-v2` 对基线 `local-heuristic-v1-security` 的硬门禁结果：

**Hard Gate — PASS（candidate safe to promote）**

| Gate | Baseline | Candidate | Pass |
|---|---:|---:|---|
| Critical Misses ≤ | 1 | 0 | ✅ |
| High-risk Recall ≥ | 0.667 | 1.000 | ✅ |
| Recall ≥ | 0.143 | 0.286 | ✅ |
| F1 ≥ | 0.250 | 0.421 | ✅ |

**Metrics（Baseline / Candidate / Delta）**

| Metric | Baseline | Candidate | Delta |
|---|---:|---:|---:|
| F1 | 0.250 | 0.4211 | **+0.1711 (+68%)** |
| Recall | 0.1429 | 0.2857 | **+0.1428 (+100%)** |
| High-risk Recall | 0.6667 | 1.000 | **+0.3333 (+50%)** |
| Critical Misses | 1 | 0 | **-1 (-100%)** |
| True Positives | 2 | 4 | +2 (+100%) |
| False Negatives | 12 | 10 | -2 (-17%) |

完整逐案例报告见 `artifacts/evolution_eval/local-heuristic-v2.md`。CI 中 `benchmark-regression` job 会对每次 push 重跑并上传 `*.json` 制品。

## 10. Demo

`python scripts/run_production_demo.py` 运行 5 个可复现 Demo，产出 `artifacts/demo/production_demo.md`，全部 PASS：

| Demo | 场景 | 结果 | 关键输出 |
|---|---|---|---|
| A | 风险感知 Harness | PASS | low 1 agent / 3 steps / 5 tools vs high 3 agents / 10 steps / 25 tools + critic,evidence,verifier,sandbox |
| B | Tool Governance | PASS | `run_tests` 被 DENY；阻塞工具超时 → `RETRY_WITH_BACKOFF` |
| C | Self-Evolution | PASS | baseline → missed → candidate → **Promote（state=PROMOTED）**，live review 用 v2（`lane=candidate`） |
| D | Auto Rollback | PASS | 坏候选 hard gate 失败 → **ROLLED_BACK** → restart → previous-good（v1）恢复 |
| E | Restart Recovery | PASS | canary → restart → 状态与 lane 保持不变（CANARY / baseline） |

Promote / Rollback 实际输出（摘自 Demo C / D）：

```text
Demo C — baseline -> missed -> candidate -> promote
  deployment state=PROMOTED ; live review policy=baseline-high-raise_max_steps lane=candidate
  lineage chain: EXPERIENCE -> HYPOTHESIS -> CANDIDATE -> EVALUATION -> DEPLOYMENT

Demo D — bad candidate -> previous-good restored
  bad candidate state=ROLLED_BACK (hard-safety gate failed -> auto rollback)
  after rollback + restart, live review policy=baseline-high-raise_max_steps (previous-good)
```

## 11. Running Locally

前置：Python 3.11。

```powershell
python -m pip install -r requirements.txt

$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:EVOAGENT_AUTH_REQUIRED = 'true'
$env:EVOAGENT_AUTH_SECRET = [Convert]::ToBase64String($bytes)
$env:EVOAGENT_BOOTSTRAP_ADMIN_USERNAME = 'admin'
$env:EVOAGENT_BOOTSTRAP_ADMIN_PASSWORD = '<至少 10 个字符的密码>'

python -m evoagent
```

- 默认监听 `127.0.0.1:8080`，无模型时使用确定性本地规则 Agent；
- 模型配置见 **模型配置 / 完整生产模式** 小节（DeepSeek / OpenRouter / custom OpenAI-compatible，`.env` 支持）；
- 完整生产模式：`Copy-Item .env.example .env` + `docker compose up --build`（PostgreSQL + Redis）；
- 运行测试：`python -m unittest discover -s tests -v`。

### API（含 Observability / Evidence 端点）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查 |
| `POST` | `/v1/auth/login` | 登录并获取租户绑定 Bearer Token |
| `POST` | `/v1/reviews`、`/v1/reviews?async=true` | 创建同步 / 异步审查任务 |
| `GET` | `/v1/tasks/{id}` | 状态、轨迹与报告 |
| `GET` | `/v1/tasks/{id}/report` | Markdown 报告 |
| `GET` | `/v1/tasks/{id}/decision-trace` | 决策 Trace 证据导出 |
| `GET` | `/v1/tasks/{id}/replay` | 该任务的 Replay 记录 |
| `POST` | `/v1/tasks/{id}/fix` | 创建自动修复分支和提交 |
| `POST` | `/v1/tasks/{id}/feedback` | 回流误报 / 漏报 / 坏修复 |
| `POST` | `/v1/tasks/{id}/cancel`、`/resume` | 取消 / 从 checkpoint 续跑 |
| `POST` | `/webhooks/github` | GitHub PR webhook |
| `GET/POST` | `/v1/evaluation/cases` | 版本化评测样本 |
| `POST` | `/v1/evolution/auto`、`/propose` | 生成 / 评测提示词候选 |
| `GET` | `/v1/evolution/status`、`/runs` | 评测门禁就绪状态与持久化记录 |
| `GET` | `/v1/evolution/{candidate_id}/lineage` | Evolution Lineage 证据导出 |
| `POST` | `/v1/skills/reload` | 动态重载 Skill |
| `GET/POST` | `/v1/skill-evolution/...` | Skill artifact 进化、状态、版本链与激活 |
| `GET` | `/metrics`、`/api/alerts`、`/api/audit` | Prometheus / 告警 / 审计 |
| `GET/POST` | `/api/queue/dead-letters`、`/v1/queue/dead-letters/replay` | 死信队列 |
| `GET/POST` | `/v1/deployments/{id}/metrics` | 策略部署指标证据导出 |
| `GET` | `/v1/runtime-policies`、`/v1/runtime-policies/{id}` | 风险感知运行时策略 |
| `POST` | `/v1/policy-evolution/propose` | 生成策略候选 |
| `GET/POST` | `/v1/policy-deployments...` | 策略部署清单 / 新建 DRAFT 部署 |
| `POST` | `/v1/policy-deployments/{id}/{replay-pass\|shadow\|canary\|advance}` | 推进部署状态机 |
| `POST` | `/v1/policy-deployments/{id}/promote` | 全量接管 Candidate Policy |
| `POST` | `/v1/policy-deployments/{id}/rollback` | 回滚到 previous-good policy |

## 12. CI

Main 分支要求全部绿色。结构对齐计划 §16：

```text
quality            ——  lint（ruff core）、typecheck（mypy core）、coverage（core ≥ 85%）、unit（3.x 矩阵）
integration        ——  sqlite（默认控制面全量）、postgres（16 service container）、redis（7 service container）
agent-system       ——  harness-e2e、closed-loop-e2e（生产闭环）/ evolution-regression（Known-Good fixtures）、benchmark-regression（Phase 9 gate）
```

核心证据指标：

- **coverage ≥ 85%**（scoped 核心模块）；
- **lint / typecheck 全绿**（核心闭环 / control-plane 模块，`ci.yml` 统一装 `.[dev]`）；
- **harness-e2e / closed-loop-e2e / evolution-regression / benchmark-regression** 每次 push 直达 main 上重跑，bad candidate 会被 hard gate 拒之门外。

---

完整工程化收尾计划与逐项验收见 `docs/EvoReview-Agent_Final_Engineering_Hardening_Plan.md`。