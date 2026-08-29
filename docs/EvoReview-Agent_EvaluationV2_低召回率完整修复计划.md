# EvoReview-Agent：Evaluation V2 低召回率完整修复计划

> 编制日期：2026-08-28  
> 适用范围：`six-agent-v2` 的 Current Harness、Self-Evolved、Evaluation V2 及其 CI Hard Gates  
> 前置文档：`docs/EvoReview-Agent_EvaluationV2接入SixAgentV2_逐步实施计划.md`  
> 当前结论：评测接线已经修复，现阶段低分来自真实的路由缺陷、候选 Skill 领域接入缺陷、规则覆盖不足和跨扫描器重复发现。

---

## 1. 修复目标

本计划不是继续调整评分器，也不修改已冻结的 100-case Benchmark 标签，而是修复真实运行链：

```text
PR Diff
→ RiskProfiler / SemanticChangeSummary
→ SemanticPlanner / FallbackPlanner
→ Security / Reliability Specialist
→ Stable Rules + Candidate Skill + Semantic Scanner
→ Critic / Verifier
→ CWE 归一化与 Finding 去重
→ Evaluation V2 Matcher
```

最终必须同时满足：

1. 安全类 Diff 能稳定路由到 `security-agent`；
2. 可靠性类 Diff 能稳定路由到 `reliability-agent`；
3. 高风险或跨领域 Diff 执行 Security + Reliability 双路由；
4. Candidate Skill 根据规则领域进入正确 Specialist，而不是只挂载到 Security；
5. 补齐现有稳定规则无法识别的主要 CWE；
6. 同一漏洞被规则扫描器和语义扫描器重复发现时只计一个 Finding；
7. Critical Misses 始终为 0；
8. 评测修复不得通过读取 case id、预期标签或 Holdout 答案实现；
9. Current 与 Evolved 的唯一实验变量仍然是 Frozen Candidate Skill；
10. 新的泛化结论必须来自未参与本轮诊断和调参的新盲测集。

---

## 2. 当前可信基线

数据集：100 条受控 PR Diff，40 个风险样本、60 个干净样本；Validation 80、Holdout 20。

数据集 SHA-256：

```text
88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115
```

### 2.1 总体指标

| 系统 | TP | FP | FN | Precision | Recall | F1 | High-risk Recall | Critical Misses |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Single Agent | 25 | 5 | 15 | 83.33% | 62.50% | 71.43% | 84.21% | 0 |
| Legacy Multi-Agent | 33 | 7 | 7 | 82.50% | 82.50% | 82.50% | 94.74% | 0 |
| Current Six-Agent | 17 | 4 | 23 | 80.95% | 42.50% | 55.74% | 42.11% | 0 |
| Self-Evolved Six-Agent | 17 | 4 | 23 | 80.95% | 42.50% | 55.74% | 42.11% | 0 |

当前 Six-Agent 指标计算：

```text
Precision = TP / (TP + FP) = 17 / 21 = 80.95%
Recall    = TP / (TP + FN) = 17 / 40 = 42.50%
F1        = 2PR / (P + R) = 55.74%
HR Recall = 8 / 19 = 42.11%
Critical Misses = 0 / 4 = 0
```

### 2.2 Holdout 指标

| 指标 | Stable Holdout | Evolved Holdout |
|---|---:|---:|
| Precision | 100.00% | 100.00% |
| Recall | 12.50% | 12.50% |
| F1 | 22.22% | 22.22% |
| High-risk Recall | 0.00% | 0.00% |
| Critical Misses | 0 | 0 |

当前 Holdout 已被用于 FN 诊断。从本计划开始，它只能作为回归集，不再用于声明新的“盲测泛化”结果。

---

## 3. 根因分析

### 3.1 FN 总览

23 个 FN 的分类如下：

| 根因 | 数量 | 占 FN 比例 | 现象 |
|---|---:|---:|---|
| `NO_AGENT_ROUTED` | 18 | 78.26% | 只调用了 Reliability，没有调用应负责安全检测的 Security |
| `RULE_NOT_TRIGGERED` | 5 | 21.74% | Reliability 已运行，但稳定规则不覆盖该 CWE，Candidate 又没有接入 Reliability |

FN 严重度：

