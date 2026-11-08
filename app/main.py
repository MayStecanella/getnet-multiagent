"""API HTTP e telas.

Endpoints do desafio: POST /chat, GET /health, GET /metrics.
Telas: /cliente, /especialista, /qualidade (HTML estático em app/web/static).
APIs das telas: /api/...

Com ACCESS_CODE definido, toda chamada (menos /health, /api/config e as páginas) exige o
cabeçalho X-Access-Code.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.observability.logging import setup_logging
from app.web import handlers
from app.web.handlers import ApiError

setup_logging()
STATIC = Path(__file__).resolve().parent / "web" / "static"
app = FastAPI(title="Getnet Multi-Agent Support", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def require_access(x_access_code: str | None = Header(default=None)) -> None:
    try:
        handlers.check_access(x_access_code)
    except ApiError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


def run(background: BackgroundTasks | None, fn, *args) -> Any:
    try:
        out = fn(*args)
    except ApiError as exc:
        raise HTTPException(exc.status, exc.detail) from exc
    body, tasks = out if isinstance(out, tuple) else (out, [])
    for task, task_args, task_kwargs in tasks:
        background.add_task(task, *task_args, **task_kwargs)
    return body


# --- contrato do desafio ------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_id: str = Field(min_length=1, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    channel: str | None = Field(default=None, max_length=10)


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
    conversation_id: str
    status: str
    specialist: str | None
    handoff: bool
    delivered_to_human: bool
    awaiting_feedback: bool
    feedback: str | None


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_access)])
def chat(req: ChatRequest, request: Request, background: BackgroundTasks) -> dict:
    return run(background, handlers.chat, req.model_dump(), client_ip(request))


@app.get("/health")
def health() -> dict:
    return handlers.health()


@app.get("/metrics", dependencies=[Depends(require_access)])
def get_metrics() -> dict:
    return handlers.quality_metrics()


# --- telas ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def page_home() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/cliente", include_in_schema=False)
def page_client() -> FileResponse:
    return FileResponse(STATIC / "cliente.html")


@app.get("/especialista", include_in_schema=False)
def page_agent() -> FileResponse:
    return FileResponse(STATIC / "especialista.html")


@app.get("/qualidade", include_in_schema=False)
def page_quality() -> FileResponse:
    return FileResponse(STATIC / "qualidade.html")


# --- APIs das telas ---------------------------------------------------------------------
@app.get("/api/config")
def api_config() -> dict:
    return handlers.public_config()


@app.get("/api/auth/check", dependencies=[Depends(require_access)])
def api_auth_check() -> dict:
    return {"ok": True}


@app.get("/api/conversations/{conversation_id}/messages", dependencies=[Depends(require_access)])
def api_client_messages(conversation_id: str, user_id: str, after: int = 0) -> dict:
    return run(None, handlers.client_messages, conversation_id, user_id, after)


class AgentPayload(BaseModel):
    agent_name: str = Field(min_length=1, max_length=60)
    text: str | None = Field(default=None, max_length=4000)


@app.get("/api/agent/queue", dependencies=[Depends(require_access)])
def api_agent_queue() -> dict:
    return handlers.agent_queue()


@app.get("/api/agent/conversations/{conversation_id}", dependencies=[Depends(require_access)])
def api_agent_conversation(conversation_id: str) -> dict:
    return run(None, handlers.agent_conversation, conversation_id)


@app.post("/api/agent/conversations/{conversation_id}/claim", dependencies=[Depends(require_access)])
def api_agent_claim(conversation_id: str, payload: AgentPayload) -> dict:
    return run(None, handlers.agent_claim, conversation_id, payload.model_dump())


@app.post("/api/agent/conversations/{conversation_id}/messages", dependencies=[Depends(require_access)])
def api_agent_send(conversation_id: str, payload: AgentPayload, background: BackgroundTasks) -> dict:
    return run(background, handlers.agent_send, conversation_id, payload.model_dump())


@app.post("/api/agent/conversations/{conversation_id}/close", dependencies=[Depends(require_access)])
def api_agent_close(conversation_id: str, payload: AgentPayload) -> dict:
    return run(None, handlers.agent_close, conversation_id, payload.model_dump())


@app.get("/api/quality/metrics", dependencies=[Depends(require_access)])
def api_quality_metrics() -> dict:
    return handlers.quality_metrics()


@app.get("/api/quality/evaluations", dependencies=[Depends(require_access)])
def api_quality_evaluations(author: str | None = None, below: int | None = None, limit: int = 50) -> dict:
    return run(None, handlers.quality_evaluations, author, below, limit)


class BatchPayload(BaseModel):
    csv: str = Field(min_length=1, max_length=500_000)
    name: str | None = Field(default=None, max_length=120)
    sample: int | None = Field(default=None, ge=1, le=100)  # sorteia N perguntas do arquivo


@app.post("/api/quality/batch", dependencies=[Depends(require_access)])
def api_batch_start(payload: BatchPayload, request: Request, background: BackgroundTasks) -> dict:
    return run(background, handlers.batch_start, payload.model_dump(), client_ip(request))


@app.get("/api/quality/batch", dependencies=[Depends(require_access)])
def api_batch_list() -> dict:
    return handlers.batch_list()


@app.get("/api/quality/batch/{batch_id}", dependencies=[Depends(require_access)])
def api_batch_get(batch_id: str) -> dict:
    return run(None, handlers.batch_get, batch_id)
