# EvoAgent 完整进化闭环逐步实施方案

## 1. 文档目的

本文面向当前 EvoAgent 代码基线，目标是把现有的：

```text
人工反馈 → Failure Case / Experience → 手动调用 auto_propose
→ Validation / Holdout → 直接激活或人工配置发布 → 人工回滚
```

升级为：

```text
Failure / Feedback / Runtime Signal
  → Experience 聚合与可信度治理
  → 结构化 Reflection / Hypothesis
  → 受限 Candidate 生成
  → 离线 Evaluation 与遗忘/泛化/安全门禁
  → 人工审批或策略审批
  → Shadow
  → Canary
  → Active
  → 持续质量监控
  → Promote / Pause / Rollback / Archive
```

闭环必须满足以下原则：

1. 自动化不等于无约束自治；Agent 只能生成候选，不能绕过门禁直接修改 Active 能力。
2. 所有状态变化均持久化、可重放、可审计、可中止、可恢复。
3. 离线数值提升不是生产激活的充分条件；真实数据来源、Shadow 和 Canary 均为独立门禁。
4. 新能力必须同时证明增益、非退化、成本可接受和权限安全。
5. 任何不确定、样本不足或依赖异常都采用 fail-closed：保存候选，但不发布。
6. Prompt、声明式 Rule Skill、未来 Procedure Skill 和 Tool Proposal 使用同一控制面，但保持不同权限等级。

## 2. 当前基础与主要缺口

### 2.1 可复用的现有能力

| 已有模块 | 可复用能力 |
|---|---|
| `service.py` | 反馈写入、任务执行、Skill 注入、Shadow 调用入口 |
| `experience.py` | 反馈分类、脱敏、指纹和 Experience 构造 |
| `evolution.py` | Prompt 候选、Validation/Holdout 回放、版本与回滚 |
| `skill_evolution.py` | 声明式 Skill 候选、边际指标、历史比较、来源记录 |
| `evaluation_harness.py` | E2E 指标、一对一匹配、数据来源门禁 |
| `memory.py` | Working/Episodic/Semantic 记忆和仓库级召回 |
| `rollout.py` | 确定性 Shadow/Canary 分流和错误预算回滚 |
| `store.py` / `postgres_store.py` | 双后端持久化、审计、版本、部署和 Observation |
| `runtime.py` / `harness.py` | 有界执行、checkpoint、取消和恢复 |
| `metrics.py` / `observability.py` | Prometheus、Trace 和告警基础 |

### 2.2 必须闭合的缺口

1. `continuous_eval_seconds` 仅有配置，没有实际调度控制器。
2. `/v1/evolution/auto` 和 `/v1/skill-evolution/auto` 需要外部主动调用。
3. Runtime Reflection 只指导本轮 Finding 修订，没有形成可持久化的进化 Hypothesis。
4. Prompt/Skill 回放通过后可直接 `active`，没有强制经过 `shadow → canary`。
5. 灰度发布主要围绕 `llm-review`，声明式 Skill 没有统一进入发布控制面。
6. 自动回滚主要看运行错误率，未覆盖误报、漏报、高风险漏检、延迟、成本和人工接受率。
7. 默认保护较弱：反馈可信度、边际门禁、历史比较、冷却期等默认关闭或阈值过低。
8. Holdout 太小，且现有主评测数据以合成 Python 样本为主。
9. 缺少跨仓库、跨语言、时间外推和灾难性遗忘基准。
10. 当前进化运行、部署和运行观测没有统一的 `evolution_job_id`，难以端到端追踪。
11. 主审查链路缺少完整 Token、费用和预算台账。
12. 没有持续运行、备份恢复和业务 SLO 的验收证据。

## 3. 目标架构

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Signal Sources                                                       │
│ task failure / false positive / missed issue / bad fix / accepted   │
│ critic rejection / tool failure / repair failure / quality drift    │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Experience Router + Trust + Corroboration                            │
│ 去重、脱敏、租户隔离、独立任务确认、反馈者可信度、证据聚类             │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Evolution Controller                                                 │
│ 持久化 Job、租约、幂等、预算、暂停、重试、恢复                         │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Reflection / Hypothesis Engine                                       │
│ problem → evidence → root cause → change type → expected effect/risk │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Candidate Builder                                                    │
│ prompt_patch | rule_patch | procedure_proposal | tool_proposal       │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Evaluation Policy                                                    │
│ schema/safety → validation → holdout → history → forgetting          │
│ → generalization → latency/cost → provenance                         │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Unified Deployment                                                   │
│ validated → shadow → canary(5/20/50%) → active                       │
└──────────────────────────────┬───────────────────────────────────────┘
                               ↓
┌──────────────────────────────────────────────────────────────────────┐
│ Online Quality Monitor                                               │
│ error / FP / acceptance / high-risk miss / latency / cost / drift    │
└──────────────────────┬───────────────────────────────┬───────────────┘
                       ↓                               ↓
                    promote                         rollback
```

## 4. 统一状态模型

### 4.1 Experience 状态

保留现有状态并扩展为：

```text
observed
  → corroborated
  → hypothesized
  → candidate_created
  → consumed

observed/corroborated
  → rejected
  → expired
```

规则：

- 单任务重复信号只计一次。
- `missed_issue` 默认至少来自 2 个独立任务，或由有权限的人工明确确认。
- 恶意、低可信、证据缺失的反馈只能进入 `observed` 或 `rejected`，不能生成候选。
- Experience 被候选使用后不删除；通过 `candidate_run_id` 保留精确来源。

### 4.2 Hypothesis 状态

```text
draft → reviewed → approved → materialized
  └──────────────→ rejected
  └──────────────→ expired
