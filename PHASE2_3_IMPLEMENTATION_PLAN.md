# EvoAgent 第二阶段与第三阶段实施方案（可行性审定版）

本文档在 [`OPTIMIZATION_ROADMAP.md`](./OPTIMIZATION_ROADMAP.md) 的 P1/P2 方向与 [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) 已完成的四个工作包之上制定，目标是：

- **第二阶段（审查效果）**：在不破坏现有规则审查与自进化链路的前提下，叠加语义分析、改善结果合并与置信度、升级安全修复。
- **第三阶段（可信评测与生产演进）**：建立真实数据评测、防范反馈污染与过拟合、完善可观测性与运维能力。

延续首轮三项不可违反的原则：**只追加、不删除、可回退**；每个工作包都以暗开关默认关闭、显式启用；SQLite/PostgreSQL 契约一致。

---

## 一、总体技术选型（最可行方案）

| 能力 | 首选方案 | 依赖 | 备选/可选 |
|---|---|---|---|
| 语义分析 | Python 内置 `ast` 模块的跨行/跨函数/污点检查 | 零依赖 | Bandit、Ruff（子进程，可选安装） |
| 结构化补丁 | `ast` 改写 + `ast.unparse`（3.9+） | 零依赖 | LibCST（保留格式，可选） |
| Finding 聚类 | 位置 + 证据归一化 + 规则相似度的确定性算法 | 零依赖 | — |
| 真实数据集 | 现有 `import_github_pr_dataset.py` 扩展 + 标注规范 | 零依赖 | — |
| 可观测性 | 现有 `metrics.py`/`observability.py` 扩展 | 零依赖 | OTLP 已有 |

外部工具一律采用"可选依赖"模式（同首轮 psycopg）：安装时启用，未安装时自动降级并记日志，绝不成为启动硬依赖。

---

## 二、第二阶段：审查效果

### 工作包五：语义审查层（AST + 可选外部静态分析）

目标：把现有"逐行正则第一层"之上的第二层补上，捕获跨行、跨函数、数据流类问题；保留规则层为快速确定性基线。

#### 第 1 步：纯标准库 AST 分析器

新增 `evoagent/ast_analysis.py`：

- 解析 Diff 新增行所属的函数/类（用 `ast` 对整文件快照解析，定位新增语句所在作用域）。
- 首期检查项（确定性、可测）：
  - 跨行：异常处理缺失（`try` 内新增调用但无 `except`）、资源未关闭（打开文件句柄未在 `finally` 关闭）。
  - 跨函数：外部输入（`request`/`input`/`os.getenv` 等来源）流入 `eval/exec/subprocess/sql/deserialization` 的污点路径（同文件内数据流，不做跨文件分析）。
  - 数据流：`shell=True`、格式字符串拼接进 `subprocess`。
- 输出统一的检查结果结构 `{rule_id, severity, path, line, evidence, explanation, fix, test}`，与现有 `Finding` 字段一一对应。

#### 第 2 步：新增语义审查器并接入

