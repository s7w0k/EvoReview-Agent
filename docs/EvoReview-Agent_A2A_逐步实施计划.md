# EvoReview-Agent：A2A 通信能力逐步实施计划

## 1. 目标与总体原则

### 1.1 当前现状

EvoReview-Agent 当前已经具备较完整的进程内 Multi-Agent 协作能力：

- `MultiAgentCoordinator` 负责 Planner、Specialist、Critic、Reflection、Evidence、Verifier、Arbiter 的编排；
- `CollaborationBus` 提供 task-scoped 的内部消息总线；
- `AgentMessage` 已包含 `sender / recipient / kind / content / correlation_id`；
- 已支持 Assignment、Peer Challenge、Revision、Evidence、Verification、Retry、Handoff；
- Agent Runtime 已具备超时、重试、预算、Tool Governance、Trace、Replay 等 Harness 能力；
- 当前 Specialist 主要仍运行在同一 Python 进程中。

因此，本轮改造的目标不是重写 Multi-Agent，而是：

> 在保留现有进程内 CollaborationBus 的基础上，增加一层标准化 Remote A2A Transport，使 Specialist Agent 可以独立部署，并通过 HTTP/JSON-RPC 进行任务委派、状态查询、结果返回和能力发现。

## 2. 目标架构

```text
                    ┌─────────────────────────────┐
                    │       ReviewService         │
                    │                             │
PR Diff ──────────> │ Planner / Coordinator       │
                    │         │                   │
                    │         ▼                   │
                    │     A2A Gateway             │
                    └─────────┬───────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
           HTTP/JSON-RPC   HTTP/JSON-RPC   HTTP/JSON-RPC
              │               │               │
              ▼               ▼               ▼
      Security Agent   Reliability Agent   Future Agent
        Service             Service          Service
              │               │
              └────── Artifact / Result ─────┘

进程内：Planner / Critic / Verifier / Arbiter → CollaborationBus
跨进程：Coordinator / Remote Specialist → A2A Gateway + HTTP/JSON-RPC
```

推荐采用：

- **进程内协作**：继续使用现有 `CollaborationBus`
- **跨进程同步调用**：HTTP + JSON-RPC 2.0
- **流式事件（可选）**：SSE
- **未来异步任务/高并发扩展**：NATS / RabbitMQ / Kafka
- **A2A 语义层**：自定义 `AgentCard / Task / Message / Artifact / TaskStatus`

第一阶段不建议直接引入消息队列，优先把协议边界和远程 Agent 生命周期做扎实。

# 3. Phase 0：建立基线与边界

## 3.1 首批远程化 Agent

第一批只拆：

- `SecurityRuleReviewer`
- `ReliabilityRuleReviewer`

暂时保留在 Coordinator 进程内：

- Planner
- Critic
- Reflection
- Evidence
- Verifier
- Arbiter

原因：Specialist 天然是能力提供者，最适合先抽成 Remote Agent；而 Planner、Critic、Verifier 等当前强依赖内部状态与 CollaborationBus，过早拆分会显著增加复杂度。

## 3.2 建立改造前基线

记录当前：

- Evaluation V2 F1
- High-risk Recall
- Clean Accuracy
- Execution Success
- P95 latency
- collaboration messages
- retries / handoffs
- trace coverage
- replay coverage

要求：A2A 改造后检测结果不得因为 Transport 改造而发生无解释变化。

### 验收标准

- 保存当前 Evaluation V2 报告；
- 记录当前 commit SHA；
- CI 全绿；
- 建立 `A2A_BASELINE.md` 或 JSON snapshot。

# 4. Phase 1：抽象统一 A2A Domain Model

不要一开始直接写 HTTP 接口，先把协议对象从业务对象中抽离。

建议新增：

```text
evoagent/a2a/
├── __init__.py
├── models.py
├── protocol.py
└── errors.py
```

## 4.1 AgentCard

```python
@dataclass
class AgentCard:
    agent_id: str
    name: str
    version: str
    endpoint: str
    capabilities: list[str]
    domains: list[str]
    supported_task_types: list[str]
    protocol_version: str
```