| 严重度 | 数量 |
|---|---:|
| High | 11 |
| Medium | 11 |
| Low | 1 |

Critical 漏报为 0，说明 Critical 路径仍有效；低分主要由 High 和 Medium 覆盖不足造成。

### 3.2 路由遗漏的 18 个 FN

| CWE | 数量 | 典型风险 |
|---|---:|---|
| CWE-798 | 4 | 硬编码凭据 |
| CWE-78 | 4 | OS 命令注入 |
| CWE-502 | 2 | 不安全反序列化 |
| CWE-330 | 1 | 弱随机数 |
| CWE-617 | 1 | 使用断言执行授权检查 |
| CWE-614 | 1 | Cookie 缺少 Secure 属性 |
| CWE-22 | 1 | 路径穿越 |
| CWE-328 | 1 | 弱哈希 |
| CWE-601 | 1 | 开放重定向 |
| CWE-377 | 1 | 不安全临时文件 |
| CWE-117 | 1 | 日志伪造 |

这些 case 全部只执行了：

```text
agents_called = ["reliability-agent"]
tools_called  = ["reliability-agent:2"]
```

直接原因有两处：

1. `evoagent/loop_agents/tools.py::profile_risk()` 只有少量关键字；
2. `SemanticPlanner` 重新从 `semantic_summary` 推导领域，却没有把 `risk_profile.agents` 作为强路由输入。

这会造成 RiskProfiler 即使给出 Specialist 建议，Planner 也可能丢失该建议；而路径穿越、反序列化、弱加密、Cookie、重定向、日志等风险又不在当前摘要器的安全信号集合中。

### 3.3 规则未触发的 5 个 FN

| CWE | 数量 | 所需能力 |
|---|---:|---|
| CWE-835 | 1 | 无界重试/循环检测 |
| CWE-682 | 1 | 金额浮点或错误数值计算检测 |
| CWE-367 | 1 | 时间/状态竞争与不安全时间处理检测 |
| CWE-400 | 1 | Async 阻塞或资源耗尽检测 |
| CWE-362 | 1 | 非原子共享状态写入检测 |

当前稳定 Specialist 的规则范围过窄：

```text
SecurityRuleReviewer: 4 条规则
ReliabilityRuleReviewer: 2 条规则
```

更关键的是，`build_evaluation_leader()` 只把 Candidate 放入 `security_reviewers`。`ExpertContext` 的 Reliability 侧仍固定实例化单个 `ReliabilityRuleReviewer()`，因此 Candidate 中已经存在的 CWE-362、367、400、682 等规则不会被 Reliability Agent 执行。

### 3.4 Evolved 与 Stable 完全相同

Candidate 已在 Validation 合成 12 条规则，但：

1. 安全 case 未路由到 Security 时，Candidate 没有执行机会；
2. Reliability case 虽路由正确，Candidate 却只挂在 Security 工具链；
3. Candidate 多为单条代码字面量，泛化能力较弱；
4. 当前 Gate 允许 Candidate F1 与 Stable 相等时通过，因此“无实际增益”仍会被标记为 PASS。

所以当前结果不是 Candidate 被 Verifier 拒绝，而是 Candidate 大部分时间根本没有进入正确执行路径。

### 3.5 FP 与重复发现

当前去重键主要是：

```text
(rule_id, path, line)
```

当规则扫描器和语义扫描器用不同 Rule ID 报告同一行的同一 CWE 时，两条 Finding 会同时保留。例如 `SEC-EVAL` 与 `SEM-TAINTED-EXEC` 可以表示同一个动态执行问题。应按漏洞族/CWE、位置和证据范围做受控归并，同时保留全部来源信息。

### 3.6 非根因

以下部分目前不是优先修复对象：

- Evaluation V2 已真实运行 `six-agent-v2`；
- execution success 为 100%；
- Decision Trace 和 Replay Snapshot 覆盖率为 100%；
- 诊断样本中 Verifier rejection rate 为 0；
- Critical 4/4 全部命中；
- 现有 12 项 Evaluation V2 CI Hard Gates 全部通过。

---

## 4. 修复原则

