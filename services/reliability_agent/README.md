# Reliability Agent (A2A)

Standalone **Reliability Review Agent** implementing the EvoReview A2A protocol over HTTP + JSON-RPC.

Wraps the deterministic [`ReliabilityRuleReviewer`](../../evoagent/reviewer.py) (REL-* rules).

## Endpoints

- `GET /health` — liveness + `agent_id`
- `GET /a2a/agent-card` — capability discovery (`AgentCard`)
- `POST /a2a` — JSON-RPC 2.0 task lifecycle

## RPC methods

`agent.discover`, `task.submit`, `task.get`, `task.cancel`, `artifact.list`.

## Config

- `EVOAGENT_A2A_TOKEN` — required shared service token (empty disables auth)
- `EVOAGENT_A2A_DELAY_SECONDS` — optional artificial latency (failure injection: slow-agent)
- `EVOAGENT_A2A_ENDPOINT` — the endpoint advertised in the `AgentCard`

## Run

```bash
uvicorn services.reliability_agent.app:app --host 0.0.0.0 --port 8002
```