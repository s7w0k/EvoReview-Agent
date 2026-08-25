# EvoAgent 报告对话与受控经验沉淀逐步实施计划

本文档基于当前 EvoAgent 的任务报告、用户反馈、分层记忆、Experience Router、Prompt Evolution 和 Skill Evolution 链路制定。目标是在不破坏现有审查与演进能力的前提下，增加一个面向任务报告的对话工作台，使用户可以围绕报告、Finding、Diff 和 Trace 进行分析，并将对话中形成的有效纠正以可追溯、可确认、可回滚的方式进入现有沉淀链路。

本方案遵循项目已有的兼容策略：**只追加、不替换；先旁路、后启用；SQLite/PostgreSQL 契约一致；任何对话都不能绕过现有演进门禁。**

---

## 一、目标与非目标

### 1.1 本期目标

1. 用户可以从已完成任务进入独立的报告对话页面。
2. 对话助手可以基于当前任务报告、关联 Finding、有效 Diff 行、Trace 摘要和少量相关记忆回答问题。
3. 回答中的关键判断必须带结构化引用，并由服务端验证引用真实存在。
4. 系统可以从对话中生成“误报、漏报、坏修复、已接受”候选结论。
5. 候选结论必须经过用户确认，才能复用现有 `record_feedback()` 链路进入 Failure Case、Memory 和 Experience。
6. 对话、引用、模型、Prompt、报告版本和沉淀来源均可审计。
7. 支持租户隔离、请求幂等、失败重试、成本限制和功能开关回退。

### 1.2 本期非目标

- 不允许模型根据聊天内容直接修改 Prompt、Skill 或生产规则。
- 不把普通解释性问答自动写入 Semantic Memory。
- 不允许模型执行任意代码、Shell、网络请求或自动修复。
- MVP 不支持跨仓库对话、全局知识问答和自动合并多条经验。
- MVP 不要求流式输出；先完成同步请求和完整可靠性验证。
- 不改变现有 `POST /v1/tasks/{id}/feedback` 的请求和响应语义。
- 不绕过 Validation、Holdout、可信反馈或版本激活门禁。

---

## 二、当前基础与推荐接入点

当前主链路为：

```text
审查任务
   ↓
ReviewReport + Trace + Collaboration
   ↓
任务详情页上的结构化反馈
   ↓
failure_cases + semantic memory
   ↓
Experience Router（off / shadow / enforce）
   ↓
Prompt / Skill 候选
   ↓
Validation + Holdout + 激活/回滚门禁
```

新增链路应插在“任务报告”和“结构化反馈”之间：

```text
ReviewReport / Finding / Diff / Trace / Memory
                      ↓
              任务级报告对话
                      ↓
        回答 + 引用 + 候选结论（draft）
                      ↓
           用户确认 / 修改 / 驳回
                      ↓
       复用 ReviewService.record_feedback()
                      ↓
        原有沉淀、评测、演进和门禁链路
```

推荐代码边界：

| 层级 | 当前文件 | 新增或修改方向 |
|---|---|---|
| Web 页面 | `web/index.html` | 增加“报告对话”导航、任务入口和三栏工作台 |
| Web 行为 | `web/app.js` | 会话、消息、候选卡片、确认与引用跳转 |
| Web 样式 | `web/app.css` | 对话布局、消息、引用、候选状态和响应式样式 |
| API | `evoagent/api.py` | 增加会话、消息、候选确认/驳回路由 |
| 对话领域层 | 新增 `evoagent/chat.py` | 上下文构建、模型调用、引用校验、候选提取 |
| Service | `evoagent/service.py` | 组合 Store、Chat Analyzer 和现有反馈入口 |
| Store | `evoagent/store.py`、`evoagent/postgres_store.py` | 双数据库会话、消息、上下文快照和候选方法 |
| 配置 | `evoagent/config.py` | 功能、预算、轮次、内容长度和保留策略开关 |
| 迁移 | `scripts/migrate_db.py` | 将新增表和兼容列纳入检查与幂等迁移 |
| 测试 | `tests/` | 单元、Store 契约、Service、API、安全与前端手测 |

---

## 三、不可违反的设计约束

### 3.1 对话与沉淀隔离

```text
用户消息 ──→ chat_messages
                 │
                 ├── 普通回答：到此结束
                 │
                 └── 候选结论：chat_insights(draft)
                                      ↓ 用户确认
                              record_feedback()
```

- `chat_messages` 不是演进数据源。
- `chat_insights.status=draft` 不是演进数据源。
- 只有 `confirmed` 候选允许调用现有反馈链路。
- 模型置信度不能代替用户确认。
- 用户确认也不能代替现有的 Experience corroboration 和回归评测。

