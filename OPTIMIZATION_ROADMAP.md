# EvoAgent 后续优化路线图

本文档汇总 EvoAgent 当前可继续优化的方向。建议按照“先稳定基础，再提升审查效果，最后扩展生产能力”的顺序推进。

## P0：稳定性与工程质量

### 1. 修复 SQLite 连接泄漏

当前 `with self._connect()` 只负责事务提交或回滚，并不会关闭连接。在 Windows 环境中，这会导致临时数据库文件仍被占用，从而使大量测试在清理阶段失败。

建议：

- 为数据库连接实现显式的上下文管理器，并在 `finally` 中调用 `close()`。
- 统一 SQLite 和 PostgreSQL Store 的连接生命周期接口。
- 增加数据库连接及文件句柄泄漏测试。
- 测试结束时显式关闭 `ReviewService`、任务队列和数据库资源。
- 为服务增加统一的 `close()` 或 `shutdown()` 生命周期方法。

这是目前最优先的问题，因为它已经直接影响全量测试结果和 Windows 平台稳定性。

### 2. 恢复全量测试绿色

除数据库连接问题外，当前自动修复测试还依赖固定的单双引号形式，导致生成结果语义正确但断言失败。

建议：

- 测试补丁的语义或 AST，而不是固定比较单双引号风格。
- 将测试分为 `unit`、`integration`、`postgres`、`redis` 和 `llm` 等层级。
- 建立覆盖 Python 3.11、3.12、3.13 以及 Windows、Linux 的 CI 矩阵。
- 补充并发执行、任务取消、服务退出和异常恢复测试。
- 对 ResourceWarning、未关闭线程和未释放连接进行严格检查。

## P1：审查准确率

### 3. 从逐行正则升级为语义分析

目前本地规则主要检查 Diff 的新增单行，速度快且结果确定，但容易遗漏跨行、跨函数和数据流问题。

建议逐步加入：

- Python AST 和 JavaScript/TypeScript Parser。
- 污点分析：跟踪外部输入流向 SQL、Shell、文件系统和反序列化等危险位置。
- 跨行异常处理、权限判断和资源释放分析。
- 根据文件类型和语言选择专门的审查 Agent。
- 接入 Ruff、Bandit、Semgrep、ESLint 等成熟工具，并统一转换为 `Finding`。

应保留现有规则作为快速、确定性的第一层，在其上叠加语义分析能力。

### 4. 改进误报过滤和结果合并

目前主要使用 `path + line + rule_id` 去重。同一个问题可能被不同 Agent 以不同规则 ID 重复报告，也可能因为缺少上下文而产生误报。

建议：

- 基于位置、证据和语义相似度对 Finding 聚类。
- 引入多 Agent 一致性评分。
- 将规则历史误报率和仓库级反馈纳入置信度计算。
- 对测试代码、生成代码、迁移文件和示例代码采用不同审查策略。
- 将结果区分为“确定缺陷”“需要人工确认”和“改进建议”。
- 在报告中展示结论来源、支持 Agent 和反对意见。

## P1：Agent 架构

### 5. 提升多 Agent 协作的有效性

当前已经实现完整的规划、专审、质疑、反思、补证、验证和仲裁链路，但 Critic、Evidence 和 Verifier 的判断仍以确定性检查为主，推理深度可以继续提升。

建议：

- Planner 根据 Diff 风险动态选择 Agent，不必每次执行全部 Agent。
- Critic 针对结论内容提出实质性反例，而不只是检查证据格式。
- Evidence Agent 获取有限的仓库上下文，例如被修改函数、调用者和对应测试。
- 将 Agent 的耗时、Token、工具调用和实际贡献度纳入评估。
- 对连续失败或长期低价值的 Agent 自动降级、暂停或替换。
- 仅让高风险或存在冲突的结论进入深度讨论，以降低延迟和成本。
- 根据历史结果优化任务分配，而不是固定执行相同协作图。

