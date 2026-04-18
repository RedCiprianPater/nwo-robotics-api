"""NWO Robotics API Gateway — FastAPI application."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..models.database import create_tables
from .routes import router

app = FastAPI(
    title="NWO Robotics API",
    description=(
        "Layer 5 — Unified API gateway for the NWO Robotics platform. "
        "Single authenticated surface for all four layers: design, parts, printing, and skills."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    try:
        await create_tables()
    except Exception as e:
        print(f"[WARN] DB init: {e}")


@app.get("/health", tags=["System"])
async def root_health():
    return {"status": "ok", "service": "nwo-robotics-api", "version": "0.1.0"}


@app.get("/", tags=["System"])
async def root():
    return {
        "service": "NWO Robotics API Gateway",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/v1/health",
        "events": "ws://localhost:8080/v1/events",
        "graph": "/v1/graph/nodes",
    }