### 3.2 权限与租户隔离

- 创建会话、发送消息、确认候选需要 `review` 权限。
- 查看会话可使用 `read` 权限，但查询必须同时限制 `tenant_id` 和任务归属。
- 所有 Session、Message、Snapshot 和 Insight Store 方法都必须接收 `tenant_id`。
- 禁止只按公开 UUID 查询记录后再在 API 层过滤；租户条件必须进入 SQL。
- `task_id` 必须对应当前租户可访问的任务。

### 3.3 证据约束

- Diff 引用必须落在 `parse_unified_diff()` 得到的真实新增行上。
- Finding 引用必须能匹配当前报告中的 Finding。
- Trace 引用必须来自当前任务的 Trace 或 Collaboration 记录。
- Memory 只能从当前租户、当前仓库召回，并作为不可信上下文使用。
- 找不到可验证证据时，回答必须明确标记为推测；不得生成高置信规则候选。

### 3.4 版本约束

- 会话创建时保存当前报告的规范化 SHA-256 指纹。
- 每个 Assistant Message 保存本轮上下文指纹和 Prompt 版本。
- 如果任务报告发生变化，旧会话标记为 `stale`。
- 旧会话仍可读取，但确认候选前必须重新验证或由用户重新确认。

---

## 四、配置与默认开关

所有新增配置默认保持现有系统行为不变：

```env
# 总开关；默认关闭时不创建路由、不显示可用状态
EVOAGENT_CHAT_ENABLED=false

# 是否允许从回答生成候选；关闭时仅做解释性问答
EVOAGENT_CHAT_INSIGHTS_ENABLED=false

# 是否允许把已确认候选送入现有反馈链路
EVOAGENT_CHAT_FEEDBACK_ENABLED=false

# 单会话和单轮预算
EVOAGENT_CHAT_MAX_ROUNDS=30
EVOAGENT_CHAT_MAX_MESSAGE_CHARS=8000
EVOAGENT_CHAT_CONTEXT_TOKENS=10000
EVOAGENT_CHAT_MAX_OUTPUT_TOKENS=1600
EVOAGENT_CHAT_TIMEOUT_SECONDS=60

# 上下文范围
EVOAGENT_CHAT_MAX_FINDINGS=20
EVOAGENT_CHAT_MAX_DIFF_LINES=120
EVOAGENT_CHAT_MAX_TRACE_ITEMS=30
EVOAGENT_CHAT_MEMORY_LIMIT=4

# 数据保留；0 表示不自动清理
EVOAGENT_CHAT_RETENTION_DAYS=0
```

`Settings.validate_evolution()` 或拆出的统一 `validate()` 必须校验：

- 所有整数限制为合理正数或显式允许的非负数。
- `CHAT_CONTEXT_TOKENS` 小于模型/系统总上下文预算。
- `CHAT_FEEDBACK_ENABLED=true` 时，`CHAT_INSIGHTS_ENABLED` 和 `CHAT_ENABLED` 也必须为 true。
- 未配置 LLM 时允许服务启动，但发送消息返回明确的“分析模型未配置”错误。

推荐灰度顺序：

```text
chat=false
   ↓
chat=true, insights=false
   ↓
chat=true, insights=true, feedback=false
   ↓
chat=true, insights=true, feedback=true
```

---

## 五、数据模型与状态机

### 5.1 `chat_sessions`

```sql
CREATE TABLE chat_sessions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    title TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    report_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

索引：

- `(tenant_id, task_id, updated_at)`
- `(tenant_id, created_by, updated_at)`

状态：

```text
active → stale → archived
   └────────────→ archived