### 6. 扩充安全的工具系统

当前 LLM Agent 的工具主要围绕 Diff 和记忆，可进一步增加受限的只读工具：

- 读取目标文件附近代码。
- 搜索符号定义和引用。
- 查找对应测试。
- 读取依赖清单和配置文件。
- 查询静态分析结果。
- 在临时工作树中运行受限测试。

所有工具仍应保持参数 Schema 校验、路径白名单、调用超时、输出截断、权限隔离和审计记录。

## P1：自动修复

### 7. 提升修复可靠性

当前自动修复只覆盖少量可确定安全的规则，这是合理的安全起点。

建议：

- 使用 AST/CST 生成补丁，避免字符串替换破坏格式。
- 对 Python 优先考虑 LibCST；多语言场景可考虑 tree-sitter 或语言专用重写工具。
- 限制候选补丁修改的文件数和代码行数，确保补丁最小化。
- 修复前复现问题，修复后验证风险确实消失。
- 根据项目结构自动发现测试、格式化、静态检查和编译命令。
- 自动生成修复说明、验证结果和仍未解决的风险。
- 任何质量门禁失败时只保存候选补丁，不推送远程分支。
- 对高风险或大范围修复要求人工确认。

## P2：进化与评测

### 8. 引入真实、人工标注的数据集

现有 100 条评测数据标记为 `synthetic-controlled`，适合验证评测代码和进化流程，但不能代表真实 PR 上的生产效果。

建议建立：

- 基于开源项目真实 PR 的数据集。
- 人工确认的缺陷位置、类型和严重程度。
- 按仓库隔离的 Train、Validation 和 Holdout。
- 干净 PR、重构 PR、大型 PR 和多语言 PR 样本。
- Precision、Recall、F1、误报率、成本和耗时等持续指标。
- 独立的修复正确率指标，而不是只衡量缺陷检出率。
- 公开可复现的数据来源、标注规范和数据集版本指纹。

### 9. 防止反馈污染和进化过拟合

反馈驱动进化需要防范错误反馈、恶意反馈、重复样本以及对 Validation 的过拟合。

建议：

- 引入反馈者可信度或最少确认人数。
- 对样本进行内容去重和来源追踪。
- 限制单条反馈生成规则的覆盖范围。
- 持续比较新版本与多个历史版本，而不只是当前版本。
- 为自动回滚后的候选设置冷却期。
- 定期轮换 Holdout，但不向候选生成流程暴露具体内容。
- 将 Prompt、模型、数据集、工具和 Runtime 版本共同纳入运行指纹。
- 保存候选生成依据和激活决策，支持完整审计。

## P2：生产化

### 10. 完善可观测性与运维能力

当前已有基础 Prometheus 指标、Trace 接口和失败率告警，可以继续补充：

- 每个 Agent 的调用次数、耗时、失败率、Token 和费用。
- 队列积压、重试、租约回收和死信数量告警。
- 按租户、仓库、规则和模型统计审查质量。
- 将 `/health/live` 与 `/health/ready` 分离。
- 优雅关闭 HTTP 服务、队列、数据库连接和 Trace exporter。
- 数据库迁移工具以及备份、恢复方案。
- 结构化日志和统一的 trace ID、task ID。
- 对 PostgreSQL、Redis、GitHub 和 LLM Provider 增加依赖健康检查。

## 推荐实施顺序

### 第一阶段：可靠性基线

1. 修复 SQLite 连接生命周期。
2. 修正脆弱的字符串形式测试。
3. 为服务和资源增加统一关闭接口。
4. 建立 Windows/Linux 跨平台 CI。
5. 使全量测试稳定通过且没有资源泄漏警告。

目标：解决“系统是否可靠”。

### 第二阶段：审查效果

1. 引入 AST 和成熟静态分析工具。
2. 改进 Finding 聚类、去重和置信度计算。
3. 扩充安全的仓库只读工具。
4. 根据风险动态选择 Agent 和协作深度。
5. 使用结构化重写提升自动修复安全性。