1. **先路由、再规则、后阈值。** Specialist 没运行时，扩规则没有效果。
2. **生产信号与评测标签隔离。** 路由器只读取 Diff、路径和代码结构，禁止读取 `case_id`、`expected_findings`、split 或 gold CWE。
3. **统一风险事实来源。** `profile_risk()`、`semantic_change_summary()`、Semantic Planner 和 Fallback Planner 共享同一套 Risk Signal Catalog。
4. **领域所有权明确。** Candidate Rule 必须标注 Security、Reliability 或 Shared。
5. **高风险宁可双路由。** Clean Accuracy 和成本由 Gate 约束，High-risk Recall 优先于少量工具调用开销。
6. **规则采用“候选检测 + 语义确认”。** 避免无限扩张正则导致 FP。
7. **去重不丢证据。** 合并展示，不删除 provenance、rule ids 和 scanner evidence。
8. **当前 Holdout 不再用于调参。** 最终泛化验收使用新的密封 Holdout。

---

## 5. 分阶段实施计划

## Phase 0：冻结基线与增加可观测性

### 修改范围

- `evoagent/evaluation_v2/diagnostics.py`
- `evoagent/evaluation_v2/report.py`
- `scripts/run_e2e_evaluation_v2.py`
- `tests/evaluation_v2/test_diagnostics.py`

### 工作项

- [ ] 保存当前报告、case results、FN analysis、Candidate Manifest 和 CI gates；
- [ ] 为每个 case 输出 `risk_signal_codes`；
- [ ] 输出 `risk_profile.agents`、`planner_requested_agents`、`called_agents` 三段数据；
- [ ] 增加 `route_drop_reason`，区分“未识别风险”“Planner 丢弃建议”“Agent 不可用”；
- [ ] 输出每个 Specialist 内部各 Scanner 的调用和 Finding 数；
- [ ] 输出 Candidate 按领域的调用次数；
- [ ] 输出 Verifier 输入数、批准数、拒绝数和原因；
- [ ] 保持现有 JSON 字段兼容，只新增字段。

### 验收

任意 FN 都能沿以下链路定位，不需要人工猜测：

```text
raw diff
→ signals
→ risk profile
→ planner decision
→ agent called
→ scanner invoked
→ candidate hit
→ verifier decision
→ matcher result
```

---

## Phase 1：建立统一 Risk Signal Catalog

### 建议新增文件

```text
evoagent/loop_agents/planning/risk_signals.py
```

### 建议数据结构

```python
RiskSignal(
    code="PROCESS_EXECUTION",
    domain="security",
    severity_floor="high",
    evidence=[...],
    confidence=0.9,
)
```

风险分析结果至少包含：

```python
{
    "level": "high",
    "domains": ["security", "reliability"],
    "agents": ["security-agent", "reliability-agent"],
    "signal_codes": ["PROCESS_EXECUTION", "EXTERNAL_INPUT"],
    "confidence": 0.9,
}
```

### Security 信号族

- [ ] 动态执行：`eval`、`exec`；
- [ ] 进程执行：`subprocess`、`os.system`、shell 参数；
- [ ] Credential：password、token、secret、api key 的字面量赋值；
- [ ] 反序列化：pickle、yaml unsafe load、通用 object load；
- [ ] 文件系统：用户输入参与 open/path/join、临时文件；
- [ ] 加密与随机：md5/sha1、安全 token 使用普通 random；
- [ ] Web 安全：Cookie 属性、redirect 目标、授权断言；
- [ ] 日志：外部输入直接进入换行敏感日志；
- [ ] SQL/数据库：拼接查询、动态 query；
- [ ] 外部输入到危险 sink 的组合信号。

### Reliability 信号族

- [ ] retry/while/for 的无界循环；
- [ ] async 函数中的阻塞调用；
- [ ] thread/lock/shared state；
- [ ] 非原子文件写；
- [ ] 时间、超时与状态检查；
- [ ] 金额/精度敏感浮点计算；
- [ ] 异常吞噬；
- [ ] 资源生命周期和未关闭资源。

### 修改现有代码

- [ ] `profile_risk()` 调用统一分类器；
- [ ] `semantic_change_summary()` 复用统一分类器，不再维护另一份不一致关键字；
- [ ] 保留原字段，新增结构化信号字段；
- [ ] 对所有信号记录可审计 rationale code，不保存模型思维链。

### 测试