```

Hypothesis 是 Reflection 的结构化输出，至少包含：

- `problem_type`
- `failure_signature`
- `evidence_ids`
- `root_cause`
- `change_type`
- `expected_effect`
- `affected_domains`
- `risk_level`
- `permissions_required`
- `evaluation_requirements`
- `rationale`

禁止把原始用户反馈直接拼接成 Prompt 指令或可执行代码。

### 4.3 Candidate 生命周期

统一 Prompt 和 Skill 的概念状态：

```text
draft
  → quarantined
  → evaluating
  → validated
  → shadow
  → canary
  → active
  → stale
  → archived

任意未通过门禁状态 → rejected
shadow/canary/active → rolled_back
```

其中：

- `quarantined`：Schema、来源、权限和内容安全检查通过。
- `evaluating`：正在执行可复现离线评测。
- `validated`：离线门禁全部通过，但尚未参与主输出。
- `shadow`：旁路执行，候选结果不影响最终报告。
- `canary`：小比例真实任务使用候选输出。
- `active`：正式稳定版本。
- `rolled_back`：已停止流量，保留证据和版本。
- `stale`：长期无贡献或质量退化，等待归档。

兼容策略：现有 `active` 布尔字段继续保留，只有生命周期进入 `active` 时写为 1；其他状态均为 0。

### 4.4 Evolution Job 状态

```text
pending
  → collecting
  → reflecting
  → building
  → evaluating
  → awaiting_approval
  → deploying_shadow
  → deploying_canary
  → monitoring
  → completed

任意状态 → paused / failed / cancelled / rolled_back
```

每次状态转换都必须写 checkpoint 和审计事件。

## 5. 数据库增量设计

所有变更必须同时实现 SQLite 与 PostgreSQL，并加入 `test_store_contract.py`。

### 5.1 新增 `evolution_jobs`

建议字段：

```sql
id TEXT PRIMARY KEY
tenant_id TEXT NOT NULL
repository_scope TEXT
capability_kind TEXT NOT NULL
capability_name TEXT NOT NULL
trigger_type TEXT NOT NULL
trigger_ref TEXT
idempotency_key TEXT NOT NULL
status TEXT NOT NULL
current_step TEXT NOT NULL
candidate_version INTEGER
evolution_run_id TEXT
lease_owner TEXT
lease_until TEXT
retry_count INTEGER NOT NULL DEFAULT 0
max_retries INTEGER NOT NULL DEFAULT 3
budget_json TEXT NOT NULL
checkpoint_json TEXT NOT NULL
error TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
finished_at TEXT
UNIQUE(tenant_id, idempotency_key)
```

用途：解决自动调度、崩溃恢复、重复触发和全链路关联问题。

### 5.2 新增 `evolution_hypotheses`

```sql
id TEXT PRIMARY KEY
job_id TEXT NOT NULL
tenant_id TEXT NOT NULL
repository_scope TEXT
problem_type TEXT NOT NULL
failure_signature TEXT NOT NULL
root_cause TEXT NOT NULL
change_type TEXT NOT NULL
expected_effect_json TEXT NOT NULL
affected_domains_json TEXT NOT NULL
risk_level TEXT NOT NULL
permissions_json TEXT NOT NULL
evaluation_requirements_json TEXT NOT NULL
rationale TEXT NOT NULL
evidence_ids_json TEXT NOT NULL
status TEXT NOT NULL
reviewed_by TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

### 5.3 新增 `evolution_gate_results`

```sql
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT NOT NULL
candidate_kind TEXT NOT NULL
candidate_name TEXT NOT NULL
candidate_version INTEGER NOT NULL
stage TEXT NOT NULL
gate_name TEXT NOT NULL
baseline_value REAL
candidate_value REAL
threshold_json TEXT NOT NULL
passed INTEGER NOT NULL
evidence_json TEXT NOT NULL
created_at TEXT NOT NULL
```

每个门禁独立存储，不能只把全部内容压入一个 `metrics_json`。

### 5.4 扩展现有版本与运行表

为 `evolution_runs` 追加：

- `tenant_id`
- `job_id`
- `hypothesis_id`
- `status`
- `candidate_kind`
- `approval_status`
- `approved_by`
- `updated_at`

为 `skill_evolution_runs` 追加：

- `job_id`
- `hypothesis_id`
- `status`
- `approval_status`
- `approved_by`
- `updated_at`

为 Prompt `skill_versions` 追加与 `skill_artifact_versions` 对齐的字段：

- `status`
- `origin`
- `provenance_json`
- `patch_json`
- `updated_at`
- `activated_at`
- `archived_at`

### 5.5 扩展 `deployments`

复用现有表，追加：

- `artifact_kind`：`prompt | rule_skill | procedure_skill`
- `job_id`
- `approval_policy`
- `quality_budget_json`
- `stage_started_at`
- `stage_deadline_at`
- `last_gate_result_json`
- `rollback_version`
- `rollback_reason`
- `paused_by`

`skill_name` 继续作为能力唯一名称，因此不需要破坏现有主键。

### 5.6 扩展 `release_observations`

追加：

- `stable_version`
- `candidate_version`
- `metrics_json`
- `latency_ms`
- `cost_estimate`
- `human_label`
- `feedback_category`
- `accepted`
- `evaluated_at`