目标：解决“审查是否有效”。

### 第三阶段：可信评测与生产演进

1. 建立真实、人工标注的 PR 数据集。
2. 完善版本门禁和反馈可信机制。
3. 衡量实际审查质量、延迟和成本。
4. 完善灰度、影子流量、回滚及可观测性。
5. 基于真实评测结果继续优化 Agent、记忆和自动修复。

目标：解决“效果是否有可信证据，以及能否安全投入长期运行”。

## 建议优先处理的三个任务

如果近期开始实施，建议优先建立以下三个任务：

1. **数据库资源生命周期整改**：解决 SQLite 文件句柄占用和测试清理失败。
2. **全量测试与跨平台 CI 整改**：建立可靠的回归基线。
3. **AST/静态分析增强设计**：为下一阶段的审查准确率提升确定技术方案和接口边界。

## 参考 Hermes 的自进化机制优化

### 核心结论

EvoAgent 当前通过两条独立链路进化：一条更新 `llm-review` Prompt，另一条从确认反馈生成不可执行的声明式 Rule Skill。候选需经过 Validation、Holdout、版本激活与回滚门禁。

Hermes Agent 更侧重将成功任务、失败后的正确路径和用户纠正沉淀为程序性 Skill，并支持局部 Patch、按需加载和后台整理。它的经验管理能力值得借鉴，但允许 Agent 自由修改复杂 Skill 也会增加错误积累、来源混淆和 Skill 膨胀风险。

因此推荐采用：**Hermes 式经验分类、程序性 Skill 与生命周期管理，加上 EvoAgent 式不可执行候选、证据评测和分阶段发布门禁。** 不建议让 Agent 根据单次成功或反馈直接修改生产审查逻辑。

### 1. 增加 Experience Router

在反馈进入进化引擎前先进行分类：

```text
任务轨迹 / 用户反馈 / 仲裁结果 / 修复验证
                    ↓
             Experience Router
        ┌───────────┼────────────┐
        ↓           ↓            ↓
      Memory     Rule Skill   Procedure Skill
                                  ↓
                         Prompt / Tool Proposal
```

- 仓库事实和偏好进入 Semantic Memory。
- 可确定的缺陷模式生成 Rule Skill 候选。
- 可复用的审查步骤生成 Procedure Skill 候选。
- Agent 行为偏差进入 Prompt Evolution。
- 工具能力不足形成 Tool Improvement Proposal，交由人工评审。
- 一次性或低可信信息不持久化，或等待更多独立证据。

### 2. 引入受限的 Procedure Skill DSL

将当前“学习匹配字符串”扩展为“学习如何审查某类变更”，但仍不允许生成任意代码。例如 DSL 可声明：

- 适用路径、语言和风险域。
- 需要执行的只读检查步骤。
- 必须收集的证据类型。
- 最大文件数、时间和工具预算。
- 验证条件和结构化输出 Schema。

所有步骤只能引用 Tool Registry 中预先注册的只读工具，禁止任意 Python、Shell、正则表达式和网络访问。

### 3. 使用结构化 Patch 演进 Skill

候选版本保存增量操作，而不是每次完整重写 artifact：

- `add_rule`
- `tighten_trigger`
- `add_exception`
- `change_severity`
- `add_verification_step`
- `deprecate_rule`
- `merge_rules`

每个 Patch 记录父版本、来源反馈、修改理由和影响范围，从而便于审计、归因和局部回滚。

### 4. 建立完整生命周期

建议状态流转为：

```text
draft → quarantined → validated → shadow → canary → active → stale → archived
```

- `draft`：刚生成的候选。
- `quarantined`：通过 Schema、来源、权限和内容安全检查。
- `validated`：通过 Validation 与 Holdout 回放。
- `shadow`：旁路运行，不影响最终报告。
- `canary`：只参与小比例任务并受错误预算保护。
- `active`：正式参与审查。
- `stale`：长期无命中、低贡献或高误报。
- `archived`：已被替代或确认无价值，仅保留审计记录。

