"""HTTP API for the frontend.

    uvicorn api:app --reload --port 8000

    POST /sessions                 {"text": brain dump}   -> session
    GET  /sessions/{id}                                   -> session
    POST /sessions/{id}/messages   {"text": message}      -> session   ("go" skips open questions)
    GET  /sessions/{id}/stream                            -> SSE: "session" events on every change

A session response is {"id", "status", "summary", "error", "plan"} where status is
"needs_answers" (limits missing), "negotiating" (agents working), or "idle".
Sessions live in memory: restarting the server clears them.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from intake import ask, extract, ready_to_negotiate, skip_open_questions, update_from_user
from negotiation import negotiate
from schemas import Plan
from transport import default_transport
from evidence.interfaces import router as evidence_router

app = FastAPI(title="Untangle API")
app.include_router(evidence_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

TRANSPORT = default_transport()


@dataclass
class Session:
    plan: Plan
    status: str = "idle"
    summary: str = ""
    error: Optional[str] = None
    listeners: list[asyncio.Queue] = field(default_factory=list)
    task: Optional[asyncio.Task] = None

    def view(self) -> dict:
        return {
            "id": self.plan.session_id,
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "transport": TRANSPORT.name,
            "plan": self.plan.model_dump(mode="json"),
        }

    def publish(self) -> None:
        data = self.view()
        for queue in self.listeners:
            queue.put_nowait(data)


SESSIONS: dict[str, Session] = {}


class TextIn(BaseModel):
    text: str


def _get(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


def _maybe_negotiate(session: Session, needed: bool) -> None:
    """Start a background negotiation when the plan is ready and something changed."""
    if not ready_to_negotiate(session.plan):
        session.status = "needs_answers"
        return
    if not needed:
        session.status = "idle"
        return
    session.status = "negotiating"
    session.task = asyncio.create_task(_run_negotiation(session))


async def _run_negotiation(session: Session) -> None:
    async def on_update(plan: Plan) -> None:
        session.plan = plan
        session.publish()

    try:
        session.plan = await negotiate(session.plan, on_update=on_update, transport=TRANSPORT)
        session.summary = f"{session.plan.outcome.status}: {session.plan.outcome.summary}"
        session.error = None
    except Exception as err:  # keep the session usable; the next message retries
        session.error = f"Negotiation failed: {type(err).__name__}: {err}"
    session.status = "idle"
    session.publish()


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "transport": TRANSPORT.name}


@app.post("/sessions")
async def create_session(body: TextIn) -> dict:
    if not body.text.strip():
        raise HTTPException(400, "text is empty")
    try:
        plan = await extract(body.text)
        plan, _ = await ask(plan)
    except Exception as err:
        raise HTTPException(502, f"Couldn't read that: {type(err).__name__}: {err}")
    session = Session(plan=plan, summary="Read your idea.")
    SESSIONS[plan.session_id] = session
    _maybe_negotiate(session, needed=True)
    return session.view()


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    return _get(session_id).view()


@app.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: TextIn) -> dict:
    session = _get(session_id)
    if session.status == "negotiating":
        raise HTTPException(409, "The agents are still negotiating; try again when they finish.")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text is empty")

    if text.lower() == "go":
        session.plan = skip_open_questions(session.plan)
        session.summary = "Going ahead with assumed values."
        renegotiate = True
    else:
        try:
            session.plan, session.summary, renegotiate = await update_from_user(session.plan, text)
        except Exception as err:
            raise HTTPException(502, f"Couldn't process that: {type(err).__name__}: {err}")

    # First negotiation runs as soon as the limits are known; later ones only on change.
    # A failed negotiation (error set) is retried on the next message.
    needed = renegotiate or not session.plan.negotiation or session.error is not None
    session.error = None
    _maybe_negotiate(session, needed)
    session.publish()
    return session.view()


@app.get("/sessions/{session_id}/stream")
async def stream(session_id: str) -> StreamingResponse:
    session = _get(session_id)
    queue: asyncio.Queue = asyncio.Queue()
    session.listeners.append(queue)

    async def events():
        try:
            yield f"event: session\ndata: {json.dumps(session.view())}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: session\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # stops proxies and browsers from dropping the connection
        finally:
            session.listeners.remove(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
