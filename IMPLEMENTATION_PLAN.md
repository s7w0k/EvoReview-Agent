# EvoAgent 关键优化逐步实施方案（兼容性审定版）

本文档根据 [`OPTIMIZATION_ROADMAP.md`](./OPTIMIZATION_ROADMAP.md) 制定，并经过现有源码、数据库结构、API 和测试用例复审。目标是在不破坏项目现有可运行性的前提下，逐步强化自进化的证据、追溯和治理能力。

首轮只实施四个关键工作包：

1. 修复资源生命周期和测试基线。
2. 以兼容方式增加 Skill 来源和生命周期。
3. 旁路引入 Experience Router 和证据聚合，再逐步切换自动进化数据源。
4. 先观测边际指标，再启用只读 Skill Curator。

暂不实现 Procedure Skill DSL、自动合并或删除 Skill、任意代码生成、复杂静态分析平台和全自动生产发布。

## 一、不可违反的兼容性原则

所有改造必须采用以下顺序：

```text
新增兼容字段或表
        ↓
回填旧数据并验证
        ↓
新旧路径双写
        ↓
旁路读取和指标对比
        ↓
通过配置显式切换
        ↓
稳定一个发布周期后再考虑清理旧路径
```

必须遵守：

- 不删除或重命名现有数据库列、Store 公共方法、API 路径和响应字段。
- 新字段必须有安全默认值，旧 SQLite/PostgreSQL 数据库必须原地升级成功。
- 升级前 `active=1` 的 Skill 必须在升级后仍保持激活。
- 现有 `decision=activated|rejected|deferred` 语义和 API 返回必须保留。
- 新 API 字段只能追加，不能改变已有字段的类型或含义。
- Experience 首期只旁路双写，现有 `failure_cases` 仍是自动进化的主数据源。
- 新边际门禁首期只计算和记录，不得默认阻止现有候选激活。
- 每个工作包都必须能通过配置回退到改造前行为。
- SQLite 和 PostgreSQL 必须实现同一组 Store 公共方法并通过契约测试。

## 二、交付开关与默认值

新增配置均应保持现有行为为默认值：

```env
# 首期只把反馈旁路写入 Experience，不改变 auto_propose 数据源
EVOAGENT_EXPERIENCE_MODE=shadow

# shadow: 只记录边际指标；enforce: 指标参与激活门禁
EVOAGENT_SKILL_MARGINAL_GATE=shadow

# 首期关闭自动归档和自动状态整理
EVOAGENT_SKILL_CURATOR_ENABLED=false

# 仅当 Experience 成为主数据源后才生效
EVOAGENT_EVOLUTION_MIN_EVIDENCE=2
EVOAGENT_EVOLUTION_MIN_DISTINCT_TASKS=2
```

允许值：

- `EVOAGENT_EXPERIENCE_MODE=off|shadow|enforce`
- `EVOAGENT_SKILL_MARGINAL_GATE=off|shadow|enforce`

任何未知值都应在启动时失败并给出清晰错误，不得静默选择高风险模式。

## 三、工作包一：资源生命周期和绿色基线

这是其余改造的硬性前置条件。完成前不得修改自进化数据流。

### 第 1 步：安全修复 SQLite 连接关闭

涉及：

- `evoagent/store.py`
- 新增 `tests/test_store_lifecycle.py`

当前 `_connect()` 被大量测试和实现代码间接依赖。不要删除或改变它的返回类型，而是新增事务上下文：