- [ ] 每个信号至少一个正例、两个安全负例；
- [ ] 使用合成代码片段，不使用冻结 Benchmark 的 case id；
- [ ] 覆盖 Python、JavaScript/TypeScript 常见写法；
- [ ] 对注释、测试 fixture、字符串文档降低置信度或排除；
- [ ] 确认只改删除行不会触发新增漏洞。

---

## Phase 2：修复 Planner 与 Fallback 路由

### 修改范围

- `evoagent/loop_agents/planning/planner.py`
- `evoagent/loop_agents/planning/fallback.py`
- `evoagent/loop_agents/tools.py`
- `tests/test_risk_profiler.py`
- 新增 `tests/loop_agents/test_security_routing_matrix.py`

### 核心路由规则

```python
security_required = (
    "security-agent" in risk_profile["agents"]
    or "security" in risk_profile["domains"]
    or semantic_security_signal
)

reliability_required = (
    "reliability-agent" in risk_profile["agents"]
    or "reliability" in risk_profile["domains"]
    or semantic_reliability_signal
)

if risk_profile["level"] == "high":
    security_required = True
    reliability_required = True
```

### 工作项

- [ ] Planner 使用 RiskProfiler、Semantic Summary 的并集，不允许无解释地丢弃 Specialist 建议；
- [ ] `risk_profile.agents` 成为强输入，不再只是展示字段；
- [ ] 任意 Security 信号必须生成 `review.security` 节点；
- [ ] High 风险生成 Security + Reliability 双 Specialist 节点；
- [ ] Ambiguous High 风险按安全默认双路由；
- [ ] Clean/Test-only 仍只执行轻量 Reliability baseline；
- [ ] Fallback 与正常 Planner 使用同一个 `should_route_security()` 判定函数；
- [ ] Agent 不可用时明确记录 `AGENT_UNAVAILABLE`，不得静默降级为“无风险”；
- [ ] 保持 Critic、Verifier、Fix 的结果驱动动态图策略不变。

### 路由验收矩阵

| 风险族 | Security | Reliability |
|---|---:|---:|
| Credential、命令注入、路径、反序列化、弱加密、Cookie、Redirect | 必须 | High 时必须 |
| 无界重试、精度、时间、阻塞 Async、非原子写 | High 时必须 | 必须 |
| 同时有外部输入与资源/并发变化 | 必须 | 必须 |
| 纯测试或明确干净改动 | 否 | 是，轻量模式 |

### 阶段 Gate

- [ ] 当前 18 个 `NO_AGENT_ROUTED` 降为 0；
- [ ] 所有 High-risk case 至少执行一个正确领域 Specialist；
- [ ] 安全路由覆盖率 100%；
- [ ] Execution Success 100%；
- [ ] Clean Accuracy 不低于 98%；
- [ ] 平均 Tool Calls 增幅先记录，Phase 7 再做成本优化。

---

## Phase 3：按领域接入 Stable 与 Candidate Reviewer

### 修改范围

- `evoagent/loop_agents/tools.py::ExpertContext`
- `evoagent/evaluation_v2/adapters.py::build_evaluation_leader`
- `evoagent/skill_evolution.py`
- `evoagent/evaluation_v2/evolution_protocol.py`
- `tests/evaluation_v2/test_composed_security_skills.py`
- 新增 `tests/evaluation_v2/test_composed_reliability_skills.py`

### ExpertContext 改造

当前：

```text
security_reviewers = [stable security, candidate]
reliability        = stable reliability only
```

目标：

```text
security_reviewers    = [stable security, security candidate rules]
reliability_reviewers = [stable reliability, reliability candidate rules]
```

工作项：

- [ ] 将 `_reliability` 改为 `_reliability_reviewers` 列表；
- [ ] 增加 `reliability_reviewer_ids`；
- [ ] Security 和 Reliability 共用组合扫描、异常隔离、调用计数和去重逻辑；
- [ ] Candidate Artifact Rule 增加 `domain` 字段；
- [ ] 建立 CWE → domain 的集中映射，避免在 Adapter 中硬编码；
- [ ] `shared` 规则可进入两个 Specialist，但只计一次最终 Finding；
- [ ] Manifest 保存各领域规则数量和 SHA；
- [ ] Runtime Summary 分别显示 Candidate 在 Security、Reliability 的 invocation count。

### 领域归属原则