### 5.7 新增 `usage_events`

用于统一主审查和 Chat 的 Token/费用台账：

```sql
id TEXT PRIMARY KEY
tenant_id TEXT NOT NULL
task_id TEXT
job_id TEXT
agent_name TEXT
provider TEXT
model TEXT
prompt_version TEXT
input_tokens INTEGER NOT NULL
output_tokens INTEGER NOT NULL
estimated_cost REAL
latency_ms REAL NOT NULL
status TEXT NOT NULL
created_at TEXT NOT NULL
```

## 6. 分阶段实施方案

## 工作包 0：冻结兼容契约与增加总开关

### 目标

在不改变当前默认运行路径的前提下，为后续闭环建立可回退边界。

### 实施步骤

1. 在 `config.py` 增加：

```text
EVOAGENT_EVOLUTION_CONTROLLER_ENABLED=false
EVOAGENT_EVOLUTION_TRIGGER_MODE=manual
EVOAGENT_EVOLUTION_APPROVAL_POLICY=always
EVOAGENT_EVOLUTION_PRODUCTION_PROFILE=false
EVOAGENT_EVOLUTION_MAX_CONCURRENT_JOBS=1
EVOAGENT_EVOLUTION_JOB_TIMEOUT_SECONDS=3600
EVOAGENT_EVOLUTION_JOB_MAX_RETRIES=3
EVOAGENT_EVOLUTION_LEASE_SECONDS=60
```

2. `false/manual/always` 必须保持当前行为：不自动运行、不自动部署、所有生产激活需审批。
3. 对以下 API 返回结构建立快照测试：
   - `/v1/evolution/*`
   - `/v1/skill-evolution/*`
   - `/v1/deployments/*`
   - `/v1/evaluation/cases`
4. 对现有 Store 公共方法建立 SQLite/PostgreSQL 双后端契约。
5. 在 `scripts/migrate_db.py --check` 中加入新表和字段检查，但第一提交仅允许报告缺失，不自动执行破坏性迁移。

### 测试

- 新开关关闭时，现有 250 项测试行为不变。
- 未知配置值启动失败。
- 旧数据库升级后仍可读取旧 Prompt、Skill、Deployment 和 Evolution Run。
- 回滚开关后，新表存在但不参与运行。

### 验收

- 全量测试通过。
- SQLite/PostgreSQL 契约一致。
- 旧 API 返回结构无破坏性变化。

## 工作包 1：持久化 Evolution Controller

### 目标

把“调用一次 auto API”升级为可调度、可恢复、可暂停的后台状态机。

### 新增模块

- `evoagent/evolution_controller.py`
- `evoagent/evolution_state.py`
- `tests/test_evolution_controller.py`
- `tests/test_evolution_controller_recovery.py`

### 实施步骤

1. 实现 `EvolutionController.enqueue()`：
   - 接受 `manual | scheduled | event` 触发；
   - 根据租户、能力名、Experience 指纹集合生成幂等键；
   - 相同未完成 Job 不重复创建。
2. 实现数据库租约：
   - Worker 获取 `lease_owner/lease_until`；
   - 定期续租；
   - 进程崩溃后其他 Worker 可接管过期租约。
3. 每个步骤结束后写 `checkpoint_json`，禁止只依赖进程内状态。
4. Controller 调用现有 `EvolutionEngine` 和 `SkillEvolutionEngine`，不复制评测逻辑。
5. `continuous_eval_seconds > 0` 时启动定时扫描器：
   - 只扫描存在新 corroborated Experience 的租户/能力；
   - 没有新信号时不创建 Job；
   - 使用水位线避免重复消费。
6. 事件触发条件：
   - corroborated Experience 达到阈值；
   - 线上质量告警达到阈值；
   - 操作员手动请求。
7. 增加 API：

```text
POST /v1/evolution/jobs
GET  /v1/evolution/jobs
GET  /v1/evolution/jobs/{id}
POST /v1/evolution/jobs/{id}/pause
POST /v1/evolution/jobs/{id}/resume
POST /v1/evolution/jobs/{id}/cancel
POST /v1/evolution/jobs/{id}/retry
```

8. 所有 Job 操作写入 `audit_log`，并要求 `evolve` 或 `admin` 权限。

### 故障处理

- 数据库短暂失败：按指数退避重试。
- 评测 Provider 失败：Job 保持 `failed` 或 `paused`，候选不激活。
- 进程重启：从最后 checkpoint 恢复。
- 超过总预算：状态设为 `paused`，等待人工扩充预算。

### 验收

- 重复事件只产生一个 Job。
- 任一步骤杀进程后可从 checkpoint 恢复。
- 两个 Worker 不会同时处理同一个 Job。
- Controller 关闭时完全回到当前手动路径。

## 工作包 2：结构化 Reflection 与 Hypothesis

### 目标

闭合 `Experience → Reflection → Evolution`，避免把反馈类别直接转换成固定 Prompt 句子或字面规则。

### 新增模块

- `evoagent/evolution_reflection.py`
- `evoagent/hypothesis.py`
- `tests/test_evolution_reflection.py`

### Reflection 输入

至少支持：

- `false_positive`
- `missed_issue`
- `bad_fix`
- `accepted`
- Agent 执行失败
- Tool 调用失败
- Critic 拒绝原因
- 修复门禁失败
- 线上质量漂移

### 实施步骤