示例：

```json
{
  "agent_id": "security-agent",
  "name": "Security Review Agent",
  "version": "1.0.0",
  "endpoint": "http://security-agent:8001/a2a",
  "capabilities": ["code-review", "security-review"],
  "domains": ["security"],
  "supported_task_types": ["review-assignment"],
  "protocol_version": "v1"
}
```

## 4.2 A2ATask

```python
@dataclass
class A2ATask:
    task_id: str
    assignment_id: str
    sender: str
    recipient: str
    task_type: str
    input: dict
    context: dict
    correlation_id: str
    created_at: str
```

## 4.3 TaskStatus

统一定义：

```text
PENDING
RUNNING
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

## 4.4 A2AArtifact

```python
@dataclass
class A2AArtifact:
    artifact_id: str
    task_id: str
    artifact_type: str
    producer: str
    content: dict
    metadata: dict
```

## 4.5 A2AMessage

```python
@dataclass
class A2AMessage:
    message_id: str
    task_id: str
    sender: str
    recipient: str
    message_type: str
    payload: dict
    correlation_id: str
    timestamp: str
```

建议不要直接把当前 `AgentMessage` 暴露成网络协议，而是写 `AgentMessage ↔ A2AMessage` Adapter。

### 验收标准

- Domain Model 不依赖 FastAPI；
- 可 JSON serialize / deserialize；
- 所有对象有 schema validation；
- `AgentMessage ↔ A2AMessage` 可转换；
- 单元测试覆盖非法字段、版本兼容与序列化。

# 5. Phase 2：定义 A2A Transport Interface

新增：

```text
evoagent/a2a/transport.py
```

```python
class A2ATransport(Protocol):
    def discover(self, endpoint: str) -> AgentCard: ...
    def submit_task(self, card: AgentCard, task: A2ATask) -> str: ...
    def get_task(self, card: AgentCard, task_id: str) -> dict: ...
    def cancel_task(self, card: AgentCard, task_id: str) -> None: ...
    def get_artifacts(self, card: AgentCard, task_id: str) -> list[A2AArtifact]: ...
```

实现两个 Transport：

```text
InProcessA2ATransport
HttpJsonRpcA2ATransport
```

## 5.1 InProcessA2ATransport

目的：

- 让现有 Reviewer 先适配统一 A2A Interface；
- 不经过网络；
- 作为 fallback；
- 方便验证 Transport 与业务逻辑解耦。

## 5.2 HttpJsonRpcA2ATransport

建议方法：

```text
agent.discover
task.submit
task.get
task.cancel
artifact.list
```

JSON-RPC 请求示例：

```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "method": "task.submit",
  "params": {
    "task": {
      "task_id": "review-001",
      "assignment_id": "A01"
    }
  }
}
```

### 验收标准

同一个 `A2ATask` 通过 InProcess Transport 与 HTTP Transport 得到相同结构的 `A2AArtifact`。

# 6. Phase 3：增加 Agent Registry 与 Capability Discovery

新增：

```text
evoagent/a2a/registry.py
```

当前 `_enabled_agents()` 主要根据 Reviewer 名称筛选。升级后改成：

```text
Planner → AgentRegistry → 按 capability / domain / health / version 选择 Agent
```

Registry 至少维护：

```text
agent_id
endpoint
version
capabilities
domains
health_status
protocol_version
deployment
last_seen
```

Planner 可输出：

```json
{
  "required_domains": ["security"],
  "required_capabilities": ["code-review"]
}
```

Remote Agent 提供：

```text
GET /a2a/agent-card
```

或 JSON-RPC：

```text
agent.discover
```

### 验收标准

- Coordinator 不再硬编码 Remote Agent endpoint；
- Agent Registry 能从 AgentCard 注册；
- Planner 可根据 domain 选择合适 Agent；
- Agent 不健康时不参与路由。

# 7. Phase 4：将 Security Agent 拆为首个 Remote Service

新增：

```text
services/security_agent/
├── app.py
├── service.py
├── Dockerfile
└── README.md
```

推荐使用 FastAPI。

## 7.1 最小 Endpoint

```text
GET  /health
GET  /a2a/agent-card
POST /a2a
```

`POST /a2a` 负责 JSON-RPC。

## 7.2 Remote Agent 内部执行流程

```text
HTTP Request
   ↓