| Domain | CWE 示例 |
|---|---|
| Security | 22、78、89、95、117、328、330、377、502、601、614、617、798 |
| Reliability | 362、367、400、682、703、772、835 |
| Shared | 同时涉及可利用性与服务可用性的规则，必须显式标注，不靠默认猜测 |

### 阶段 Gate

- [ ] Reliability Agent 执行时能调用 Reliability Candidate；
- [ ] Security Agent 执行时能调用 Security Candidate；
- [ ] Stable/Evolved 运行配置除 Candidate 外完全一致；
- [ ] Candidate 未路由时 invocation 为 0，正确路由时 invocation 大于 0；
- [ ] 5 个 `RULE_NOT_TRIGGERED` 能进入对应候选扫描器；
- [ ] Candidate 异常不会阻断 Stable Reviewer。

---

## Phase 4：扩展稳定规则与语义确认能力

### 修改范围

- `evoagent/reviewer.py`
- `evoagent/semantic_reviewer.py`
- `evoagent/loop_agents/security.py`
- `evoagent/loop_agents/reliability.py`
- `evoagent/evaluation_harness.py::RULE_TO_CWE`
- `tests/test_reviewer.py` 或新增领域规则测试文件
- `tests/evaluation_v2/test_rule_cwe_mapping.py`

### Security 规则补齐

- [ ] `SEC-PATH-TRAVERSAL` / CWE-22；
- [ ] `SEC-YAML-LOAD`、`SEC-PICKLE-LOAD` / CWE-502；
- [ ] `SEC-WEAK-HASH` / CWE-328；
- [ ] `SEC-WEAK-RANDOM` / CWE-330；
- [ ] `SEC-INSECURE-TEMPFILE` / CWE-377；
- [ ] `SEC-ASSERT-AUTH` / CWE-617；
- [ ] `SEC-INSECURE-COOKIE` / CWE-614；
- [ ] `SEC-OPEN-REDIRECT` / CWE-601；
- [ ] `SEC-LOG-FORGING` / CWE-117。

### Reliability 规则补齐

- [ ] `REL-UNBOUNDED-RETRY` / CWE-835；
- [ ] `REL-FLOAT-MONEY` / CWE-682；
- [ ] `REL-NAIVE-DATETIME` / CWE-367；
- [ ] `REL-BLOCKING-ASYNC` / CWE-400；
- [ ] `REL-NONATOMIC-WRITE` / CWE-362。

### 实现约束

- [ ] 单行正则只负责产生候选 Finding；
- [ ] 需要上下文的风险由 AST/semantic scanner 确认；
- [ ] 路径穿越必须区分常量路径与外部输入路径；
- [ ] 弱哈希必须区分安全用途与密码/签名用途；
- [ ] 普通 random 必须区分模拟/测试与安全 token；
- [ ] open redirect 必须识别 allowlist 或同源校验；
- [ ] Cookie 规则检查 `secure`、`httponly`、`samesite`，但按具体 CWE 输出；
- [ ] Async 阻塞规则只在 async 上下文确认；
- [ ] 金额浮点规则要求金额/货币语境；
- [ ] 每条规则必须有 title、explanation、fix、test、severity、confidence 和 CWE 映射；
- [ ] 每条规则至少包含正例、近似负例、安全修复负例。

### 阶段 Gate

- [ ] 现有 5 个 `RULE_NOT_TRIGGERED` 降为 0；
- [ ] `test_rule_cwe_mapping.py` 保持全通过；
- [ ] 新增规则没有造成 Critical/High 误报；
- [ ] Precision 不低于 80%；
- [ ] Clean Accuracy 不低于 95%。

---

## Phase 5：Finding 归一化、去重与证据合并

### 修改范围

- `evoagent/loop_agents/tools.py`
- `evoagent/loop_agents/security.py`
- `evoagent/loop_agents/reliability.py`
- `evoagent/loop_agents/coordinator.py`
- 建议新增 `evoagent/finding_identity.py`
- 新增 `tests/loop_agents/test_cross_scanner_dedup.py`

### 目标身份模型

不再只使用 `(rule_id, path, line)`，改为：

```text
canonical identity =
(issue_family_or_cwe, normalized_path, overlapping_line_span, sink_or_evidence_fingerprint)
```

Finding 增加或计算：