1. 对 Experience 按以下维度聚类：
   - 租户；
   - 仓库或租户级作用域；
   - 规则/问题类型；
   - 规范化证据；
   - 失败阶段；
   - 语言和路径类型。
2. 生成结构化 Hypothesis，禁止输出任意 Python/Shell。
3. `change_type` 初期只允许：
   - `prompt_patch`
   - `rule_add`
   - `rule_tighten`
   - `rule_exception`
   - `rule_remove`
   - `no_change`
   - `procedure_proposal`
   - `tool_proposal`
4. 只有 Prompt/Rule 类允许自动物化；Procedure/Tool 只生成待人工评审提案。
5. 为每个 Hypothesis 计算风险：
   - 是否扩大检测范围；
   - 是否降低严重等级；
   - 是否影响高风险规则；
   - 是否请求新权限；
   - 是否跨仓库生效。
6. 对高风险变更强制人工批准 Hypothesis，低风险变更可进入自动离线评测。
7. 将 `source_experience_ids`、`source_case_ids`、`source_task_ids` 精确记录到 Provenance。
8. 反馈文本只作为不可信证据；生成器不得遵循其中的指令语句。

### Hypothesis 质量门禁

- 至少 2 个独立任务证据，或 1 个经过授权的明确人工确认；
- 所有 Finding 必须定位到真实新增行；
- 证据完成脱敏；
- 作用域明确；
- 可定义客观评测预期；
- 不请求未声明权限。

### 验收

- 每个候选都能反查唯一 Hypothesis 和完整证据集合。
- 单条匿名漏报默认不能生成 Active 候选。
- Prompt Injection 式反馈不会进入候选内容。
- 无法解释的 Experience 被保存，但决策为 `no_change` 或待人工处理。

## 工作包 3：真实数据、泛化和灾难性遗忘门禁

### 目标

闭合“离线提升是否可信”“是否过拟合”“是否破坏旧能力”。

### 数据集分层

建立以下不可混用的 Suite：

1. `golden-regression`：不可变核心能力回归集；
2. `real-validation`：用于候选选择；
3. `real-holdout`：仓库和时间隔离，不向生成器暴露明细；
4. `cross-repo-transfer`：未见仓库；
5. `cross-language-transfer`：未见或低频语言；
6. `temporal-holdout`：候选生成时间之后的样本；
7. `adversarial-safety`：恶意反馈、提示注入、危险 Skill 和权限绕过；
8. `repair-regression`：修复正确性和行为保持。

### 数据要求

生产激活最低要求建议为：

- 真实标注样本总数 ≥ 300；
- Validation ≥ 150；
- Holdout ≥ 100；
- 独立仓库 ≥ 5；
- 至少覆盖 2 种主要语言；
- 高风险样本 ≥ 50；
- 干净 PR ≥ 100；
- 每条数据带来源 URL 或企业内部不可变来源 ID；
- 标注包含标注人、时间、规范版本和冲突解决记录。

试点期可以降低数量，但必须保留人工审批，不能自动生产激活。

### 实施步骤

1. 扩展 `evaluation_cases`：
   - `suite_id`
   - `dataset_version`
   - `repository`
   - `language`
   - `source_uri`
   - `labeler_ids_json`
   - `label_schema_version`
   - `created_before_candidate`
2. 扩展 `import_github_pr_dataset.py`：
   - 校验来源；
   - 检查重复 Diff；
   - 检查标签定位；
   - 生成数据版本指纹；
   - 按仓库分组切分，禁止同仓库泄漏到 Validation/Holdout。
3. 在 `RegressionEvaluator` 上增加分层指标：
   - 按语言；
   - 按仓库；
   - 按规则；
   - 按严重等级；
   - 按变更规模；
   - 按新旧能力集合。
4. 新增遗忘门禁：
   - Golden critical recall 必须 100%；
   - 任何核心规则 Recall 不得下降；
   - 各主要语言 F1 下降不得超过 2 pp；
   - 各历史 Active 版本优势用例必须保留；
   - 修复正确率不得下降。
5. 新增泛化门禁：
   - Cross-repo F1 不低于稳定版本；
   - Temporal Holdout 不低于稳定版本；
   - 不允许总体提升掩盖某一关键域大幅退化。
6. Holdout 轮换时只归档旧批次，不删除；候选生成器不能读取 Holdout 明细。

### 建议离线门禁

| 门禁 | 建议阈值 |
|---|---:|
| Validation F1 提升 | ≥ 2 pp |
| Precision 退化 | ≤ 0.5 pp |
| High-risk Recall | 不退化 |
| Clean Accuracy | 不退化 |
| Holdout F1 | 不退化 |
| Golden critical recall | 100% |
| Cross-repo F1 | 不退化 |
| p95 延迟增长 | ≤ 20% |
| 单 PR 成本增长 | ≤ 15% |
| 执行成功率 | ≥ 99% |
| 修复正确率 | 不退化 |

所有阈值必须按租户策略可配置，但生产环境不能允许空数据集通过。

### 验收

- 合成数据只能证明算法正确，不能通过生产来源门禁。
- 任一关键分层退化都会阻止候选进入 Shadow。
- 删除旧能力的候选可被 Golden/History 门禁稳定拒绝。
- 数据泄漏测试能发现相同仓库、相同 Diff 和派生样本跨分区。

## 工作包 4：统一 Prompt/Skill 生命周期

### 目标

