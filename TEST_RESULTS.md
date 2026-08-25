# EvoAgent 测试结果报告

- 执行日期：2026-08-13
- 环境：Windows，Python 3.11，SQLite + PostgreSQL 16.14（Docker 容器 `evoagent-postgres`，宿主端口 15432）
- 执行命令：

```powershell
$env:EVOAGENT_DATABASE_URL="postgresql://evoagent:evoagent-local@127.0.0.1:15432/evoagent"
python -W error::ResourceWarning -m unittest discover -s tests
```

## 一、测试概况

| 项目 | 结果 |
|---|---|
| 总用例数 | 190 |
| 通过 | 190 |
| 跳过 | 0 |
| 失败 / 错误 | 0 |
| 耗时 | 44.3 秒 |
| ResourceWarning（视为错误） | 0 |

**结论状态：全绿（OK，无跳过）**

## 二、测试集数量及分布

共 24 个测试文件、190 个用例：

| 测试文件 | 用例数 | 对应工作包/范围 |
|---|---|---|
| test_semantic_reviewer.py | 18 | 工作包五：AST 语义审查、污点传播、外部分析器回退 |
| test_skill_metrics.py | 18 | 工作包四：边际指标、使用指标、只读 Curator |
| test_finding_quality.py | 17 | 工作包六：Finding 聚类、置信度合成与结果分级 |
| test_feedback_trust.py | 15 | 工作包九：反馈可信度、多确认人、历史对比、冷却期、Holdout 轮换 |
| test_eval_source.py | 13 | 工作包八：真实数据集源切换、持续评测指标 |
| test_observability.py | 13 | 工作包十：健康检查分离、Agent/质量指标、迁移工具 |
| test_experience.py | 13 | 工作包三：Experience 旁路、路由、聚合与切换 |
| test_advanced.py | 12 | 提示词进化、修复器、多智能体 |
| test_ast_fixer.py | 11 | 工作包七：保留格式 AST 修复、修复后复验 |
| test_skill_lifecycle.py | 9 | 工作包二：Skill 生命周期、旧库升级、幂等迁移 |
| test_runtime_memory_context.py | 9 | Agent Runtime、记忆与上下文压缩 |
| test_production_features.py | 8 | 生产特性：灰度、告警、死信等 |
| test_skill_evolution.py | 5 | Skill 自进化引擎 |
| test_config.py | 5 | 配置解析、进化开关默认值与校验 |
| test_evaluation_harness.py | 4 | 评测回放框架 |
| test_multi_agent_collaboration.py | 4 | 多智能体协作 |
| test_service.py | 4 | 服务层 |
| test_store_lifecycle.py | 4 | 工作包一：连接生命周期、无泄漏 |
| test_store_contract.py | 2 | SQLite/PostgreSQL 契约（1 项运行时跳过） |
| test_harness.py | 2 | 审查 Harness |
| test_diff_parser.py | 1 | Diff 解析 |
| test_evolution_proof.py | 1 | 离线进化证明 |
| test_github.py | 1 | GitHub 签名与客户端 |
| test_reviewer.py | 1 | 规则审查器 |
| **合计** | **190** | |

## 三、测试结果明细

全部 24 个文件通过。`PostgresContractTests.test_contract` 在本机 Docker PostgreSQL 16.14 上执行并通过（此前因无 PostgreSQL 环境而跳过）；契约覆盖 WP1–WP10 的公共 API，含 `list_evaluation_cases` source 过滤、`archive_oldest_holdout_cases`、`ping()`、`save/list_skill_evolution_run` 新指标键往返，以及 WP4 部署与 shadow 观察一致性；测试在共享库上可重复执行（setUp 清空 public schema）。

首次在真实 PostgreSQL 上运行暴露出并修复了双后端不一致缺陷：
- PostgreSQL 缺 `release_observations` 表与 `deployments` 表的 `max_disagreement_rate`/`auto_promote`/`shadow_samples`/`disagreements` 四列，已按 SQLite 语义幂等补齐；
- PostgreSQL 缺 `record_shadow_observation`/`list_release_observations` 方法，已补齐并与 SQLite 行为一致；
- `migrate_db.py --db postgres://... --check` 验证全部必需表与列就绪。

各工作包测试覆盖要点：