```text
issue_family
cwe
primary_rule_id
source_rule_ids[]
scanner_sources[]
evidence_items[]
line_span
```

### 合并规则

- [ ] 同 CWE、同路径、同一行且危险 sink 相同：合并；
- [ ] 同漏洞族、位置相差不超过评分容差且证据重叠：合并；
- [ ] 不同 CWE 或不同 sink：不得仅因同行而合并；
- [ ] 合并后保留最高严重度、最高置信度和全部 provenance；
- [ ] Rule Scanner 与 Semantic Scanner 交叉确认时提高 evidence strength，不新增第二条 Finding；
- [ ] Verifier 接收 canonical Finding，并能看到所有来源证据。

### 阶段 Gate

- [ ] `SEC-EVAL` 与 `SEM-TAINTED-EXEC` 同位置只输出一个漏洞；
- [ ] 不同漏洞同一行仍能输出多条；
- [ ] 当前 4 个 FP 逐条复核，重复类 FP 清零；
- [ ] 去重不得降低 TP、High-risk Hits 或 Critical Hits。

---

## Phase 6：修复 Candidate 合成与 Evolution Gates

### 修改范围

- `evoagent/evaluation_v2/evolution_protocol.py`
- `evoagent/evaluation_v2/gates.py`
- `evoagent/skill_evolution.py`
- `tests/evaluation_v2/test_candidate_freeze.py`
- `tests/evaluation_v2/test_ci_hard_gates.py`

### Candidate 泛化

当前 Candidate 多为精确字面量，例如固定变量名、固定常量或固定函数参数。改为受约束的结构化候选：

- [ ] 从重复确认样本提取 AST/operator/call-shape；
- [ ] 变量名和常量参数化；
- [ ] 保留 sink、上下文和 domain 约束；
- [ ] 自动生成近似负例并执行反例测试；
- [ ] 单个样本只允许形成实验 Candidate，不直接进入 Active；
- [ ] 多任务独立证据满足 corroboration 后才能申请 Promote。

### Gate 修复

当前 `candidate_f1 >= stable_f1` 会让零增益 Candidate PASS。改为同时要求：

```text
Validation F1 improvement >= 2.0 pp
或 Validation High-risk Recall improvement >= 5.0 pp

并且：
Critical Misses 不增加
High-risk Recall 不回退
Precision 不低于 80%
Clean Accuracy 回退不超过 2.0 pp
Execution Success >= 99%
```

如果 Candidate 只有非回退而无增益，状态应为：

```text
NO_MATERIAL_IMPROVEMENT
```

不得标记为 Promote-ready。

### 阶段 Gate

- [ ] No-op Candidate 被拒绝或保留为实验态；
- [ ] Candidate 的独立 TP contribution 大于 0；
- [ ] Candidate 不重复 Stable Rule 的同一 canonical Finding；
- [ ] Candidate Manifest 包含 domain、来源 split、规则哈希和训练样本 lineage；
- [ ] Holdout 内容不进入 Candidate synthesis。

---

## Phase 7：评测治理与新盲测集

### 当前数据集处理

- [ ] 保持 `evaluation_data/pr_diff_100.jsonl` 和 SHA 不变；
- [ ] 将当前 Validation/Holdout 全部视为已知回归数据；
- [ ] 所有历史基线仍可复现；
- [ ] 报告中将当前 Holdout 标注为 `observed_regression_holdout`。

### 新 Holdout V3

- [ ] 从未用于规则设计的新仓库和新代码形态采样；
- [ ] 至少包含现有 CWE 的同义实现，而不是重复固定字符串；
- [ ] 包含安全修复负例、注释负例、测试 fixture 负例；
- [ ] CWE、严重度、路径和行号由独立标注流程冻结；
- [ ] 在 Candidate Freeze 后才允许解封运行；
- [ ] 保存独立 SHA、创建时间、标注版本和访问记录；
- [ ] V3 结果只运行一次用于最终报告，失败后不得继续在同一 V3 上调参并声称盲测。

### 新 CI Hard Gates

- [ ] Security route coverage；
- [ ] Reliability route coverage；
- [ ] High-risk dual-route coverage；
- [ ] Candidate domain invocation；
- [ ] Cross-scanner duplicate rate；
- [ ] No-op Candidate rejection；
- [ ] Risk signal provenance present；
- [ ] Current/Evolved config isolation；
- [ ] Dataset fingerprint and split isolation。