每个版本记录使用次数、命中次数、误报率、仲裁拒绝率、边际贡献、耗时、Token 成本和最近使用时间。

### 5. 增加 Skill Curator

借鉴 Hermes Curator 管理持续增长的 Skill 库，但默认只提出整理候选：

- 聚类相似 Skill，发现重复、包含和冲突规则。
- 提议合并、拆分、收窄范围或归档。
- 将长期无命中或高误报 Skill 标记为 `stale`。
- 对整理前后版本重新运行 Validation、Holdout 和消融评测。
- 未通过门禁时不得修改 Active Skill。

### 6. 强化来源与权限治理

为 Skill 标记来源并限制修改权限：

- `builtin`：内置只读。
- `operator`：人工维护，Agent 只能提出 Patch。
- `agent-created`：允许生成候选，但必须经过门禁。
- `imported`：先隔离和安全扫描。
- `tenant-local` / `repository-local`：限制生效范围。

所有 Skill 应携带内容哈希、签名、父版本、来源任务、数据集指纹、生成模型、Prompt 指纹、权限声明及工具白名单。后台进化不得直接修改 Built-in 或人工维护版本。

### 7. 从单次反馈升级为证据聚合

采用分层学习流程：

```text
单次信号 → Experience → Hypothesis → Skill Candidate
                                      ↓
                          回放 → Shadow → Canary → Active
```

只有当同类漏报、误报、Critic 否决理由、工具调用路径或修复失败在多个独立任务中重复出现，或经过人工确认后，才生成 Skill 候选。单次成功或单条反馈不得直接产生 Active Skill。

### 8. 选择性加载和渐进式披露

根据文件路径、语言、依赖、Diff 特征、仓库策略和历史风险，只加载相关 Skill：

- Metadata 始终供 Planner 查看。
- Procedure 仅在触发条件命中后加载。
- References 由 Agent 按需通过只读工具读取。
- Templates 可用于输出或修复候选，但不能直接执行。

这可以降低上下文占用、执行延迟和无关 Skill 引起的误报。

### 9. 评估 Skill 的边际贡献

除总体 F1 外，增加消融评测：

```text
全部 Active Skills
全部 Active Skills - 目标 Skill
全部 Active Skills + 候选 Skill
仅候选 Skill
```

衡量候选独立增加的 TP/FP、与已有 Skill 的重复度、跨仓库/语言/模型迁移能力，以及质量收益是否值得新增延迟和 Token 成本。只有产生稳定正向边际贡献的 Skill 才长期保留。

### 推荐目标架构

```text
任务轨迹 + 用户反馈 + 仲裁结果 + 修复验证
                    ↓
             Experience Store
                    ↓
             Experience Router
        ┌───────────┼────────────┐
        ↓           ↓            ↓
      Memory     Rule Skill   Procedure Skill
                    ↓            ↓
             Candidate Generator
                    ↓
     静态检查 / 冲突检测 / 来源与权限验证
                    ↓
       Validation + Repository-level Holdout
                    ↓
              Shadow → Canary → Active
                    ↓
       Usage Metrics + Skill Curator
                    ↓
       Patch / Merge / Archive / Rollback
```

### 实施顺序

1. 实现 `ExperienceRouter`、Skill Provenance 和完整生命周期，使经验正确分类、版本完全可追溯。
2. 增加使用指标、消融评测和只生成建议的 `SkillCurator`，治理重复、冲突和 Skill 堆积。
3. 引入受限 Procedure Skill DSL、结构化 Patch 和选择性加载，将系统升级为学习安全、可验证的审查流程。
4. 最后再评估是否提高 Agent 的自动修改权限；始终保留 Validation、Holdout、Shadow、Canary、自动回滚和人工审计。