```python
from contextlib import contextmanager

def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self.path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

@contextmanager
def _connection(self):
    conn = self._connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

然后机械地将 Store 内部的：

```python
with self._connect() as conn:
```

改为：

```python
with self._connection() as conn:
```

注意事项：

- `_init()` 也必须使用 `_connection()`，它正是当前最早泄漏连接的位置。
- 同一事务中调用其他 Store 方法可能重新获取锁；不得在这一改动中将 `threading.Lock` 替换为其他并发模型。
- 保留当前每个公共方法一个短事务的行为，不引入长连接或连接池。
- 不在此步骤修改 SQL、返回结构或锁粒度。

### 第 2 步：保持 PostgreSQL 原有连接语义

`psycopg.Connection` 的上下文语义和 SQLite 不完全相同。首轮不要机械替换 `postgres_store.py` 的所有连接代码。

处理方式：

1. 为 PostgreSQL 同样增加 `_connection()` 包装器。
2. 包装器内部使用当前 psycopg 推荐的事务上下文，并在 `finally` 中确保关闭。
3. 先写一个 Store 契约测试，对 SQLite 全量运行；PostgreSQL 在依赖可用的 CI 服务中运行。
4. 在没有 PostgreSQL 测试环境前，不删除现有 `_connect()`。

### 第 3 步：增加幂等服务关闭

涉及：

- `evoagent/service.py`
- `evoagent/task_queue.py`
- `evoagent/observability.py`
- `evoagent/api.py`

`ReviewService.close()` 必须幂等：

```python
def close(self):
    with self._close_lock:
        if self._closed:
            return
        self._closed = True
    self.queue.close()
    self.observability.close()
```

约束：

- `TaskQueue.close()` 多次调用不报错。
- `Observability.close()` 在未安装 OpenTelemetry 时为空操作。
- 不关闭由调用方传入或全局共享的第三方 TracerProvider；只关闭本服务创建并持有的资源。
- `api.run()` 的 `finally` 改为 `service.close()`，但保留 `server.server_close()`。
- 现有调用 `service.queue.close()` 的代码仍然有效。

### 第 4 步：修正测试而不放宽功能断言

自动修复测试应由字符串引号断言改为 AST 语义断言，但仍需验证：

- 使用了 `os.environ`。
- 环境变量名正确。
- 原危险赋值被替换。
- 不支持的规则未被修改。
- 修复后 Python 能编译。

### 第 5 步：绿色基线验收

依次运行：

```powershell
python -m unittest discover -s tests -v
python -W error::ResourceWarning -m unittest discover -s tests -v
```

验收条件：

- 现有 53 项测试全部通过。
- Windows 临时 SQLite 文件可以立即删除。
- 无数据库 `ResourceWarning`。
- `ReviewService.close()` 和 `TaskQueue.close()` 可重复调用。
- 同一条审查任务的状态、报告和协作结果与改造前一致。

回滚方式：仅恢复 Store 内部上下文调用和 `service.close()` 接线，不涉及数据库迁移。

## 四、工作包二：兼容式 Skill 来源与生命周期

### 第 1 步：定义兼容状态模型

首期状态：

```text
draft → validated → active
   └→ rejected
validated ↔ active
active/validated/rejected → archived
```

允许 `active → validated`，因为现有回滚会激活历史版本；被替换的旧 Active 版本必须回到可再次激活的状态，而不能直接归档。

状态含义：

- `draft`：刚保存，评测尚未完成。
- `validated`：通过回放，可被激活或作为历史回滚目标。
- `active`：当前正式运行版本。
- `rejected`：未通过门禁，不允许激活。
- `archived`：人工明确归档，不参与运行，也不允许直接激活。

### 第 2 步：只追加数据库字段

为 SQLite 和 PostgreSQL 的 `skill_artifact_versions` 追加：

```sql
status TEXT,
origin TEXT,
repository_scope TEXT,
provenance_json TEXT,
patch_json TEXT,
updated_at TEXT,
activated_at TEXT,
archived_at TEXT
```

迁移必须分为“加列”和“回填”两步，不能直接使用 `status DEFAULT 'draft'` 判断旧数据：

```sql
UPDATE skill_artifact_versions
SET status = CASE WHEN active = 1 THEN 'active' ELSE 'validated' END
WHERE status IS NULL;

UPDATE skill_artifact_versions
SET origin = 'agent-created'
WHERE origin IS NULL;