---

## Phase 8：性能、Canary 与回滚

路由正确后 Agent 和工具调用会增加，性能优化只能在 Recall 达标后进行。

### 工作项

- [ ] 记录 Security/ Reliability 单 Agent 和双 Agent 的 P50/P95；
- [ ] 记录每个 Risk Signal 带来的额外工具调用；
- [ ] 对 Clean PR 保持轻量 Reliability 路径；
- [ ] 对已由强规则确认且证据完整的 Finding，允许跳过非必要深挖工具，但不得跳过高风险 Verifier；
- [ ] Candidate 先 Shadow，再 5%/10%/25%/50%/100% Canary；
- [ ] 任意 Critical Miss、执行错误激增或 High-risk Recall 回退触发自动回滚；
- [ ] 回滚恢复 previous-good stable version，并保留完整 trace。

### 性能 Gate

| 指标 | Gate |
|---|---:|
| Execution Success | 100% |
| P95 Latency | 不超过修复前 Six-Agent 的 2 倍；超出需单独性能评审 |
| Clean PR 平均 Specialist 数 | 不超过 1.2 |
| Tool Denials | 0，或全部有可解释的安全策略原因 |
| Trace / Replay Coverage | 100% |

---

## 6. 测试与执行顺序

### 6.1 单元测试

```powershell
python -m pytest tests/test_risk_profiler.py -q
python -m pytest tests/evaluation_v2/test_composed_security_skills.py -q
python -m pytest tests/evaluation_v2/test_composed_reliability_skills.py -q
python -m pytest tests/evaluation_v2/test_rule_cwe_mapping.py -q
python -m pytest tests/loop_agents/test_security_routing_matrix.py -q
python -m pytest tests/loop_agents/test_cross_scanner_dedup.py -q
```

### 6.2 Evaluation V2 定向测试

```powershell
python -m pytest tests/evaluation_v2 -q
```

### 6.3 分阶段评测

```powershell
python scripts/run_e2e_evaluation_v2.py --stage smoke
python scripts/run_e2e_evaluation_v2.py --stage diagnostic
python scripts/run_e2e_evaluation_v2.py --stage current
python scripts/run_e2e_evaluation_v2.py --stage evolve
python scripts/run_e2e_evaluation_v2.py --stage holdout
python scripts/run_e2e_evaluation_v2.py --stage canary
python scripts/run_e2e_evaluation_v2.py --stage report
```

### 6.4 全量验收

```powershell
python scripts/run_e2e_evaluation_v2.py --stage all
python -m pytest -q
```

每个阶段都必须使用隔离的 SQLite evaluation store；正式结果不得复用污染状态。

---

## 7. 量化验收标准

### 7.1 最低发布 Gate

| 指标 | 当前 | 最低 Gate |
|---|---:|---:|
| Precision | 80.95% | ≥ 80.00% |
| Recall | 42.50% | ≥ 75.00% |
| F1 | 55.74% | ≥ 75.00% |
| High-risk Recall | 42.11% | ≥ 85.00% |
| Critical Misses | 0 | = 0 |
| Clean Accuracy | 100.00% | ≥ 95.00% |
| Execution Success | 100.00% | = 100.00% |
| `NO_AGENT_ROUTED` FN | 18 | = 0 |
| `RULE_NOT_TRIGGERED` FN | 5 | ≤ 2，最终目标 0 |

### 7.2 目标 Gate

目标是达到或接近 Legacy Multi-Agent，同时保留 Six-Agent 的治理、Trace、Replay 和可演化能力：

```text
Recall >= 80.50%
F1 >= 80.50%
High-risk Recall >= 93.00%
Critical Misses = 0
Precision >= 80.00%
Clean Accuracy >= 95.00%
```

### 7.3 Evolved 专属 Gate

Evolved 相对 Stable 必须满足：

```text
Validation F1 至少提升 2.0 pp，或 High-risk Recall 至少提升 5.0 pp
Critical Misses 不增加
Precision >= 80%
Clean Accuracy 回退 <= 2.0 pp
新 Holdout V3 不回退
Candidate 独立 TP contribution > 0
Candidate invocation 与领域路由证据完整
```

