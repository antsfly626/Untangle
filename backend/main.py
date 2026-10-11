"""Run from repository root: uvicorn backend.main:app --reload."""

from fastapi import FastAPI

from backend.evidence.interfaces import router as evidence_router

app = FastAPI(title="Untangle")
app.include_router(evidence_router)