UPDATE skill_artifact_versions
SET provenance_json = '{}'
WHERE provenance_json IS NULL;
```

原因：如果旧记录统一默认成 `draft`，升级后 `active=1` 与 `status=draft` 会冲突，并可能导致线上 Skill 消失。

迁移要求：

- 使用当前 SQLite `_ensure_column()` 风格，保持重复启动幂等。
- PostgreSQL 使用 `ADD COLUMN IF NOT EXISTS`，随后执行幂等回填。
- 不重建或删除现有唯一索引。
- 迁移前后记录数、版本号、`artifact_sha256` 和 Active 版本完全一致。

### 第 3 步：兼容 `active` 字段

首轮仍以现有 `active` 字段作为运行时读取的权威来源：

- `get_active_skill_artifact()` 继续查询 `active=1`。
- `list_active_skill_artifacts()` 继续查询 `active=1`。
- `dashboard_stats()` 继续按 `active` 统计。

状态转换必须在一个数据库事务内同步更新 `status` 和 `active`。不要先把读取条件切成 `status='active'`；至少经过一个发布周期并通过一致性检查后再考虑。

增加一致性诊断：

```text
active=1 且 status!=active
active=0 且 status=active
同一 tenant/skill 存在多个 active=1
```

发现不一致时启动失败或记录高优先级告警，不应静默修复生产数据。

### 第 4 步：新增生命周期模块

新增 `evoagent/skill_lifecycle.py`，集中定义状态和合法转换。Store 增加：

```python
transition_skill_artifact(
    tenant_id,
    skill_name,
    version,
    target_status,
    actor,
    reason,
)
```

激活必须原子完成：

1. 检查目标版本为 `validated` 或已经 `active`。
2. 当前 Active 版本更新为 `validated, active=0`。
3. 目标版本更新为 `active, active=1`。
4. 写审计记录。

现有 `activate_skill_artifact()` 方法保留，内部委托到新转换方法，以维持公共 API 和测试兼容。

### 第 5 步：兼容现有保存与评测顺序

现有 `_propose()` 是“先完成评测，再保存版本”。首轮不要为了展示 `draft` 而提前写入半成品，否则服务崩溃会留下没有 evolution run 的版本。

安全做法：

1. 保持评测计算顺序不变。
2. 保存版本时写入最终状态：
   - `activated` → `active`
   - `rejected` → `rejected`
   - `deferred` → `draft`
3. 同一事务或可恢复顺序中保存版本与 evolution run。
4. 后续若确实需要持久化评测中的 `draft`，再单独设计 run/candidate 事务，不放入首轮。

这样保持现有 `propose()` 返回和自动激活行为不变。

### 第 6 步：追加 Provenance

自动生成版本记录：

```json
{
  "origin": "agent-created",
  "source_task_ids": [],
  "source_case_ids": [],
  "source_experience_ids": [],
  "generator": {
    "type": "feedback-rule-builder",
    "version": "1"
  },
  "dataset": {
    "validation_sha256": "...",
    "holdout_sha256": "..."
  },
  "runtime_version": "0.3"
}
```

约束：

- Holdout 只保存指纹和聚合指标。
- 手工 `propose()` 的 `origin` 为 `operator`。
- 现有调用 `save_skill_artifact()` 未传新参数时保持原行为，并使用安全默认值。
- Provenance 只追加到 API 响应，不修改原字段。

### 第 7 步：API 和归档

版本列表只追加 `status`、`origin`、`repository_scope` 和 `provenance`。

新增归档接口前，先确认版本不是当前 Active；首轮禁止直接归档 Active 版本。应先回滚/激活其他版本，再归档目标版本。

### 工作包二测试

必须新增：

- 从旧 Schema 数据库启动后，原 `active=1` 版本仍可被加载。
- 重复启动迁移不会改变任何版本数据。
- `active` 与 `status` 始终一致。
- 激活历史 Validated 版本可以完成回滚。
- Rejected/Archived 版本不能激活。
- 旧 `save_skill_artifact()` 调用签名继续工作。
- SQLite 和 PostgreSQL Store 返回字段一致。
- API 原字段和值保持不变，只增加新字段。

工作包二验收：

- 旧数据库可原地升级并保持现有 Active Skill。
- 现有 Skill 进化测试的 `decision` 和激活结果不变。
- 新版本具有状态和来源追溯信息。
- 关闭新功能开关后，运行路径与改造前一致。

## 五、工作包三：Experience 旁路与渐进切换

### 第 1 步：新增 Experience 表，不修改 failure_cases

新增表：

```sql
CREATE TABLE experiences (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    repository TEXT,
    task_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    category TEXT NOT NULL,
    experience_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, fingerprint, task_id)
);
```

注意：需要单独的 `experience_type`。原方案只定义 `category`，无法同时保存原反馈类别 `missed_issue` 和路由结果 `rule_candidate`。

状态：

- `observed`
- `corroborated`
- `consumed`
- `rejected`

### 第 2 步：确定性 ExperienceRouter

新增 `evoagent/experience.py`。首轮禁止 LLM 决定分类。

路由规则：

- 完整 `missed_issue` → `rule_candidate`。
- 信息不完整的 `missed_issue` → `semantic_memory`。
- `false_positive` → `rule_refinement`，首轮只记录建议。
- `bad_fix` → `repair_candidate`，不进入 Skill Evolution。
- `accepted` → `positive_signal`，仅用于指标。

完整 Rule Experience 必须包含合法 `rule_id/path/line`，且能从原任务 Diff 的新增行中提取证据。

### 第 3 步：稳定指纹与隐私边界

指纹包含：

```text
tenant_id + repository + experience_type + rule_id + normalized evidence
```

不把 `task_id` 放入聚合指纹；数据库唯一约束中的 `task_id` 用于保证同一任务只计一次。

证据规范化只做：

- Unicode/空白规范化。
- 长度限制。
- 已知秘密格式脱敏。

不要在首轮泛化变量名、字符串或路径，避免不同缺陷被错误合并。

### 第 4 步：Shadow 双写

当 `EVOAGENT_EXPERIENCE_MODE=shadow`：

```text
record_feedback
  ├─ 原样写 failure_cases（主路径）
  ├─ 原样写 Memory（主路径）
  └─ 尝试写 Experience（旁路）