Prompt 和声明式 Skill 使用同一状态机，离线评测通过后只进入 `validated`，不得直接 `active`。

### 新增模块

- `evoagent/candidate_lifecycle.py`
- `evoagent/evolution_policy.py`
- `tests/test_unified_candidate_lifecycle.py`

### 实施步骤

1. 将 `skill_lifecycle.py` 扩展为统一生命周期源。
2. 修改 `EvolutionEngine._propose()`：
   - 评测通过后保存为 `validated`；
   - 不再直接激活；
   - 返回 `decision=validated`。
3. 修改 `SkillEvolutionEngine._propose()` 执行相同行为。
4. 只有 Deployment Controller 可以执行：
   - `validated → shadow`
   - `shadow → canary`
   - `canary → active`
5. 只有 Evaluator 可以执行 `evaluating → validated/rejected`。
6. 只有管理员或自动回滚策略可以执行 `active/canary/shadow → rolled_back`。
7. Agent、Reflection 和 Candidate Builder 没有激活权限。
8. 现有 `/versions/{version}/activate`：
   - 开发兼容模式下保持旧行为；
   - 生产 Profile 下改为“创建部署审批”，不能直接激活未经 Shadow/Canary 的新版本；
   - 历史已验证版本的紧急回滚允许直接恢复。

### 审批策略

```text
always    所有候选进入 Shadow 前均需审批
high-risk 高风险、跨仓库、权限变化需审批
never     仅允许低风险 Prompt/Rule 且所有生产门禁已满足
```

生产默认建议 `always`，完成长期试点后才能改为 `high-risk`。

### 验收

- 任何新候选不能从 `draft/validated` 跳到 `active`。
- 非法转换在 Store 事务内失败并写告警。
- Active 版本切换和旧版本降级在同一事务完成。
- 重启后根据数据库状态恢复正确 Reviewer。

## 工作包 5：统一 Shadow 闭环

### 目标

让 Prompt 和声明式 Skill 都能在不影响用户结果的情况下运行，并收集质量、成本和稳定性证据。

### 实施步骤

1. 将 `_candidate_reviewer()` 扩展为通用 `candidate_reviewers(tenant_id, deployment)`。
2. `_run_shadow()` 支持：
   - Prompt Candidate；
   - Rule Skill Candidate；
   - 未来 Procedure Skill Candidate。
3. 对同一任务保存：
   - stable/candidate Finding Keys；
   - 新增、缺失和共同 Finding；
   - 两侧耗时；
   - Token/成本；
   - 执行错误；
   - 候选来源版本。
4. Shadow 结果绝不写入最终报告，也不能触发自动修复。
5. 对有人工反馈的任务回填 Observation：
   - `accepted`
   - `false_positive`
   - `missed_issue`
   - `bad_fix`
6. Shadow 不得仅以“与 Stable 一致”为质量标准，因为 Stable 也可能错误。
7. 引入抽样人工复核：
   - 候选新增 Finding；
   - 候选删除的高风险 Finding；
   - Stable/Candidate 冲突；
   - 干净 PR 上的新增 Finding。

### Shadow 进入 Canary 的建议门禁

- Shadow 样本 ≥ 100；
- 至少 30 个样本有人工标签或可靠代理标签；
- Candidate 执行成功率 ≥ 99%；
- 高风险漏检为 0；
- 人工接受率不低于 Stable；
- FP Rate 不高于 Stable + 1 pp；
- p95 延迟增长 ≤ 20%；
- 成本增长 ≤ 15%；
- 无权限、安全或数据泄漏事件。

标签不足时只能等待或人工批准，不能自动晋级。

### 验收

- 声明式 Skill 可真正运行在 Shadow，而非离线评测后直接注入主链路。
- Candidate 崩溃不影响主审查。
- 每个 Observation 能关联 Job、Hypothesis、Candidate 和任务。
- Shadow 门禁结果持久化到 `evolution_gate_results`。

## 工作包 6：分阶段 Canary 与自动质量回滚

### 目标

把现有单一 canary_percent 扩展为可暂停、可晋级、基于技术和质量预算的发布状态机。

### Canary 阶段

```text
5%  → 最少 50 个任务
20% → 累计最少 200 个任务
50% → 累计最少 500 个任务
100% → active
```

每个阶段必须独立通过门禁，不能按时间自动晋级。

### 技术回滚条件

任一满足即立即回滚：

- Candidate 连续 3 次执行失败；
- 最近 50 次错误率 > 2%；
- 出现未授权工具/权限请求；
- p95 延迟超过 Stable 50%；
- 单任务成本超过硬预算；
- 数据或租户隔离异常；
- 无法读取 Candidate 版本或 Artifact 指纹不匹配。

### 质量回滚条件

- 已确认高风险漏检 ≥ 1；
- FP Rate 比 Stable 高 > 2 pp；
- 人工接受率比 Stable 低 > 5 pp；
- Clean Accuracy 下降 > 2 pp；
- 修复失败率显著上升；
- 任一 Golden 核心能力线上探针失败。

### 回滚操作必须原子化

1. Deployment 状态设为 `rolled_back`；
2. Canary/Shadow 比例归零；
3. Stable 版本重新激活；
4. Candidate 从运行时 Registry 移除；
5. 写入 `rollback_reason` 和完整指标快照；
6. 创建 critical alert；
7. Evolution Job 设为 `rolled_back`；
8. 相关 Experience 不自动消费，保留用于重新反思；
9. 进入相同指纹冷却期，防止立即重发。