JSON-RPC Validation
   ↓
A2ATask
   ↓
Security Reviewer
   ↓
Finding[]
   ↓
A2AArtifact
   ↓
TaskStatus = COMPLETED
```

不要把 `Finding` 直接作为网络协议对象，应通过 Adapter 转成 `A2AArtifact`。

## 7.3 Coordinator Adapter

新增：

```text
RemoteReviewerAdapter
```

实现现有 `Reviewer` interface：

```python
class RemoteReviewerAdapter(Reviewer):
    def review(...):
        task = build_a2a_task(...)
        transport.submit_task(...)
        artifact = wait_for_result(...)
        return artifact_to_findings(artifact)
```

这样 `MultiAgentCoordinator` 第一阶段无需大改。

### 验收标准

`SecurityRuleReviewer(local)` 与 `RemoteReviewerAdapter(security-agent)` 输出一致。

# 8. Phase 5：再拆 Reliability Agent

Security Agent 跑通后，再按同样模式拆 Reliability Agent。

此时：

```text
Coordinator
   ↓
Agent Registry
   ↓
┌─────────────────────────┐
│ Security Agent (HTTP)    │
│ Reliability Agent (HTTP) │
└─────────────────────────┘
```

### 验收标准

- Security / Reliability 均可独立启动；
- Coordinator 可并发请求；
- 一个 Remote Agent 宕机时另一个正常完成；
- failure 可进入现有 Planner.replan() / fallback 路径。

# 9. Phase 6：将远程 A2A 接入现有 CollaborationBus

不要替换 CollaborationBus，而是让远程事件进入现有 Trace。

建议映射：

```text
A2A task.submit       → remote_task_submitted
A2A task.running      → remote_task_running
A2A artifact received → remote_artifact_received
Remote failure        → remote_agent_failure
Remote timeout        → remote_agent_timeout
```

继续复用：

```text
task_id
assignment_id
finding_key
```

建议增加：

```text
message_id
trace_id
span_id
```

形成：

```text
Review Task
 └── Assignment
      └── Remote Task
           └── A2A Message
                └── Artifact
```

### 验收标准

一个 Remote Security Agent 请求可以从 `ReviewService → Planner → Assignment → HTTP → Remote Agent → Artifact → Critic → Verifier → Arbiter` 完整追踪。

# 10. Phase 7：超时、重试、熔断与 Fallback

新增错误分类：

```text
A2AConnectionError
A2ATimeoutError
A2AProtocolError
A2ASchemaError
A2ARemoteExecutionError
A2AUnauthorizedError
A2ACircuitOpenError
```

## 10.1 Retry

只重试：

```text
connection reset
temporary unavailable
5xx
timeout
```

不要自动重试：

```text
schema invalid
permission denied
unsupported protocol version
malformed task
```

## 10.2 Circuit Breaker

```text
CLOSED
 ↓ failures exceed threshold
OPEN
 ↓ cooldown
HALF_OPEN
 ↓ success
CLOSED
```

## 10.3 Fallback

推荐：

```text
Remote Security Agent
       ↓ failure
备用 Remote Security Agent
       ↓ failure
Local Security Reviewer
```

### 验收标准

自动化测试覆盖：

- Agent 500；
- Agent timeout；
- Agent malformed response；
- endpoint unreachable；
- retry exhausted；
- circuit open；
- local fallback。

# 11. Phase 8：认证、授权与安全治理

Remote A2A 第一版推荐 Service Token / API Key，后续可升级 mTLS。

每个 Agent 具备：

```text
agent_id
service_identity
allowed_capabilities
tenant_scope
```

调用前：

```text
ExecutionPolicy
  ↓
允许调用该 Agent？
  ↓
允许该 capability？
  ↓
