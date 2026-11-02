"""API HTTP: POST /chat, GET /health, GET /metrics."""
from __future__ import annotations

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, Field

from app.agents.quality import evaluate_safely
from app.observability.logging import setup_logging
from app.observability.store import metrics
from app.service import handle_message

setup_logging()
app = FastAPI(title="Getnet Multi-Agent Support", version="1.0.0")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_id: str = Field(min_length=1, max_length=64)


class RouteInfo(BaseModel):
    triage: str
    agents: list[str]
    theme: str | None


class ChatResponse(BaseModel):
    response: str
    route: RouteInfo
    sources: list[str]
    tools_used: list[str]
    conversation_closed: bool
    trace_id: str


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, background: BackgroundTasks) -> ChatResponse:
    result = handle_message(req.message, req.user_id)
    if not result.error:
        # Qualidade assíncrona: roda depois que a resposta é enviada
        background.add_task(evaluate_safely, result.trace_id, result.masked_message, result.response, result.sources)
    return ChatResponse(
        response=result.response,
        route=RouteInfo(triage=result.triage, agents=result.agents, theme=result.theme),
        sources=result.sources,
        tools_used=result.tools_used,
        conversation_closed=result.conversation_closed,
        trace_id=result.trace_id,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def get_metrics() -> dict:
    return metrics()
