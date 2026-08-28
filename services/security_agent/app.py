"""Security Agent FastAPI entrypoint.

Run: ``uvicorn services.security_agent.app:app --host 0.0.0.0 --port 8001``
"""
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .service import build_host

app = FastAPI(title="EvoReview Security Agent (A2A)", version="1.0.0")
_host = build_host()


@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "agent_id": _host.card.agent_id}


@app.get("/a2a/agent-card")
def agent_card() -> dict:
    return _host.card.to_dict()


@app.post("/a2a")
async def a2a(request: Request) -> Response:
    body = await request.body()
    return JSONResponse(content=_host.handle(body))