允许访问该 repository / tenant？
```

Remote Artifact 进入主 Harness 前执行：

```text
Schema Validation
Content Validation
Finding Validation
Observation Sanitization
```

### 验收标准

- 跨租户 Agent Task 不可访问；
- 伪造 Agent ID 被拒绝；
- 非法 Artifact 不进入 Verifier。

# 12. Phase 9：Task Lifecycle 与异步模式

初版可以同步 `submit → wait → result`，但协议层从一开始保留 Task Lifecycle：

```text
PENDING
RUNNING
COMPLETED
FAILED
CANCELLED
TIMED_OUT
```

接口：

```text
task.submit
task.get
task.cancel
artifact.list
```

未来如 Agent 执行时间变长，可增加 SSE：

```text
POST task.submit → task_id
GET /events/{task_id} → progress / tool-call / partial-artifact
```

只有出现大量异步任务、水平扩容、durable delivery 等需求时，再引入 NATS / RabbitMQ / Kafka。

# 13. Phase 10：A2A Observability

新增 Metrics：

```text
a2a_requests_total
a2a_request_latency_seconds
a2a_request_failures_total
a2a_timeouts_total
a2a_retries_total
a2a_fallback_total
a2a_circuit_open_total
a2a_artifacts_total
```

Labels：

```text
source_agent
target_agent
task_type
status
protocol_version
```

Trace：

```text
Review Trace
 └── Planner Span
 └── Remote Assignment Span
      └── HTTP Client Span
           └── Remote Agent Server Span
                └── Review Execution Span
```

如果已有 OpenTelemetry，直接把 A2A Client/Server span 接入。

# 14. Phase 11：Evaluation Harness V3

比较：

```text
A. Local Multi-Agent
B. Remote A2A Multi-Agent
```

在相同 frozen dataset 上执行。

## 14.1 Functional Equivalence

比较：

```text
Precision
Recall
F1
High-risk Recall
Clean Accuracy
Critical Misses
```

原则上与 Local 模式保持一致。

## 14.2 Runtime Metrics

新增：

```text
A2A request success
Remote task success
Remote timeout rate
Remote retry rate
Fallback rate
P50/P95/P99 A2A latency
End-to-end P95 latency
Trace coverage
Artifact schema validity
```

## 14.3 Failure Injection

新增：

```text
security-agent-down
security-agent-timeout
security-agent-500
malformed-artifact
protocol-version-mismatch
slow-agent
duplicate-response
```

验证 Retry、Replan、Fallback、Circuit Breaker、Task Cancel、Trace。

推荐报告表：

| Metric | Local | Remote A2A |
|---|---:|---:|
| F1 | x | x |
| High-risk Recall | x | x |
| Execution Success | x | x |
| P95 Latency | x | x |
| Remote Task Success | — | x |
| A2A Retry Rate | — | x |
| Fallback Success | — | x |
| Trace Coverage | x | x |

# 15. Phase 12：CI 与部署

新增 CI Job：

```text
a2a-unit-tests
a2a-contract-tests
a2a-integration-tests
a2a-failure-injection
evaluation-v3-regression
```

提供：

```text
docker-compose.a2a.yml
```

启动：

```text
review-api
security-agent
reliability-agent
redis/postgres
```

CI Gate 至少要求：

```text
Remote A2A execution success = 100%
Functional metrics no regression
Fallback test PASS
Trace coverage = 100%
Protocol contract PASS
```

# 16. 推荐目录结构

```text
evoagent/
├── a2a/
│   ├── __init__.py
│   ├── models.py
│   ├── protocol.py
│   ├── registry.py
│   ├── transport.py
│   ├── http_transport.py
│   ├── inprocess_transport.py
│   ├── adapters.py
│   ├── errors.py
│   └── telemetry.py
│
├── agents.py
├── runtime.py
├── harness.py
└── ...

services/
├── security_agent/
│   ├── app.py
│   ├── service.py
│   └── Dockerfile
│
└── reliability_agent/
    ├── app.py
    ├── service.py
    └── Dockerfile