### 验收

- 在并发请求中回滚不产生双 Active 版本。
- 回滚后新任务只使用 Stable。
- 进程重启不重新加载已回滚 Candidate。
- 错误、误报、高风险漏检、延迟和成本均有独立故障注入测试。

## 工作包 7：持续学习、迁移和遗忘监控

### 目标

证明系统不是只对单一反馈和单一仓库过拟合，而是可持续积累、迁移和保留能力。

### Experience Scope

增加：

```text
repository-local
tenant-shared
global-builtin
```

规则：

- 默认 `repository-local`；
- 同一租户多个仓库独立确认后，才可提升为 `tenant-shared`；
- 不允许自动跨租户共享原始 Experience；
- `global-builtin` 只能由维护者发布。

### 学习与迁移指标

每个活跃版本持续记录：

- Experience 使用率；
- 候选生成成功率；
- 独立新增 TP；
- 新增 FP；
- 与其他 Skill 重复率；
- Repository-local 增益；
- Cross-repo 增益；
- Cross-language 增益；
- 30/60/90 天保留率；
- 历史能力回归数；
- 单位质量收益的延迟和费用。

### 实施步骤

1. 每日或每周运行持续 Evaluation，而不是每个线上请求同步运行。
2. 当前 Active 与最近 N 个历史 Active/Validated 版本比较。
3. 对长期无独立贡献的 Skill 标记 `stale`。
4. Curator 仍只提出合并、收紧、归档建议，不直接修改 Active。
5. 所有 Curator 候选重新走完整离线、Shadow 和 Canary。
6. 建立固定的遗忘趋势告警：
   - 核心规则 Recall 下降；
   - 某语言/仓库质量连续两个窗口下降；
   - 误报持续升高；
   - Skill 冲突或重复率持续升高。

### 验收

- 能输出版本×仓库×语言迁移矩阵。
- 能证明某个 Experience 在未见仓库带来正向边际贡献。
- 能识别并阻止“总体 F1 提升但核心高风险能力退化”的候选。
- Stale/Archive 不删除历史审计和回滚目标。

## 工作包 8：安全、权限与危险进化拦截

### 目标

把当前“具备安全能力”升级为“生产环境默认强制安全”。

### 生产 Profile 建议强制值

```text
EVOAGENT_AUTH_REQUIRED=true
EVOAGENT_EXPERIENCE_MODE=enforce
EVOAGENT_FEEDBACK_MIN_CONFIRMERS=2
EVOAGENT_FEEDBACK_TRUST_ENABLED=true
EVOAGENT_SKILL_MARGINAL_GATE=enforce
EVOAGENT_EVOLUTION_COMPARE_HISTORY=3
EVOAGENT_EVOLUTION_COOLDOWN_MINUTES=1440
EVOAGENT_HOLDOUT_ROTATION=5
EVOAGENT_SKILL_LIFECYCLE_ENABLED=true
EVOAGENT_EVOLUTION_APPROVAL_POLICY=always
```

生产 Profile 缺少强密钥、真实 Holdout、审批人或审计存储时启动失败。

### 权限矩阵

| 角色 | 权限 |
|---|---|
| Agent/Generator | 创建 Draft Hypothesis/Candidate |
| Evaluator | 写评测结果，转为 Validated/Rejected |
| Deployment Controller | 执行 Shadow/Canary |
| Policy Engine | 根据已配置规则晋级或回滚 |
| Operator | 审批、暂停、拒绝、紧急回滚 |
| Admin | 修改策略和数据集，但不能篡改历史审计 |

### 危险候选拦截

- Agent-created Skill 只允许声明式 Rule DSL；
- 禁止 `eval`、正则、任意文件、网络、Shell 和动态 import；
- `permissions` 必须为空；
- 任何新增权限的 Procedure/Tool Proposal 必须人工代码审查；
- 动态 Python Skill 在生产环境必须配置无网络、只读文件系统的容器沙箱；
- 仅使用 `python -I` 子进程不能视为生产级沙箱；
- Artifact 哈希、签名、父版本和来源必须一致；
- 反馈、Diff、Memory 和工具输出均按不可信输入处理。

### 验收

- Prompt Injection、路径逃逸、跨租户、签名伪造、Artifact 替换均被拒绝。
- Agent 无法调用激活 API。
- 审批人不能审批自己生成的高风险候选，可配置双人审批。
- 审计记录包含谁、何时、基于什么证据批准了什么版本。

## 工作包 9：端到端可观测性、成本与恢复

### 目标

让进化过程可追踪、可控制、可恢复，并能回答“提升是否值得成本”。

### Trace 统一属性

所有相关 Span、日志和审计至少包含：

- `tenant_id`
- `repository`
- `task_id`
- `evolution_job_id`
- `hypothesis_id`
- `candidate_kind`
- `candidate_name`
- `candidate_version`
- `deployment_stage`
- `dataset_version`
- `model`

### 指标

新增：

```text
evoagent_evolution_jobs_total{status,trigger,kind}
evoagent_evolution_job_seconds{step,kind}
evoagent_evolution_gate_total{stage,gate,passed}
evoagent_evolution_candidate_tokens_total{model,direction}
evoagent_evolution_candidate_cost_total{model,stage}
evoagent_deployment_tasks_total{stage,version,status}
evoagent_deployment_rollbacks_total{reason,kind}
evoagent_shadow_quality_delta{metric}
evoagent_canary_quality_delta{metric}
evoagent_experience_total{type,status,scope}
evoagent_forgetting_regressions_total{domain}
```

