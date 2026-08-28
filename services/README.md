# EvoReview A2A Remote Services

Standalone Specialist Agents speaking **HTTP + JSON-RPC 2.0** A2A.

## Services

| Service           | Port | Agent ID          | Reviewer                        |
|-------------------|------|-------------------|---------------------------------|
| security_agent    | 8001 | `security-agent`  | `SecurityRuleReviewer`          |
| reliability_agent | 8002 | `reliability-agent`| `ReliabilityRuleReviewer`       |

## Endpoints (identical contract for every agent)

```
GET  /health                -> {"status": "healthy", "agent_id": "..."}
GET  /a2a/agent-card        -> AgentCard JSON (capability discovery)
POST /a2a                   -> JSON-RPC 2.0 (task.submit / task.get / task.cancel / artifact.list)
```

## JSON-RPC methods

```
agent.discover   -> AgentCard
task.submit      {task:{task_id,assignment_id,sender,recipient,task_type,input:{diff},context}}
task.get         {task_id}
task.cancel      {task_id}
artifact.list    {task_id}
```

All methods accept an optional `token` in `params`; when
`EVOAGENT_A2A_TOKEN` is set the Coordinator must send the same token.

## Run locally

```bash
pip install fastapi uvicorn
uvicorn services.security_agent.app:app --host 0.0.0.0 --port 8001
uvicorn services.reliability_agent.app:app --host 0.0.0.0 --port 8002
```

## Run the full Remote stack

```bash
docker compose -f docker-compose.a2a.yml up --build
```

The Coordinator is then bootstrapped with
`EVOAGENT_A2A_ENDPOINTS=http://security-agent:8001/a2a,http://reliability-agent:8002/a2a`.