```

### 5.2 `chat_messages`

```sql
CREATE TABLE chat_messages (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    status TEXT NOT NULL,
    error TEXT,
    client_request_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, client_request_id)
);
```

消息状态：`pending | completed | failed`。用户消息和模型失败记录均保留，便于安全重试和审计。

### 5.3 `chat_context_snapshots`

```sql
CREATE TABLE chat_context_snapshots (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    report_fingerprint TEXT NOT NULL,
    context_fingerprint TEXT NOT NULL,
    references_json TEXT NOT NULL,
    truncation_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Snapshot 首期不保存完整原始 Diff，只保存被引用对象、裁剪元数据和指纹，减少敏感代码复制。

### 5.4 `chat_insights`

```sql
CREATE TABLE chat_insights (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    category TEXT NOT NULL,
    finding_json TEXT NOT NULL,
    note TEXT NOT NULL,
    confidence REAL NOT NULL,
    validation_json TEXT NOT NULL,
    status TEXT NOT NULL,
    confirmed_by TEXT,
    feedback_case_id INTEGER,
    supersedes_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

状态机：

```text
draft ──→ confirmed
  │           │
  ├──→ rejected
  └──→ superseded
```

如果确认过程需要防止并发，可在内部使用短暂的 `confirming` 状态，但对外响应仍应归并为上述稳定状态。

### 5.5 现有 `failure_cases` 的兼容追加

为实现确认幂等，建议只追加：

```sql
ALTER TABLE failure_cases ADD COLUMN source_key TEXT;
CREATE UNIQUE INDEX ... ON failure_cases(task_id, source_key)
WHERE source_key IS NOT NULL;
```

对话确认时使用：

```text
source_key = chat_insight:<insight_id>
```

旧反馈的 `source_key` 为 `NULL`，原行为不变。PostgreSQL 和 SQLite 分别使用其支持的部分唯一索引语法。

---

## 六、逐步工作包

## 工作包 0：契约冻结与安全基线

目标：在增加功能前冻结现有反馈行为，并补齐对话沉淀所需的身份归因。

### 步骤 0.1：冻结现有反馈契约

为以下行为增加回归测试：

- 只有 `SUCCESS + report` 任务可提交反馈。
- 支持 `false_positive / missed_issue / bad_fix / accepted`。
- 反馈写入 Failure Case 和 Semantic Memory。
- Experience `off / shadow / enforce` 行为保持不变。
- 相同租户可读、其他租户不可读。

涉及：

- `tests/test_service.py`
- `tests/test_experience.py`
- API 测试文件；若当前没有统一 API 测试夹具，则新增 `tests/test_chat_api.py` 时一并建立。

### 步骤 0.2：传递反馈者身份

当前 API 调用 `record_feedback()` 时未传入已登录用户。修改 `evoagent/api.py`：

```python
self.service.record_feedback(
    task_id, category, finding, note,
    principal.tenant_id,
    feedbacker=principal.user_id,
)
```

兼容要求：

- `feedbacker` 继续是可选参数。
- 未开启认证的现有调用保持可用。
- 审计日志仍记录用户名，反馈可信计算使用稳定的 `user_id`。

### 步骤 0.3：定义共享 Schema 常量

在 `evoagent/chat.py` 中定义：

- 会话、消息、候选状态集合。
- 候选类别集合，复用 `experience.py` 的类别语义。
- Citation 类型：`report | finding | diff | trace | memory`。
- 最大字段长度和规范化函数。

### 工作包 0 验收

- 现有反馈测试全部通过。
- API 提交的反馈包含稳定反馈者身份。
- 未启用 Chat 时 API、数据库和前端行为无变化。

---

## 工作包 1：数据库与 Store 契约

目标：先建立纯数据能力，不接模型、不改前端。

### 步骤 1.1：SQLite 幂等建表

在 `evoagent/store.py` 的 `_init()` 中追加四张表、索引和 `failure_cases.source_key`。使用现有 `_ensure_column()` 保持旧库原地升级。

### 步骤 1.2：PostgreSQL 幂等建表

在 `evoagent/postgres_store.py` 追加完全对等的表、索引和列。字段含义、空值处理、JSON 编解码和返回结构必须与 SQLite 一致。

### 步骤 1.3：新增 Store 公共方法

建议的最小方法集：

```python
create_chat_session(...)
get_chat_session(session_id, tenant_id)
list_task_chat_sessions(task_id, tenant_id, limit=50)
update_chat_session_status(session_id, tenant_id, status)

append_chat_message(...)
get_chat_message(message_id, tenant_id)
list_chat_messages(session_id, tenant_id, limit=100)
complete_chat_message(...)
fail_chat_message(...)

save_chat_context_snapshot(...)
get_chat_context_snapshot(message_id, tenant_id)

create_chat_insight(...)
get_chat_insight(insight_id, tenant_id)
list_chat_insights(session_id, tenant_id)
update_chat_insight_status(...)
```

所有返回值统一为普通 `dict`，JSON 字段在 Store 边界完成反序列化。

### 步骤 1.4：反馈幂等能力

- `record_failure_case()` 追加可选 `source_key=None`。
- 插入对话反馈时按 `(task_id, source_key)` 幂等返回已有记录。
- 方法返回新增或已有 Failure Case 的 ID；旧调用可忽略返回值。
- `ReviewService.record_feedback()` 追加可选 `source_key` 和 `source_metadata`，默认值保持旧行为。

### 步骤 1.5：迁移检查

更新 `scripts/migrate_db.py`：

- `REQUIRED_TABLES` 加入四张 Chat 表。
- `REQUIRED_COLUMNS` 加入 `failure_cases.source_key`。
- `--check` 继续只读。
- 迁移执行两次结果一致。

### 步骤 1.6：Store 契约测试

扩展 `tests/test_store_contract.py`，同一套测试同时覆盖：

- 会话的租户隔离。
- 消息顺序和 JSON 往返。
- `client_request_id` 幂等。
- Insight 状态转换。
- `source_key` 重复确认只产生一个 Failure Case。
- SQLite 立即执行；PostgreSQL 在配置数据库的 CI 中执行。

### 工作包 1 验收

- 旧数据库可原地升级且数据不丢失。
- 新表在 SQLite/PostgreSQL 结构和行为一致。
- 迁移检查通过且幂等。
- 尚未启用任何对话路由和模型调用。

回滚：关闭 Chat 开关。新增表保留，不做破坏性降级。

---

## 工作包 2：对话上下文与模型适配层

目标：建立可独立测试的报告问答核心，不暴露 HTTP API。

### 步骤 2.1：实现报告指纹

对 `task["report"]` 使用稳定 JSON 序列化：

```python
json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

计算 SHA-256，用于会话绑定、消息快照和过期检测。

### 步骤 2.2：实现 `ChatContextBuilder`

新增 `evoagent/chat.py`，按以下顺序构建上下文：

1. 当前任务元数据、报告摘要和风险等级。
2. 与问题关键词或显式引用相关的 Finding。
3. Finding 对应的真实新增 Diff 行和有限邻近行。
4. Trace/Collaboration 的摘要，不直接注入无限原始消息。
5. 通过 `MemoryManager.recall()` 获取的少量当前仓库记忆。
6. 最近若干轮消息；超限时使用确定性摘要或截断。

输出：

```python
{
    "text": "...",
    "references": [...],
    "report_fingerprint": "...",
    "context_fingerprint": "...",
    "truncation": {...},
}
```

必须把 Report、Diff、Trace、Memory 和历史消息标记为“不可信数据”，禁止其中内容覆盖 System Prompt。

### 步骤 2.3：模型传输层

首选方案是新增一个小型 OpenAI-compatible `ChatModelClient`，复用 `settings.resolved_llm()` 的：

- `base_url`
- `api_key`
- `model`
- `provider`
- `headers`
- timeout

不要直接调用 `OpenAICompatibleReviewer._request_json()`，因为该方法是审查器私有实现且绑定 Finding 输出协议。后续可再把两者的 HTTP 传输抽成共享模块，但不应在 Chat MVP 中强制重构已有 Reviewer。

### 步骤 2.4：结构化模型输出

模型固定返回 JSON：

```json
{
  "answer": "...",
  "citations": [
    {"type": "finding", "ref": "finding:0"},
    {"type": "diff", "path": "app/api.py", "line": 42}
  ],
  "insights": [
    {
      "category": "false_positive",
      "finding_ref": "finding:0",
      "note": "...",
      "confidence": 0.78
    }
  ]
}
```

服务端不得直接信任模型结果：

- 删除不在 Context References 中的 Citation。
- 对 Finding 引用重新从报告复制结构，禁止模型伪造完整 Finding。
- 对 Diff 路径和行号重新调用解析结果验证。
- `confidence` 限制在 `[0, 1]`。
- Note 重新清理、脱敏和截断。
- Insights 开关关闭时忽略模型返回的所有候选。

### 步骤 2.5：本地无模型降级

当 `resolved_llm()` 为空时：

- 会话列表和历史仍可读取。
- 创建会话可以允许，也可以由产品配置决定；建议允许。
- 发送消息返回 `409 chat model is not configured`。
- 不使用本地规则伪造自然语言答案。

### 工作包 2 测试

- Context 只包含当前租户和当前任务数据。
- 超预算时优先保留问题、相关 Finding 和证据。
- 恶意 Diff 中的“忽略系统指令”不会进入 System Prompt 控制区。
- 伪造 Citation 被过滤。
- 不合法漏报位置只能生成低置信语义候选或直接不生成候选。
- 模型超时、HTTP 错误、非 JSON 响应都转为稳定领域错误。

### 工作包 2 验收

- 通过 Mock LLM 可以完成独立问答测试。
- 每个回答都有可复现的报告和上下文指纹。
- 不产生任何 Feedback、Memory 或 Experience 副作用。

---

## 工作包 3：Chat Service 与 HTTP API

目标：提供租户安全、可重试的同步 API。

### 步骤 3.1：Service 方法

在 `ReviewService` 增加：

```python
create_chat_session(task_id, title, principal)
list_task_chat_sessions(task_id, principal)
get_chat_session(session_id, principal)
send_chat_message(session_id, content, client_request_id, principal)
reject_chat_insight(insight_id, principal)
```

确认动作放到工作包 5，在 Feedback 总开关关闭时不注册或返回功能禁用。

### 步骤 3.2：API 路由

建议路径：

```text
POST /v1/tasks/{task_id}/chat/sessions
GET  /v1/tasks/{task_id}/chat/sessions
GET  /v1/chat/sessions/{session_id}
POST /v1/chat/sessions/{session_id}/messages
POST /v1/chat/insights/{insight_id}/reject
```

响应约定：

- 创建会话：`201`
- 发送并完成回复：`201`
- 重放相同 `client_request_id`：返回原结果，不重复调用模型。
- Chat 未启用：`404` 或 `409`；项目内统一选择一种，推荐 `409 feature disabled` 便于前端提示。
- 未配置模型：`409`。
- 模型暂时失败：`502` 或 `503`，同时保存 failed message。
- 超长消息/轮次耗尽：`400`。
- 越权：`403`；租户外资源建议统一返回 `404`，减少资源枚举。

### 步骤 3.3：发送消息事务顺序

```text
校验 Session / Task / Report 指纹
        ↓
按 client_request_id 创建 pending 用户消息
        ↓
构建 Context Snapshot
        ↓
调用模型
        ↓
服务端校验 Answer / Citations / Insights
        ↓
保存 Assistant Message + Snapshot + draft Insights
        ↓
更新 Session.updated_at
```

模型调用不能持有数据库长事务或 Store 锁。

### 步骤 3.4：接口审计

追加审计事件：

- `chat.session.create`
- `chat.message.send`
- `chat.message.failed`
- `chat.insight.reject`

审计 Detail 只记录 ID、数量、模型和状态，不记录完整消息正文或密钥。

### 工作包 3 测试

- 路由方法、状态码和 JSON Schema。
- 租户隔离和 RBAC。
- 相同请求 ID 不重复调用模型。
- 报告变化后 Session 变为 stale。
- failed message 可重试且不丢用户原始输入。
- Chat 关闭时现有 API 快照不变。

### 工作包 3 验收

- 可通过 API 完成任务级多轮问答。
- 普通问答仍不会写入现有沉淀链路。
- 所有模型调用均有会话、消息和审计关联。

---

## 工作包 4：前端报告对话工作台

目标：提供清晰的报告解释、引用和候选确认前置体验。

### 步骤 4.1：增加入口

- 左侧导航增加“报告对话”。
- 任务报告区域增加“对话分析”按钮。
- 只有 `SUCCESS + report` 任务显示可用入口。
- Chat 关闭或模型未配置时显示明确状态，不隐藏错误原因。

### 步骤 4.2：三栏页面

```text
┌────────────┬────────────────────────┬──────────────────┐
│ 任务/会话   │ 对话消息与输入框        │ 报告证据/候选结论 │
│            │                        │                  │
│ 最近任务    │ 用户问题                │ 报告摘要          │
│ 历史会话    │ AI 回答 + 引用           │ Finding / Diff    │
│            │ 建议问题                │ Draft Insight     │
└────────────┴────────────────────────┴──────────────────┘
```

窄屏降级为：任务抽屉 + 主对话 + 可折叠证据面板。

### 步骤 4.3：消息与引用

- 用户消息、Assistant Message、失败状态分开显示。
- Citation 可点击定位右侧 Finding、Diff 行或 Trace 摘要。
- “推测”与“已有证据支持”使用不同标签。
- 页面只使用 `textContent` 或现有 `escapeHtml()`，禁止直接插入模型 HTML。

### 步骤 4.4：候选卡片

候选卡片展示：

- 类型：误报、漏报、坏修复、已接受。
- 关联 Finding 或路径/行号。
- 判断依据和模型置信度。
- 证据完整性校验结果。
- “编辑”“确认沉淀”“驳回”按钮。

在工作包 4 阶段，`CHAT_FEEDBACK_ENABLED=false`，确认按钮显示“沉淀功能尚未启用”或隐藏，仅允许查看、编辑草稿和驳回。

### 步骤 4.5：基础交互健壮性

- 发送期间禁用重复提交。
- 使用 `crypto.randomUUID()` 生成 `client_request_id`。
- 网络失败保留输入并支持重试。
- 切换任务时取消过期 UI 更新，避免旧请求覆盖新会话。
- 使用 `aria-live` 宣告回答状态，保持键盘可操作性。

### 工作包 4 手工验收

- 从任务中心一键进入绑定任务的会话。
- 创建、切换和恢复历史会话。
- 引用能定位正确报告证据。
- 长回答、长文件名、移动端和 reduced-motion 正常。
- 模型失败、鉴权过期、Chat 关闭均有可理解提示。

---

## 工作包 5：候选确认与受控沉淀

目标：在完成问答稳定性后，才启用对话到反馈的桥接。

### 步骤 5.1：类别级校验

#### `false_positive`

- 必须关联当前报告中真实存在的 Finding。
- 保存报告 Finding 的服务端副本，而不是模型生成副本。
- Note 必须说明为什么不成立或缺少何种上下文。

#### `missed_issue`

- 高质量 Rule Candidate 必须具备合法 `rule_id + path + line`。
- `path + line` 必须映射到 Diff 新增行。
- 不满足时只允许用户补充，或明确降级为 Semantic Memory 类型反馈。

#### `bad_fix`

- 应关联原 Finding 或修复结果。
- Note 至少描述破坏行为、兼容性问题或验证失败。

#### `accepted`

- 支持 Finding 级接受；任务级接受首期可转成不关联 Finding 的 Positive Signal。
- 正反馈只影响统计和证据，不直接提高线上规则权重。

### 步骤 5.2：允许用户编辑候选

建议新增：

```text
PATCH /v1/chat/insights/{insight_id}
```

只允许修改 draft Insight 的：

- `category`
- `finding` 的受控引用或漏报位置
- `note`

每次修改重新执行服务端校验，并在 `validation_json` 中记录结果。

### 步骤 5.3：确认接口

```text
POST /v1/chat/insights/{insight_id}/confirm
```

确认流程：

```text
校验 review 权限和 tenant
        ↓
校验 Insight=draft、Session/Task/Report 未过期
        ↓
原子占有 draft → confirming
        ↓
record_feedback(
  source_key="chat_insight:<id>",
  source_metadata={session_id, message_id, insight_id, report_fingerprint}
)
        ↓
保存 feedback_case_id，状态改为 confirmed
```

失败处理：

- 可恢复错误将 Insight 恢复为 `draft`。
- 进程在 confirming 阶段崩溃时，通过 `source_key` 查询已有 Feedback；存在则补记 confirmed，不存在则恢复 draft。
- 重复确认直接返回已确认结果，不重复写 Memory 或 Experience。

### 步骤 5.4：沉淀来源追踪

Failure Case Payload 追加：

```json
{
  "source": "chat_insight",
  "chat": {
    "session_id": "...",
    "message_id": "...",
    "insight_id": "...",
    "report_fingerprint": "..."
  }
}
```

Memory 和 Experience 继续通过现有 `record_feedback()` 产生，避免双写两套业务逻辑。

### 步骤 5.5：冲突与更正

- 同一 Finding 同时出现 `accepted` 和 `false_positive` 时提示冲突，不自动覆盖。
- 用户修改已确认结论时创建新 Insight，并通过 `supersedes_id` 指向旧 Insight。
- 旧 Failure Case 不删除；通过更正记录和后续演进门禁处理。
- 首期不自动撤销已经消费的 Experience。

### 工作包 5 测试

- 普通消息、draft、rejected 均不产生 Feedback。
- confirmed 恰好产生一次 Failure Case、Memory 和按配置产生 Experience。
- 双击、网络重试和并发确认最多写入一次。
- 非法漏报位置无法确认成 Rule Candidate。
- 报告过期时阻止确认，重新验证后才能继续。
- Chat 反馈关闭时确认接口无副作用。

### 工作包 5 验收

- 用户能清楚看到“对话建议”和“已进入沉淀链路”的区别。
- 每条 Chat 来源反馈都能回溯到 Session、Message、Insight 和报告版本。
- 原有演进门禁完全保留。

---

## 工作包 6：安全、隐私与可靠性加固

目标：在扩大灰度前处理内容安全、资源消耗和生命周期问题。

### 步骤 6.1：输入与输出安全

- 消息长度、JSON 深度、Citation 数量和 Insight 数量设置硬上限。
- 对模型输入中的 Diff、报告、Trace 和 Memory 使用明确数据边界。
- 复用 `experience.normalize_evidence()` 的已知密钥脱敏，并扩展到保存前的引用片段。
- 不记录 Authorization、API Key 或完整 Provider 错误响应。
- 前端不渲染模型提供的 HTML；Markdown 若后续启用，必须使用白名单 Sanitizer。

### 步骤 6.2：预算与限流

- 单用户、单租户和单会话的并发限制。
- 单轮 Context Token 和 Output Token 上限。
- 单会话最大轮数。
- 模型超时后不自动无限重试。
- 在 Metrics 中记录拒绝原因：`budget / rate_limit / timeout / invalid_output`。

### 步骤 6.3：数据保留

- MVP 提供“归档会话”，不立即物理删除审计来源。
- 可选保留期任务只删除未形成反馈的普通聊天正文。
- 已形成反馈的会话保留最小来源摘要和指纹。
- 删除策略必须按租户执行，并避免删除 Failure Case、Experience 或 Evolution Run。

### 步骤 6.4：故障恢复

- 服务启动时扫描超时的 `pending` Message 和 `confirming` Insight。
- pending Message 标记为 failed，可由用户重试。
- confirming Insight 按 `source_key` 对账并恢复一致状态。
- Chat 故障不得影响审查任务队列、Webhook 或现有反馈 API。

### 工作包 6 验收

- Prompt Injection、跨租户 ID 猜测、超长输入、重复请求和模型异常测试通过。
- 对话服务故障不影响核心审查链路。
- 敏感字段不会出现在日志、审计 Detail 和 API 错误中。

---

## 工作包 7：可观测性、灰度与后续优化

### 步骤 7.1：指标

在现有 Metrics 上追加：

- `chat_sessions_total`
- `chat_messages_total{status}`
- `chat_request_duration_seconds{provider,model}`
- `chat_input_tokens_total`、`chat_output_tokens_total`
- `chat_insights_total{category,status}`
- `chat_insight_confirmation_rate`
- `chat_feedback_total{category}`
- `chat_invalid_citations_total`
- `chat_stale_sessions_total`
- `chat_failures_total{reason}`

避免把 `tenant_id`、`repository` 或 Session ID 作为无限基数标签；需要分租户分析时写入受控日志或数据库聚合。

### 步骤 7.2：Trace 与审计

- 每次模型请求生成 request/correlation ID。
- Message 保存 provider、model、Prompt 版本和 Token 使用。
- 日志关联 task_id、session_id、message_id，但不输出正文。
- Context Snapshot 记录裁剪数量，便于解释回答缺失信息。

### 步骤 7.3：灰度阶段

#### 阶段 A：开发环境，只读问答

- `CHAT_ENABLED=true`
- `CHAT_INSIGHTS_ENABLED=false`
- `CHAT_FEEDBACK_ENABLED=false`

关注：正确性、引用有效率、延迟和模型异常。

#### 阶段 B：候选旁路

- 开启 Insights。
- 用户可以查看和驳回候选。
- 仍禁止确认进入 Feedback。

关注：候选生成率、驳回率、类别准确性和证据完整率。

#### 阶段 C：小范围确认沉淀

- 开启 Feedback。
- 只向管理员或指定租户开放。
- Experience 建议先保持 `shadow`。

关注：重复写入、冲突反馈、确认率和后续候选质量。

#### 阶段 D：正式开放

- 扩大到 Maintainer。
- 仍保留全部门禁和紧急关闭开关。
- 评估是否需要 SSE、仓库级会话和对话摘要。

### 步骤 7.4：后续可选能力

仅在 MVP 指标证明有效后考虑：

- SSE 流式回答。
- 长会话摘要和上下文压缩。
- 跨任务的仓库趋势分析。
- 多人共同确认同一 Insight。
- 报告版本对比和重复问题聚类。
- 从多个确认 Insight 形成 Procedure Skill Proposal。

---

## 七、接口建议示例

### 7.1 创建会话

```http
POST /v1/tasks/{task_id}/chat/sessions
Content-Type: application/json

{"title":"分析本次权限风险"}
```

```json
{
  "id": "session-uuid",
  "task_id": "task-uuid",
  "status": "active",
  "report_fingerprint": "sha256...",
  "created_at": "..."
}
```

### 7.2 发送消息

```http
POST /v1/chat/sessions/{session_id}/messages
Content-Type: application/json

{
  "content": "SEC-EVAL 为什么是高风险？这里是否可能是误报？",
  "client_request_id": "browser-generated-uuid"
}
```

```json
{
  "user_message": {"id":"...","status":"completed"},
  "assistant_message": {
    "id":"...",
    "content":"...",
    "citations":[
      {"type":"finding","ref":"finding:0"},
      {"type":"diff","path":"app/api.py","line":42}
    ]
  },
  "insights":[
    {
      "id":"...",
      "category":"false_positive",
      "status":"draft",
      "confidence":0.78,
      "validation":{"valid":true,"issues":[]}
    }
  ]
}
```

### 7.3 确认候选

```http
POST /v1/chat/insights/{insight_id}/confirm
Content-Type: application/json

{}
```

```json
{
  "id":"insight-uuid",
  "status":"confirmed",
  "feedback_case_id":123,
  "feedback":{
    "category":"false_positive",
    "experience":{
      "experience_type":"rule_refinement",
      "status":"observed"
    }
  }
}
```

---

## 八、测试矩阵

| 层级 | 必测内容 |
|---|---|
| Chat 单元测试 | 指纹、Context 排序/裁剪、Citation 校验、Insight 校验、脱敏 |
| Store 契约 | SQLite/PostgreSQL 表结构、JSON 往返、租户隔离、幂等、状态转换 |
| Service | 创建会话、发送消息、模型失败、stale、确认与原反馈链路复用 |
| API | RBAC、状态码、请求上限、错误结构、重复 request ID |
| 安全 | Prompt Injection、跨租户访问、伪造 Finding/Diff 引用、敏感数据日志 |
| 并发 | 双击发送、并发确认、进程中断后的恢复 |
| 回归 | 全量现有审查、反馈、Experience、Evolution、Skill 测试 |
| 前端手测 | 桌面/移动布局、键盘操作、长内容、错误恢复、鉴权过期 |

建议新增测试文件：

```text
tests/test_chat_context.py
tests/test_chat_service.py
tests/test_chat_api.py
tests/test_chat_security.py
tests/test_chat_store_contract.py
```

如果 Store 契约已有统一 Mixin，应优先扩展现有 `test_store_contract.py`，避免重复维护。

---

## 九、实施顺序、依赖与交付物

```text
WP0 契约冻结/身份归因
            ↓
WP1 Schema + Store + Migration
            ↓
WP2 Context + Model + Validation
            ↓
WP3 Service + API
            ↓
WP4 Web 工作台
            ↓
WP5 Confirm → record_feedback
            ↓
WP6 安全/恢复/保留策略
            ↓
WP7 指标/灰度/后续扩展
```

每个工作包应形成一个可独立验收的提交或 PR，不把 Schema、模型、前端和沉淀功能一次性合入。

推荐交付节奏：

| 里程碑 | 包含工作包 | 可见结果 |
|---|---|---|
| M0 基线 | WP0 | 反馈身份和回归契约完整 |
| M1 数据层 | WP1 | 会话数据可持久化，尚无模型调用 |
| M2 API MVP | WP2-WP3 | 可通过 API 做安全的任务级问答 |
| M3 UI MVP | WP4 | 用户可在控制台完成报告对话 |
| M4 受控沉淀 | WP5 | 确认候选复用现有反馈与演进链路 |
| M5 生产准备 | WP6-WP7 | 安全、恢复、指标和灰度机制完备 |

---

## 十、最终 Definition of Done

只有同时满足以下条件，报告对话功能才算完成：

1. 用户能从成功任务创建、恢复和继续任务级会话。
2. 回答能引用真实 Finding、Diff 行或 Trace，伪造引用会被服务端剔除。
3. 普通聊天和未确认候选不会产生 Failure Case、Memory、Experience 或 Evolution 输入。
4. 确认候选最多写入一次，并能追溯到用户、会话、消息、报告版本和模型版本。
5. 漏报不具备有效新增行证据时不能生成可执行 Rule Candidate。
6. 报告变更后旧会话被识别为 stale，不能静默确认旧候选。
7. 所有 Chat 数据严格按租户隔离，RBAC 和审计完整。
8. LLM 未配置、超时、返回非法 JSON 或暂时不可用时，不影响核心审查、Webhook、队列和原反馈入口。
9. SQLite 和 PostgreSQL 通过同一 Store 契约测试，迁移可重复执行。
10. 前端桌面与移动布局可用，消息失败可重试，内容不会产生 XSS。
11. 全量现有测试保持通过，默认 `EVOAGENT_CHAT_ENABLED=false` 时行为与改造前一致。
12. 对话确认产生的反馈仍必须经过现有可信证据、Validation、Holdout、激活和回滚门禁。

---

## 十一、建议首先执行的三个任务

1. **WP0：补齐反馈者身份并冻结现有反馈契约**，避免对话反馈上线后无法可靠归因。
2. **WP1：完成四张表、`source_key` 幂等列和双 Store 契约**，先解决数据正确性。
3. **WP2：实现可独立测试的 Context Builder 与 Citation Validator**，先证明回答证据可信，再接页面和沉淀。

完成以上三项后，再进入 API/UI MVP；在只读对话稳定前，不应提前开启 `EVOAGENT_CHAT_FEEDBACK_ENABLED`。