### 预算

Job 至少设置：

- 最大模型调用次数；
- 最大输入/输出 Token；
- 最大费用；
- 最大评测样本数；
- 最大墙钟时间；
- 最大重试次数。

超预算后暂停，不得通过缩小 Holdout 或跳过门禁继续。

### 备份恢复

1. 为 PostgreSQL 制定每日备份和保留策略。
2. 每月至少执行一次恢复演练。
3. 恢复验证必须检查：
   - Active/Stable 版本；
   - 未完成 Job；
   - Deployment 状态；
   - Dataset 指纹；
   - Audit 完整性。
4. 新增：
   - `scripts/run_closed_loop_drill.py`
   - `scripts/replay_evolution_job.py`
   - `scripts/verify_backup_restore.py`

### 验收

- 从任一生产 Finding 可以追踪到版本、Hypothesis、Experience 和原始证据。
- 从任一激活决策可以重放离线门禁。
- 重启、数据库短暂故障和 Worker 崩溃均不会形成未知状态。
- 能按租户统计质量收益、Token、成本和延迟。

## 工作包 10：真实业务试点闭环

### 目标

用真实企业任务证明性能、成本、安全和可靠性，而不是仅验证代码路径。

### 试点阶段

#### 阶段 A：2 周历史回放

- 选取 3–5 个仓库；
- 回放至少 300 个已完成 PR；
- 独立人工标注；
- 不连接线上 Webhook；
- 建立 Stable 基线。

退出条件：真实数据来源门禁通过，核心高风险 Recall 达标。

#### 阶段 B：2 周 Shadow

- 连接真实 PR，但不回写评论；
- 收集候选与人工 Reviewer 对比；
- 每日抽样冲突和新增 Finding；
- 不自动激活。

退出条件：至少 100 个真实任务，质量、延迟、成本、安全门禁通过。

#### 阶段 C：2–4 周 Advisory Canary

- 5% → 20% → 50%；
- 评论标记为建议，不阻断合并；
- 监控人工接受、误报和撤回；
- 允许一键回滚。

退出条件：连续两个统计窗口无关键回归，SLO 达标。

#### 阶段 D：受控生产

- 只允许低风险 Prompt/Rule 自动进入 Shadow；
- Canary 晋级仍由审批或严格 Policy 控制；
- 高风险、跨仓库、权限变化始终人工审批；
- 每月进行遗忘评测和恢复演练。

### 业务指标

至少跟踪：

- Finding 人工接受率；
- 每 PR 误报数；
- 高风险漏检率；
- 干净 PR 准确率；
- Reviewer 节省时间；
- p50/p95/p99 延迟；
- 单 PR Token 和费用；
- 服务可用性；
- 自动回滚次数与恢复时间；
- 进化候选通过率；
- 进化后 7/30/90 天质量保持率。

### 建议 SLO

- 审查成功率 ≥ 99.5%；
- p95 延迟满足团队约定，且相对 Stable 增长 ≤ 20%；
- Critical 漏检不高于 Stable；
- 人工接受率不低于 Stable；
- 自动回滚检测到恢复完成 ≤ 5 分钟；
- 审计事件完整率 100%；
- 未授权发布事件 0。

## 7. 完整闭环步骤与责任边界

| 闭环阶段 | 输入 | 输出 | 执行者 | 失败行为 |
|---|---|---|---|---|
| Failure | 任务/反馈/线上指标 | 原始信号 | Runtime/API/Monitor | 保存并告警 |
| Experience | 原始信号 | 去重、脱敏经验 | Experience Router | 降级为 observed |
| Reflection | corroborated Experience | Hypothesis | Reflection Engine | no_change/待审批 |
| Evolution | Approved Hypothesis | Candidate | Candidate Builder | 保存 Draft |
| Evaluation | Candidate + Suites | Gate Results | Evaluator | Rejected/Deferred |
| Approval | Gate Results | 发布许可 | Operator/Policy | Awaiting approval |
| Shadow | Validated Candidate | Online Observation | Deployment Controller | Pause/Rollback |
| Canary | Shadow-passed Candidate | Quality Evidence | Deployment Controller | Automatic rollback |
| Active | Canary-passed Candidate | Stable Version | Policy Engine | Continuous monitor |
| Rollback | Budget/Gate breach | Previous Stable | Rollback Controller | Critical alert |

## 8. 测试矩阵

### 8.1 单元测试

- 状态转换合法性；
- 幂等键；
- 租约获取与过期；
- Hypothesis Schema；
- 反馈可信和多任务确认；
- 数据泄漏检测；
- 分层指标；
- 遗忘门禁；
- Shadow/Canary 分配；
- 技术和质量回滚策略；
- Token/费用预算。

### 8.2 Store 契约测试

- SQLite/PostgreSQL 新表方法一致；
- 状态转换事务一致；
- 双 Active 冲突；
- Job 并发领取；
- Observation 回填；
- 回滚事件和审计；
- 旧数据库升级。

### 8.3 集成测试

- Feedback → Experience → Hypothesis → Candidate；
- Candidate → Validation/Holdout → Validated；
- Validated → Shadow → Canary → Active；
- Canary 质量下降 → Rollback；
- 进程重启 → Job 恢复；
- Provider 故障 → fail-closed；
- 数据集不足 → Deferred；
- 审批拒绝 → Candidate 不进入发布。