tests/
└── a2a/
    ├── test_models.py
    ├── test_protocol.py
    ├── test_registry.py
    ├── test_http_transport.py
    ├── test_remote_reviewer.py
    ├── test_timeout.py
    ├── test_retry.py
    ├── test_fallback.py
    └── test_contract.py
```

# 17. 实施顺序总结

严格按以下顺序推进：

1. **P0：冻结当前基线** —— Evaluation V2 / CI / commit SHA
2. **P1：A2A Domain Model** —— AgentCard / Task / Message / Artifact / TaskStatus
3. **P2：Transport Interface** —— InProcess / HTTP JSON-RPC
4. **P3：Agent Registry** —— Discovery / Capability Matching / Health
5. **P4：Security Agent Remote 化** —— FastAPI / JSON-RPC / RemoteReviewerAdapter
6. **P5：Reliability Agent Remote 化** —— 并行 Remote Specialists
7. **P6：CollaborationBus 集成** —— Remote events / correlation / durable trace
8. **P7：Resilience** —— Timeout / Retry / Circuit Breaker / Fallback / Replan
9. **P8：Security** —— Auth / Authorization / Tenant isolation / Sanitization
10. **P9：Task Lifecycle** —— submit / get / cancel / artifact
11. **P10：Observability** —— Metrics / Trace / A2A spans
12. **P11：Evaluation Harness V3** —— Local vs Remote / Failure Injection
13. **P12：CI / Docker Compose** —— Contract / Integration / Regression Gate

# 18. 第一阶段不建议做的内容

当前不要同时加入：

- Kafka；
- 官方 A2A Protocol 的完整兼容；
- 所有 Agent 微服务化；
- Service Mesh；
- Kubernetes；
- 动态 Agent Spawn；
- LLM Planner 动态 DAG；
- Self-Evolution 与 A2A 同时大改。

建议先完成：

> **HTTP/JSON-RPC Remote Specialist + AgentCard + Task Lifecycle + Registry + Retry/Fallback + Trace**

这已经足以形成一个完整、可解释、可评测的 A2A 工程闭环。

# 19. 完成后的架构定位

完成后，EvoReview-Agent 可以从：

> 单进程 Multi-Agent + Internal CollaborationBus

升级为：

> **Hybrid Multi-Agent Runtime：进程内 Event Bus + 跨进程 HTTP/JSON-RPC A2A + Capability Discovery + Durable Task Lifecycle + Governed Remote Agent Execution**

核心结构：

```text
Planner
  ↓
Capability-based Agent Routing
  ↓
A2A Gateway
  ↓
Remote Specialist Agents
  ↓
Structured Artifact
  ↓
Critic / Evidence / Verification
  ↓
Arbiter

外围：
Policy / Budget / Retry / Circuit Breaker / Fallback / Trace / Replay / Tenant Isolation
```

# 20. Definition of Done

只有满足以下条件，才认为 A2A 第一版真正完成：

- [ ] Security / Reliability Agent 可独立进程运行；
- [ ] Coordinator 不直接依赖 Remote Agent Python 对象；
- [ ] AgentCard 支持能力发现；
- [ ] Task / Message / Artifact 有稳定 schema；
- [ ] Remote Task 有完整 lifecycle；
- [ ] HTTP/JSON-RPC Transport 可替换 InProcess Transport；
- [ ] Remote Agent failure 可 Retry；
- [ ] Retry 失败可触发 Fallback / Replan；
- [ ] 所有 A2A 事件进入 Decision Trace；
- [ ] Tenant / Policy 权限在调用前验证；
- [ ] Remote Artifact 在进入 Harness 前校验；
- [ ] Local / Remote Evaluation 指标无功能性回归；
- [ ] Failure Injection 全部通过；
- [ ] CI 自动执行 Contract + Integration + Regression Test；
- [ ] Docker Compose 可一键启动完整 Remote Multi-Agent 环境。

达到这一阶段后，再进入下一轮：

> **Dynamic Planner + Runtime TaskGraph + Result-driven Replanning**

此时 A2A 将不再只是“远程调用 Agent”，而会真正成为动态 Multi-Agent 编排的通信底座。