- **工作包一（资源生命周期）**：SQLite 连接确定性关闭、临时文件可立即删除、无 ResourceWarning、回滚后 Store 可用、`close()` 幂等。
- **工作包二（Skill 生命周期）**：旧 Schema 数据库原地升级后 Active 版本保持、重复启动迁移幂等、`active` 与 `status` 一致、历史版本回滚、Rejected/Archived 不可激活、旧调用签名兼容、Provenance 往返。
- **工作包三（Experience 旁路）**：Shadow 双写失败不影响反馈主路径、同任务去重、跨任务 corroborate、租户隔离、不完整漏报不产生候选、Enforce 只读 corroborated、激活后新旧记录状态一致。
- **工作包四（边际指标与 Curator）**：Shadow 边际不改变激活决策、Enforce 显式生效、无法归属不计数、指标写失败不影响审查、Curator 识别重复规则、Curator 无修改能力、指标与建议按租户隔离。
- **工作包五（语义审查层）**：AST 污点传播（eval/subprocess/shell 注入）、未关闭资源检测、快照解析失败逐行 dedent 回退、Bandit/Ruff 未安装时回退到标准库 AST、`EVOAGENT_STATIC_ANALYZER=off` 不注入、语义层可捕获规则层漏报。
- **工作包六（聚类与置信度）**：同位置 Finding 确定性聚类、严重度优先主项、共识置信度合成（0.7+0.3×共识 与 1-FP 率相乘）、分级分桶、`finding_clustering=off` 默认不合并、开关校验。
- **工作包七（结构化修复）**：仅编辑目标行保留注释、print/secret/shell 修复语义正确、修复后 pattern 复验与 compile 校验、超文件数/复验失败返回拒绝 note、`ast_fixer_enabled=False` 走旧修复路径。
- **工作包八（真实数据集与持续评测指标）**：`EVOAGENT_EVAL_SOURCE=builtin|github-real|all` 数据源切换、真实标注样本以 `source="github-real"` 导入并隔离评测、默认 builtin 路径不受真实样本污染、样本不足保持 `deferred`、指标新键（`false_positive_rate`/`latency_ms`/`per_finding_cost_estimate`/`fix_correctness`）往返一致且旧记录缺省读取不报错、评测报告 `dataset_source`/`dataset_sha256` 落库、EndToEndEvaluationHarness 支持按 source.kind 可插拔过滤、导入脚本 `--db` 直写评测库。
- **工作包九（反馈可信与过拟合防护）**：低可信反馈在 `trust_enabled=on` 下不直接生成候选、`feedback_min_confirmers` 多独立任务确认（含服务层 corroborate 计数）、历史版本对比 shadow 只记录/enforce 拦截回归、同指纹候选冷却期内不重复评测、Holdout 每 N 次激活轮换且旧样本保留审计、provenance 追加 `model`/`tool_version`/`dataset_source`/`prompt_fingerprint`、全部默认值下与原行为一致。
- **工作包十（生产可观测性与运维）**：`/health`/`/health/live`/`/health/ready` 分离且 `/health` 快照不变、`/health/ready` 依赖降级返回 503、Agent 级指标（调用/耗时/失败率）、按租户×仓库×规则×模型 finding 分布、每规则误报率、队列积压与死信告警、关闭顺序 HTTP→队列→Trace exporter 且 `close()` 幂等、`migrate_db.py --check` 只读幂等且无 destructive 变更、新指标键在 /metrics 输出存在且不影响旧键。

## 四、简要结论

1. 190 项自动化测试全绿，`-W error::ResourceWarning` 严格模式下无资源泄漏，达到绿色基线验收标准。
2. 第一阶段四个工作包（资源生命周期、Skill 生命周期、Experience 旁路与渐进切换、边际指标与只读 Curator）、第二阶段三个工作包（语义审查层、聚类与置信度、结构化修复）与第三阶段三个工作包（真实数据集与持续评测指标、反馈可信与过拟合防护、生产可观测性与运维）均已实现并通过对应测试，兼容性原则（追加式改造、暗开关回退、双后端契约）得到验证。
3. 真实运行冒烟验证补充确认：语义层捕获规则层漏掉的 `SEM-TAINTED-SUBPROCESS`；规则层+语义层同位置 finding 被聚类合并、置信度合成生效；AST 修复保留注释；真实 PR 样本在 `source="github-real"` 下可隔离评测，默认 `builtin` 行为不变；低可信反馈/多确认人/冷却期/历史对比/Holdout 轮换等防护在默认零变化下开启后生效；健康检查与迁移工具可运行。
4. 双后端契约已在真实 PostgreSQL（Docker postgres:16-alpine）上验证：SQLite 与 PostgreSQL 对 WP1–WP10 的公共 API 行为一致；契约测试改为每次运行前清空 public schema，可在共享数据库上重复执行。