### 8.4 安全测试

- 恶意反馈 Prompt Injection；
- 跨租户 Experience/Job/Version ID 猜测；
- Artifact Hash/签名替换；
- 动态 Skill 文件系统和网络访问；
- 激活权限绕过；
- Holdout 明细泄漏；
- 审计篡改；
- 费用预算绕过。

### 8.5 故障注入

- Job 每个步骤前后杀进程；
- 数据库断开；
- Redis 重启；
- OTEL 不可用；
- LLM 超时/限流/无效 JSON；
- Candidate Reviewer 崩溃；
- 并发回滚和晋级；
- 备份后恢复到新环境。

### 8.6 E2E 验收脚本

`run_closed_loop_drill.py` 应自动完成：

1. 创建两条独立漏报反馈；
2. 生成 corroborated Experience；
3. 创建并批准 Hypothesis；
4. 生成 Candidate；
5. 运行 Validation/Holdout/History/Forgetting；
6. 进入 Shadow；
7. 注入足够 Shadow Observation；
8. 进入 Canary；
9. 注入质量退化；
10. 验证自动回滚；
11. 重启服务；
12. 验证 Stable、Job、Audit 和版本链一致。

## 9. 推荐实施顺序与里程碑

### 里程碑 M1：可恢复控制面

范围：工作包 0–1。

完成标准：Job 可持久化、幂等、暂停、恢复；尚不自动生成或部署候选。

### 里程碑 M2：可解释候选

范围：工作包 2。

完成标准：每个 Candidate 都有结构化 Hypothesis 和证据来源。

### 里程碑 M3：可信离线评测

范围：工作包 3–4。

完成标准：真实来源、泛化、遗忘、安全、成本门禁齐备；候选只能到 Validated。

### 里程碑 M4：安全发布闭环

范围：工作包 5–6。

完成标准：Prompt/Skill 都必须经过 Shadow/Canary；质量退化可自动回滚。

### 里程碑 M5：持续学习治理

范围：工作包 7–9。

完成标准：持续评测、迁移矩阵、成本台账、审计和恢复演练完整。

### 里程碑 M6：真实业务验证

范围：工作包 10。

完成标准：4–8 周真实业务数据证明质量、成本、安全和可靠性达标。

## 10. 建议排期

在不假设具体团队规模的情况下，推荐按以下顺序排期：

| 阶段 | 建议时间 | 交付 |
|---|---:|---|
| M1 | 2 周 | Job、状态机、租约、恢复、API |
| M2 | 1–2 周 | Reflection/Hypothesis/Provenance |
| M3 | 3 周 | 真实评测、遗忘/泛化门禁、统一生命周期 |
| M4 | 3 周 | 通用 Shadow、Canary、质量回滚 |
| M5 | 2–3 周 | 持续监控、成本、恢复、治理 |
| M6 | 4–8 周 | 真实业务 Shadow/Canary 试点 |

实现阶段约 11–13 周，之后至少需要 4–8 周真实试点。若真实标注数据尚未准备，M3 和 M6 会成为关键路径。

## 11. 每个缺口的闭环判定

| 原缺口 | 闭环标准 |
|---|---|
| 需要手动调用 auto API | Event/Scheduled Controller 可幂等创建并恢复 Job |
| Reflection 不进入进化 | 每个候选必须关联结构化 Hypothesis 和证据 |
| 回放后直接激活 | 评测通过只到 Validated，强制 Shadow/Canary |
| Skill 未纳入灰度 | Prompt/Rule Skill 共用 Deployment Controller |
| 只按错误率回滚 | 技术、质量、成本、安全预算均可回滚 |
| 默认保护关闭 | Production Profile 启动时强制安全值 |
| Holdout 太小/合成 | 真实、仓库隔离、时间隔离、版本化数据 Suite |
| 缺少泛化评测 | Cross-repo/Cross-language/Temporal 门禁 |
| 缺少遗忘防护 | Golden + History + 分域非退化门禁 |
| 进化不可端到端追踪 | Job/Hypothesis/Candidate/Deployment/Observation 统一 ID |
| 缺少成本闭环 | 主审查 Token、费用、延迟进入 Usage Ledger 和门禁 |
| 缺少业务证明 | 4–8 周真实 Shadow/Canary 与明确 SLO |

## 12. 最终 Definition of Done

只有同时满足以下条件，才能将 EvoAgent 表述为“自动且受控的持续进化系统”：

1. 真实失败信号能自动、幂等地进入持久化 Evolution Job。
2. 每个候选都有可解释、可审计的 Hypothesis 和来源证据。
3. Agent 不能绕过安全、权限、数据来源和非退化门禁。
4. 候选不会从离线评测直接进入 Active。
5. Prompt 和 Skill 都必须经过 Shadow 与分阶段 Canary。
6. 技术、质量、安全、延迟或成本退化可自动回滚。
7. 系统能证明未见仓库/语言上的迁移能力，并持续检测灾难性遗忘。
8. 所有运行、指标、审批、发布、回滚和费用均可端到端追踪。
9. 进程、队列或数据库故障后可以恢复到唯一确定状态。
10. 在真实业务中连续运行至少 4–8 周，达到事先定义的质量、成本、安全和可靠性 SLO。

在完成 M1–M4 前，系统应保持“受控研发/影子试点”定位；完成 M5–M6 并满足上述 Definition of Done 后，才适合逐步开放低风险能力的自动进化。