---

## 8. 建议提交拆分

1. `test(routing): lock security and reliability routing matrix`
2. `feat(risk): centralize semantic risk signal catalog`
3. `fix(planner): honor profiler agents and dual-route high risk diffs`
4. `fix(runtime): compose candidate reviewers by specialist domain`
5. `feat(rules): cover prioritized security and reliability CWE families`
6. `fix(findings): deduplicate cross-scanner findings by canonical issue identity`
7. `fix(evolution): reject no-op candidates and record domain lineage`
8. `test(evaluation): add route, invocation, dedup and blind-split hard gates`
9. `perf(runtime): bound clean-path tool cost after recall recovery`

每个提交必须可独立测试和回滚，不把路由、规则、评分器改动混在同一提交中。

---

## 9. 风险与回滚策略

| 风险 | 监控信号 | 缓解/回滚 |
|---|---|---|
| 双路由增加延迟 | P95、tool calls | 保留 Feature Flag，先保证 Recall，再优化 clean path |
| 新规则增加 FP | Precision、Clean Accuracy | 单规则开关；关闭问题规则，不回滚路由修复 |
| Candidate 跨领域重复运行 | invocation、duplicate rate | 规则 domain 过滤，Shared 规则 canonical dedup |
| 去重误合并不同漏洞 | TP、同一行多 CWE 测试 | 只合并相同 issue family/CWE 且证据重叠的 Finding |
| Planner 与 Fallback 再次漂移 | routing matrix | 共享同一 predicate，不复制规则 |
| Holdout 泄漏 | manifest lineage、访问记录 | 当前 Holdout 降级为回归集，使用新密封 V3 |
| 新 Candidate 无增益仍 Promote | contribution/gate result | 增加 material improvement Gate |

建议 Feature Flags：

```text
risk_signal_catalog_v2
planner_security_union_route
high_risk_dual_route
domain_composed_candidates
extended_domain_rules
canonical_finding_dedup
material_candidate_gate
```

回滚应按功能层分别执行，禁止为了处理单条误报而整体退回已验证的 Evaluation V2 接线修复。

---

## 10. Definition of Done

只有同时满足下列条件，才能宣布本轮低召回率修复完成：

- [ ] 23 个历史 FN 全部有修复后分类和证据；
- [ ] `NO_AGENT_ROUTED = 0`；
- [ ] `RULE_NOT_TRIGGERED <= 2`，目标为 0；
- [ ] Current 达到最低发布 Gate；
- [ ] Evolved 产生可证明的独立增益，不再以相等指标通过；
- [ ] High-risk Recall 达标且 Critical Misses 保持 0；
- [ ] 重复类 FP 被 canonical dedup 消除；
- [ ] Clean Accuracy 和 Precision 未越过回退阈值；
- [ ] Current/Evolved 仍真实使用同一 `six-agent-v2` Runtime；
- [ ] Candidate 是唯一实验变量；
- [ ] 全量 Evaluation V2、CI Hard Gates 和项目测试通过；
- [ ] 新 Holdout V3 在 Candidate Freeze 后完成一次性盲测；
- [ ] 报告、manifest、trace、replay、dataset SHA 和回滚记录齐全。

---

## 11. 预期修复路径

```text
当前 Recall 42.50%
    │
    ├─ Phase 1-2：修复 18/23 个 FN 的路由前提
    │              └─ Security Candidate 与稳定规则获得执行机会
    │
    ├─ Phase 3：Candidate 按领域接入 Reliability
    │              └─ 已存在的 Reliability Candidate 不再被旁路
    │
    ├─ Phase 4：补足仍未触发的规则与语义确认
    │              └─ Recall / High-risk Recall 上升
    │
    ├─ Phase 5：跨扫描器 canonical dedup
    │              └─ FP 下降，Precision / F1 上升
    │
    └─ Phase 6-8：真实增益 Gate、新盲测、Canary 与性能收敛
```

本轮最优先的代码改动不是继续增加 Candidate 字面量规则，而是：

```text
1. Planner 尊重 risk_profile.agents；
2. High-risk 双路由；
3. Candidate 按 Security / Reliability 领域分别接入。
```

这三项完成后，再根据新的 FN 分析补规则，才能避免“规则存在但永远没有执行机会”的重复问题。