## 五、完整验证运行记录（按序全量验证）

执行日期：2026-08-13。按顺序执行六步验证，全部通过。

| 步骤 | 命令/方式 | 结果 |
|---|---|---|
| 1. SQLite 全量测试 | `python -W error::ResourceWarning -m unittest discover -s tests` | ✅ 190 通过 / 0 失败 / 1 跳过（skip 为契约运行时检测，非缺陷） |
| 2. PostgreSQL 全量测试 | `$env:EVOAGENT_DATABASE_URL="postgresql://evoagent:evoagent-local@127.0.0.1:15432/evoagent"` + 同上 | ✅ 190 通过 / 0 失败 / 0 跳过 |
| 3. 端到端评测 | `python scripts/run_e2e_evaluation.py` | ✅ baseline F1=71.4% → candidate F1=82.5% |
| 4. 进化闭环证明 | `python scripts/run_prompt_evolution_proof.py` | ✅ decision=activated，run_id=3b2442d9-c1de-4601-8123-8ac4d110f4e1 |
| 5. HTTP 冒烟 | `python -m evoagent` 启动 + REST 调用 | ✅ health 三端点 + 同步/异步审查链路 |
| 6. 迁移检查 | `python scripts/migrate_db.py --check`（SQLite + PostgreSQL） | ✅ 双库均 `schema OK` |

### 第 3 步 e2e 关键指标

- candidate F1 = 82.5%（baseline 71.4%）
- high-risk recall = 94.7%、clean accuracy = 91.7%
- safe fix = 78.8%、e2e fix = 65.0%

### 第 4 步进化证明

- 决策：`activated`（Validation 提升且 Holdout 不退化）
- 运行 ID：`3b2442d9-c1de-4601-8123-8ac4d110f4e1`

### 第 5 步 HTTP 冒烟明细

服务以 `python -m evoagent` 启动（监听 127.0.0.1:8080），验证：

1. `/health` → `{"status":"ok", ...}`；`/health/live` 正常；`/health/ready` → `database:true, queue:true`（github_token/llm 为 false，符合未配置预期）。
2. **同步审查** POST `/v1/reviews`（body：`repository` 字符串、`diff`、`pull_request` 整数）：提交含 `eval(user_input)` 的危险 diff，正确检出 `SEC-EVAL` critical（confidence 0.93，line 4），整体风险 `high`；多智能体协议 `plan-challenge-revise-evidence-verify-arbitrate` 完整走通（7 角色、3 specialist 全部 completed、16 条对话消息、1 轮 dialogue）。
3. **异步审查** POST `/v1/reviews?async=true`：返回 `PENDING` → 轮询 `/v1/tasks/{id}` 转 `SUCCESS` → `/v1/tasks/{id}/report` 返回完整 Markdown 报告（`# EvoAgent PR Review — #43`）。

### 第 6 步迁移检查

- SQLite：`python scripts/migrate_db.py --db evoagent.db --check` → `schema OK: all required tables and columns present`
- PostgreSQL：`python scripts/migrate_db.py --db "postgresql://evoagent:evoagent-local@127.0.0.1:15432/evoagent" --check` → `schema OK`

### 验证过程遇到的问题（均已解决）

| 问题 | 原因 | 处理 |
|---|---|---|
| POST `/v1/tasks` 返回 404 | 审查提交路由是 `/v1/reviews`（`/v1/tasks` 仅查询） | 改用 POST `/v1/reviews` |
| `repository` 传对象被拒 | API 期望字符串 + `pull_request` 为整数 | 修正请求体字段类型 |
| PG 迁移检查密码认证失败 | 误用 `evoagent`，实际为 `evoagent-local` | 改用 compose 中的真实密码 |
| SQLite `--check` 报库不存在 | 数据库在项目根 `evoagent.db`，非 `data/` | 修正路径后通过 |