- 新增 `evoagent/semantic_reviewer.py`：`class SemanticReviewer(Reviewer)`，`review(diff, parsed)` 先解析新增行所在文件快照（`store.get_task_payload` 不可用则只基于 diff 构造最小快照），再调用第 1 步分析器。
- [models.py](file:///d:/研究生/1/project/EvoAgent/evoagent/models.py) 的 `Finding` **只追加**一个字段 `analyzer: Optional[str] = None`（`"rule"` / `"ast"` / `"bandit"`），`to_dict()` 自动携带。
- [service.py](file:///d:/研究生/1/project/EvoAgent/evoagent/service.py) 在注册 `security-review`/`reliability-review` 旁追加注册 `semantic-review`（`analyzer="ast"`），并加入 `_build_coordinator` 的 reviewers 列表。
- 可选外部工具：`EVOAGENT_STATIC_ANALYZER=off|ast|bandit|ruff|composite`。`bandit/ruff` 通过 `subprocess.run(encoding="utf-8", ...)` 在临时目录或输入流上运行，输出统一转换为 Finding；未安装时回退 `ast` 并记 `logger.warning`。

#### 第 3 步：配置与暗开关

[config.py](file:///d:/研究生/1/project/EvoAgent/evoagent/config.py) 新增：

```python
static_analyzer: str = "off"          # off|ast|bandit|ruff|composite
```

`validate_evolution()` 校验取值；`off` 时完全不注册语义审查器，运行路径与首轮一致。

#### 工作包五测试

- AST 能发现跨行缺失 `except`、同文件污点流入 `eval`。
- 规则层发现的跨行问题与语义层不重复计数（去重见工作包六）。
- `static_analyzer=off` 时 reviewers 列表与首轮完全一致。
- Bandit 未安装时 `composite` 回退 `ast` 且不抛错。
- Finding 的 `analyzer` 字段可追溯、SQLite/PostgreSQL 报告往返一致。

验收：默认配置零行为变化；开启 `ast` 后在不改变规则层输出的前提下追加语义发现。

回滚：`EVOAGENT_STATIC_ANALYZER=off` 并重启；删除注册代码即可，无数据迁移。

---

### 工作包六：Finding 聚类、置信度与结果分级

目标：解决同一问题被多个 Agent 以不同规则 ID 重复报告、因缺上下文导致的误报，以及报告缺少结论可信度分级。

#### 第 1 步：确定性聚类

新增 `evoagent/finding_cluster.py`：

- 键：`(path, line)` 为第一键；证据归一化（空白/大小写折叠，复用 `experience.normalize_evidence` 思路）后的相似度为第二键。
- 同一 `(path, line)` 上规则 ID 不同的 Finding 归为同一簇，保留严重度最高者为主 Finding，其余作为 `duplicates` 记录。
- `EVOAGENT_FINDING_CLUSTERING=off|shadow|on`：`shadow` 只统计重复数（记入 run/report 的 `clustering` 元数据），不改变输出；`on` 才合并。

#### 第 2 步：多 Agent 一致性与置信度

- [agents.py](file:///d:/研究生/1/project/EvoAgent/evoagent/agents.py) 已维护 `sources[key] -> [agent,...]` 与仲裁 `decisions`，直接导出为 `finding_sources` 到 collaboration 元数据（仅追加）。
- 新增 `evoagent/confidence.py`：
  - `agent_consensus = |支持该 finding 的 agent 数| / |审到该位置的 agent 数|`。
  - 历史误报率：查询该 `rule_id` 在 `failure_cases`（`false_positive`）与 `skill_usage_stats` 中的累计 `false_positive_feedback / findings_proposed`，作为先验衰减因子。
  - 合成 `confidence = clamp(base_confidence * consensus_factor * (1 - fp_rate))`。
- `EVOAGENT_CONFIDENCE_ENHANCE=off|on`，默认 `off` 保持原有 confidence。

#### 第 3 步：结果分级

- ReviewReport **只追加** `classification` 字段：`{"confirmed": [...], "needs_review": [...], "suggestion": [...]}`（按置信度与验证结果分桶，阈值可配 `EVOAGENT_CONFIDENCE_BUCKETS`）。
- Markdown 报告 `to_markdown` 追加分级小节；原段落顺序与内容不变。

#### 工作包六测试

- 同位置不同规则 ID 的重复 Finding 在 `on` 下合并且不丢主项。
- `shadow` 下输出与 `off` 完全一致，仅元数据不同。
- 一致性 1.0 与 0.33 的两类 finding 置信度符合预期单调性。
- 高误报率规则历史降低其置信度。
- 报告追加字段不影响原字段与 API 快照。

验收：默认无行为变化；开启后重复显著减少、报告附带可解释的置信度依据。

回滚：`EVOAGENT_FINDING_CLUSTERING=off` + `EVOAGENT_CONFIDENCE_ENHANCE=off`。

---

### 工作包七：结构化安全修复升级

目标：把 [fixer.py](file:///d:/研究生/1/project/EvoAgent/evoagent/fixer.py) 的字符串替换升级为 AST 补丁，避免破坏格式，并保证"修复前复现、修复后验证"。

#### 第 1 步：AST 补丁生成

新增 `evoagent/ast_fixer.py`：

- 对支持的规则（硬编码凭据、`eval`、`shell=True`、调试 print 等）用 `ast` 解析源文件，定位目标节点，通过**节点替换/删除/插入**生成新源码，用 `ast.unparse` 或保留原行尾/缩进的方式输出。
- 限制：单次补丁修改文件数 `<= EVOAGENT_AST_FIX_MAX_FILES`（默认 3）、修改行数上限，超限则拒绝并返回 `rejected_reason`。
- 不支持的规则原样返回"未修改"（保持现状语义）。

#### 第 2 步：修复后验证

- 复用现有 `RepairVerifier`：修复前先复现（静态重现 finding 命中），修复后重跑审查确认该 finding 消失且未引入新 finding。
- `EVOAGENT_REPAIR_TEST_COMMAND` 配置时执行编译/测试门禁（已有），失败只保存候选补丁、不推送分支（现状已满足，补充断言）。

#### 第 3 步：暗开关

`EVOAGENT_AST_FIXER_ENABLED=off|on`，默认 `off`：`off` 时 `create_fix_commits` 继续走原 `SafeFixer` 字符串替换路径，行为与首轮完全一致。

#### 工作包七测试

- 同输入下 AST 补丁与字符串补丁语义等价但格式保留（缩进/注释不破坏）。
- `eval(user_input)` 被改写为白名单映射而非删除。
- 修改文件/行数超限时拒绝并给出原因。
- 修复后复验：重新审查无该 finding、无新增误报。
- `off` 时修复结果与旧 `SafeFixer` 逐字节一致。

验收：默认行为不变；开启后修复格式更安全、带复验证据。

回滚：`EVOAGENT_AST_FIXER_ENABLED=off`。

---

### 第二阶段可选扩展（本期不强制）

- 动态 Agent 选择（Planner 按风险/语言只跑必要 Agent）与"仅高风险结论进入深度讨论"：收益依赖真实数据，改动面大，**列为工作包八落地的评测指标上线后再评估**。
- 仓库只读工具系统（读邻近代码、查符号、找测试）：复用 Tool Registry，与动态 Planner 一起在第三阶段工作包十的 Agent 级指标可用后再扩展。

---

## 三、第三阶段：可信评测与生产演进

### 工作包八：真实数据集与持续评测指标

目标：把 `synthetic-controlled` 评测替换/补充为真实、可复现的评测数据，并让指标能回答"质量、成本、延迟、修复正确率"。

#### 第 1 步：真实 PR 数据集管线

- 扩展 [import_github_pr_dataset.py](file:///d:/研究生/1/project/EvoAgent/scripts/import_github_pr_dataset.py)：
  - 输入：真实 PR 的 diff + 人工标注 JSON（规范见文档附录：`{diff, repository, language, expected: [{path,line,rule_id,severity}]}`）。
  - 输出：写入 `evaluation_cases`（`source="github-real"`），按仓库隔离，不混入 `synthetic-controlled`。
  - `EVOAGENT_EVAL_SOURCE=builtin|github-real|all` 选择评测数据源；`github-real` 样本不足时 `auto_propose` 保持 `deferred`（沿用现有门禁）。
- 干净 PR、重构 PR、大 PR、多语言 PR 分类标签入库，供分桶统计。

#### 第 2 步：指标扩展

- 扩展 `RegressionEvaluator` 输出（只追加键）：
  - `false_positive_rate`、`per_finding_cost_estimate`（LLM token/耗时，本地模式为 null）、`latency_ms`、`fix_correctness`（复用反馈 `accepted/bad_fix` 统计）。
  - Holdout 仍只落聚合指标，不落明细。
- `evaluation_benchmark.py` 改造成可插拔数据源（`source` 参数），`run_e2e_evaluation.py` 支持输出 JSON 报告。

#### 第 3 步：评测运行管理

- `evolution_runs`/`skill_evolution_runs` 的 metrics 追加 `dataset_source`、`dataset_sha256`（追加键，不改旧键）。

#### 工作包八测试

- 真实标注样本能导入并在 `source="github-real"` 下评测。
- 指标新键在 SQLite/PostgreSQL 往返一致；旧运行记录读取得不到新键时不报错（`dict.get` 缺省）。
- 样本不足时进化候选保持 `deferred` 且不污染 builtin 路径。

验收：可对真实 PR 数据复现评测；质量/成本/延迟指标可解释。

回滚：`EVOAGENT_EVAL_SOURCE=builtin`。

---

### 工作包九：反馈可信与过拟合防护

目标：防止错误/恶意/重复反馈污染进化，并防止候选对 Validation 过拟合。

#### 第 1 步：反馈可信度

- `EVOAGENT_FEEDBACK_MIN_CONFIRMERS=1`（默认 1，行为不变）；>1 时 `missed_issue` 需多个独立任务确认才生成候选（复用 Experience 的 corroborate 计数，enforce 模式已具备该能力，扩展到 shadow/主路径）。
- `EVOAGENT_FEEDBACK_TRUST_ENABLED=off|on`：`on` 时按反馈者历史（`accepted` 占比）加权，低可信反馈降级为 `observed` 不直接进候选。

#### 第 2 步：持续多版本对比与冷却期

- `SkillEvolutionEngine._propose` 增加 `EVOAGENT_EVOLUTION_COMPARE_HISTORY=1|3`：baseline 从"当前 Active"扩展为"当前 + 最近 N 个历史版本"，candidate 需对全部 baseline 均不退化（`shadow` 先只记录对比结果，`enforce` 才参与门禁）。
- 自动回滚/拒绝后的同一候选设置冷却期 `EVOAGENT_EVOLUTION_COOLDOWN_MINUTES`（默认 0，行为不变）；冷却期内不重复评测同指纹候选。

#### 第 3 步：Holdout 轮换

- `EVOAGENT_HOLDOUT_ROTATION=0|N`：每 N 次激活轮换一批 holdout（从不向候选生成流程暴露明细，只保留指纹）；`0` 表示不轮换（默认，行为不变）。
- 轮换后旧 holdout 样本进入 `archived` 状态保留审计。

#### 第 4 步：运行指纹扩展

- Provenance 追加：`model`、`dataset_source`、`tool_version`、`runtime_version`（已有）、`prompt_fingerprint`（追加键，不改旧键）。

#### 工作包九测试

- 低可信反馈在 `on` 下不直接生成候选；多确认人达到阈值才生成。
- 候选对历史版本非退化（enforce）且 shadow 只记录对比结果。
- 冷却期内同指纹候选不重复评测。
- Holdout 轮换不暴露明细、旧样本保留审计。
- 全部默认值下与原行为一致。

验收：默认零变化；开启后抗污染、抗过拟合，决策依据完整可审计。

回滚：全部开关恢复默认值。

---

### 工作包十：生产可观测性与运维

目标：让系统具备"能否安全长期运行"的可见性与治理手段。

#### 第 1 步：健康检查分离

- 新增 `GET /health/live`（进程存活）与 `GET /health/ready`（依赖就绪：SQLite/PostgreSQL、Redis、GitHub token 可选、LLM 可选），`/health` 保持原样兼容。

#### 第 2 步：Agent 级与质量指标

- [metrics.py](file:///d:/研究生/1/project/EvoAgent/evoagent/metrics.py) 追加：每 Agent 调用次数/耗时/失败率、按租户×仓库×规则×模型的 finding 分布、每规则误报率。
- 告警扩展：队列积压、租约回收、死信数量（复用 `AlertManager` 的 key 模式）。

#### 第 3 步：优雅关闭完善

- 关闭顺序固定为：HTTP 停止接收新请求 → 队列排空/停止 worker → 数据库连接 → Trace exporter；`ReviewService.close()` 继续幂等。

#### 第 4 步：迁移与备份工具

- 新增 `scripts/migrate_db.py`：只读检查 + 幂等迁移（复用 `_ensure_column`/`ADD COLUMN IF NOT EXISTS` 风格），支持 `--check` 干跑。
- 备份/恢复：SQLite 使用 SQL 在线备份（`VACUUM INTO`），PostgreSQL 使用 `pg_dump`，文档化恢复步骤。

#### 工作包十测试

- `/health`、`/health/live`、`/health/ready` 三种路径行为正确且 `/health` 快照不变。
- 关闭顺序在并发任务下无异常、无 ResourceWarning。
- 迁移脚本在旧库夹具上 `--check` 幂等、不产生 destructive 变更。
- 新指标键在 /metrics 输出存在且不影响旧键。

验收：可观测性完整、优雅关闭可靠、迁移可回滚。

回滚：新端点/指标均为追加，恢复旧版本即可，无迁移回滚负担。

---

## 四、数据库升级与回滚清单

沿用首轮纪律，每次改造：

1. 使用升级前版本构建夹具（含任务、反馈、Active Skill、Rejected Skill）。
2. 复制夹具启动新版本，校验任务/反馈/Skill 数量、哈希、Active 指针不变。
3. 重启第二次验证幂等。
4. 执行一次审查、反馈、Skill 进化与回滚全流程。
5. SQLite 全量跑通；PostgreSQL 在 CI 容器跑同一契约。

本阶段新增字段（`Finding.analyzer`、run metrics 追加键、Provenance 追加键、`evaluation_cases.source` 等）全部为**追加**，旧版本可忽略；不重建、不重命名任何现有索引或列。

## 五、建议提交顺序

1. `feat(analysis): add stdlib AST semantic reviewer behind a dark switch`
2. `feat(reporting): add deterministic finding clustering and confidence`
3. `feat(fixer): add AST-based safe repairs with post-verify`
4. `feat(evaluation): add real-PR dataset pipeline and extended metrics`
5. `feat(evolution): add feedback trust, history comparison and holdout rotation`
6. `feat(ops): split health checks, agent metrics and graceful shutdown`
7. `docs: add phase 2/3 test results`

每个提交：相关模块测试 + 全量 unittest + 旧库夹具 + API 快照，且不与下一个工作包混合。

## 六、明确暂缓范围

- 任意代码生成 / 执行（保持声明式 + 受限工具）。
- 全自动合并、删除、归档 Skill（Curator 仍只建议）。
- LLM 自由决定 Experience 类型。
- 完整跨仓库消融评测（先做单 Skill 边际，再扩展）。
- 在线强化学习与模型权重训练。
- 默认启用任何 Enforce / 外部分析器。
- 无人工确认的高风险大范围修复自动推送。

## 七、最终验收场景

- **场景 E（语义增强）**：开启 `ast` 后能发现规则层遗漏的跨行问题，关闭后行为与首轮一致。
- **场景 F（聚类与置信度）**：重复 finding 合并、高误报规则置信度下降、报告附分级，原字段不变。
- **场景 G（真实评测）**：真实 PR 数据可评测，质量/成本/延迟指标可解释，样本不足不污染 builtin。
- **场景 H（抗污染）**：低可信单条反馈不产生候选；候选需多版本非退化；冷却期与 Holdout 轮换生效且可审计。
- **场景 I（可观测）**：`/health/live|ready` 正确、Agent 级指标可见、优雅关闭无泄漏、迁移可干跑与回滚。

完成以上场景并保持全量测试、旧库夹具与 API 快照全绿后，方可认为第二、三阶段不会破坏项目可运行性。