```

兼容要求：

- Experience 写入失败不能让原反馈 API 失败；记录指标和审计错误即可。
- `record_feedback()` 原返回 `{recorded, category}` 保持不变，只追加可选 `experience` 字段。
- `auto_propose()` 在 Shadow 阶段继续读取 `failure_cases`。
- 旁路至少运行一个完整测试/发布周期，比较两套来源会生成的候选是否一致。

### 第 5 步：证据聚合

配置：

```env
EVOAGENT_EVOLUTION_MIN_EVIDENCE=2
EVOAGENT_EVOLUTION_MIN_DISTINCT_TASKS=2
```

达到门槛时，将同指纹的全部 `observed` Experience 原子更新为 `corroborated`。必须按 tenant 和 repository 隔离。

### 第 6 步：Enforce 切换

只有在 Shadow 对比满足以下条件后，才允许设置：

```env
EVOAGENT_EXPERIENCE_MODE=enforce
```

切换条件：

- 所有旧反馈已完成可重复的回填或明确不回填。
- Shadow 生成候选与旧路径结果差异已经人工审核。
- Experience Store 在 SQLite/PostgreSQL 都通过故障和并发测试。
- 管理台能够查看 observed/corroborated/consumed 状态。

Enforce 模式下，`auto_propose()` 只读取 `corroborated rule_candidate`。激活成功后才将 Experience 标记为 `consumed`；拒绝时保留原状态并记录候选 run ID 与原因，便于后续重新评测。

### 第 7 步：失败案例兼容

在 Enforce 模式稳定前：

- 不删除 `failure_cases`。
- 继续支持 `/api/failures` 和任务反馈历史。
- Skill 激活成功后，同时更新对应 Experience 和旧 failure case 的 resolved 状态。
- 所有映射通过 Provenance 中的 `source_case_ids/source_experience_ids` 完成，禁止模糊匹配。

### 工作包三测试

- Shadow 写入失败不影响反馈原路径。
- 原 API 字段保持一致。
- 同任务重复反馈只计一份证据。
- 不同任务的相同证据可 corroborate。
- 不同租户或仓库绝不聚合。
- 不完整漏报不会成为 Rule Candidate。
- Shadow 模式 `auto_propose()` 仍读取 failure_cases。
- Enforce 模式只读取 corroborated Experience。
- 激活后新旧记录状态一致。

工作包三验收：

- 默认配置下现有反馈和进化行为完全不变。
- 可以旁路观察 Experience 分类质量。
- 显式启用 Enforce 后，单条反馈不再直接产生候选。
- 可一键切回 Shadow 恢复旧进化数据源。

## 六、工作包四：边际指标与只读 Curator

### 第 1 步：先核实当前评测器能力

当前 `RegressionEvaluator` 以单个 reviewer/artifact 对 Validation 和 Holdout 回放。首轮不要直接承诺“全部 Active Skills ± Candidate”的完整组合评测，除非先把评测器扩展为与运行时一致的 Composite Reviewer。

安全的首期边际指标：

```text
当前该 evolved skill 的 Active Artifact
候选 Artifact
Candidate Only（诊断）
```

先记录：

- Candidate 相对当前版本新增的 TP/FP。
- Finding 重合数。
- Validation F1 差值。
- Holdout 聚合非退化结果。

“所有不同 Skill 的组合消融”延后到评测器能够复用生产协调图之后。

### 第 2 步：Shadow 边际门禁

新增：

```env
EVOAGENT_SKILL_MARGINAL_GATE=shadow
EVOAGENT_SKILL_MIN_UNIQUE_TP=1
EVOAGENT_SKILL_MAX_NEW_FP=0
```

Shadow 模式只把结果写入 evolution run：

```json
{
  "marginal_gate": {
    "mode": "shadow",
    "would_pass": true,
    "unique_true_positives": 1,
    "new_false_positives": 0
  }
}
```

不得改变现有 `decision`。运行足够样本并校验指标后，才允许切换到 `enforce`。

### 第 3 步：Skill 使用指标

新增 `skill_usage_stats` 表。首期只统计能够可靠归属到 `skill_name@version` 的声明式 evolved reviewer：

- `executions`
- `findings_proposed`
- `findings_approved`
- `false_positive_feedback`
- `last_used_at`

不要把跨 Agent 合并后的 Finding 猜测归属到 Skill。需要在 Finding 或协作消息中保留明确 `source_skill` 元数据；未能明确归属时不计入版本指标。

指标写入失败不得导致审查任务失败。

### 第 4 步：只读 SkillCurator

新增 `evoagent/skill_curator.py`，只从版本和使用指标生成建议：

- 完全相同的 `match + scope` → duplicate。
- 明确高误报且样本数达到阈值 → tighten_trigger。
- 长期无执行或无独立贡献 → stale_candidate。

Curator：

- 不持有 Store 写方法。
- 不改变 Skill 状态。
- 不自动调用 `activate/archive`。
- 结果可即时计算或单独持久化为 recommendation，不能混入 Skill 版本表。

### 第 5 步：只读 API

首期只增加：

```http
GET /v1/skill-curator/recommendations
```

如果提供 `POST /analyze`，它也只能触发重算，不得产生状态变更。两者都要求 `manage` 权限。

### 工作包四测试

- Shadow 边际指标不改变原激活决策。
- Enforce 模式只在显式配置后生效。
- 指标无法归属时不错误计数。
- 指标写入失败不影响审查报告。
- Curator 能识别完全重复规则。
- Curator 不具备修改 Active Skill 的能力。
- 所有指标和建议按租户隔离。

工作包四验收：

- 默认配置不改变现有 Skill 激活行为。
- 可以解释候选相对当前版本的增量效果。
- Curator 只能建议，不能修改生产状态。
- 关闭 Curator 后不影响任何审查或进化路径。

## 七、数据库升级与回滚清单

每次数据库改造必须执行：

1. 使用升级前版本创建包含任务、反馈、Active Skill、Rejected Skill 的数据库夹具。
2. 复制数据库夹具并在副本上启动新版本。
3. 校验任务数、反馈数、Skill 版本数、哈希和 Active 指针不变。
4. 重启第二次，验证迁移幂等。
5. 用新版本执行一次审查、反馈和 Skill 回滚。
6. SQLite 运行完整流程；PostgreSQL 在容器 CI 中运行同一契约。

回滚策略：

- 新表和新列在首轮不删除，旧版本会忽略它们。
- 在未改变旧列语义前，可以直接回滚应用版本。
- Experience Enforce 出现问题时切回 `shadow`，恢复 failure_cases 主路径。
- Marginal Enforce 出现问题时切回 `shadow`，恢复原有门禁决策。
- 不使用不可逆的数据压缩、重命名或 destructive migration。

## 八、建议提交和验证顺序

1. `fix(store): close sqlite connections deterministically`
2. `fix(runtime): add idempotent service shutdown`
3. `test: make repair assertions syntax-aware`
4. `test: add legacy database upgrade fixtures`
5. `feat(skills): add backward-compatible lifecycle metadata`
6. `feat(experience): add shadow feedback routing`
7. `feat(experience): add corroboration and enforce switch`
8. `feat(evaluation): record shadow marginal metrics`
9. `feat(curator): add read-only recommendations`

每个提交必须：

- 运行相关模块测试。
- 运行全量 `unittest`。
- 验证旧数据库升级夹具。
- 检查 API 兼容快照。
- 不与下一个工作包混合提交。

## 九、明确暂缓范围

- Procedure Skill DSL。
- 可执行 Python/Shell Skill 自动生成。
- Skill 自动合并、删除、归档。
- LLM 自由决定 Experience 类型。
- 完整多 Skill 组合消融。
- 自动从外部安装 Skill。
- 在线强化学习和模型权重训练。
- 默认启用 Experience/Marginal Enforce。
- 自动 Shadow/Canary Skill 流量发布。

## 十、最终兼容性验收场景

### 场景 A：旧数据库升级

1. 旧数据库中已有 Active evolved Skill。
2. 新版本首次启动并执行迁移。
3. 原 Skill 仍是唯一 Active，版本、哈希和审查结果不变。
4. 第二次启动不再改变数据。
5. 历史版本仍可回滚激活。

### 场景 B：默认 Shadow 模式

1. 提交现有格式的 `missed_issue`。
2. 原 failure case 和 Memory 正常写入。
3. API 原字段不变，并可追加 Experience 信息。
4. Experience 写入失败时反馈仍成功。
5. `auto_propose()` 继续产生与改造前相同的结果。

### 场景 C：显式 Enforce 模式

1. 任务 A 的反馈只生成 observed Experience。
2. 任务 B 的独立同类反馈使其变为 corroborated。
3. `auto_propose()` 生成带 Provenance 的候选。
4. 候选通过原有 Validation/Holdout 门禁后激活。
5. Experience 与对应 failure cases 被精确标记为 consumed/resolved。
6. 切回 Shadow 后仍可使用旧 failure_cases 路径。

### 场景 D：Curator 隔离

1. Curator 识别重复或低价值 Skill。
2. 返回只读建议。
3. Active Skill、版本状态和审查结果完全不变。
4. 禁用 Curator 后系统其余能力不受影响。

完成以上四个场景，并保证全量测试、旧数据库夹具和 API 兼容快照全部通过后，才可认为首轮自进化优化不会破坏项目可运行性。
