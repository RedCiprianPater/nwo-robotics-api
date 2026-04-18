"""
Real-time WebSocket event broadcaster.
Maintains a set of active WebSocket connections and broadcasts
platform events (new graph nodes, print completions, skill runs, etc.)
to all subscribed clients.

Events are JSON objects with shape:
  { "event": "graph_node_created", "data": {...}, "ts": "2026-..." }
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class EventBroadcaster:
    """Singleton broadcaster — one instance shared across the app lifetime."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, event: str, data: dict[str, Any]) -> None:
        """Send an event to all connected WebSocket clients."""
        payload = json.dumps({
            "event": event,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        dead: set[WebSocket] = set()
        async with self._lock:
            connections = set(self._connections)

        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)

        if dead:
            async with self._lock:
                self._connections -= dead

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Singleton instance
broadcaster = EventBroadcaster()


# ── Typed event helpers ────────────────────────────────────────────────────────

async def emit_graph_node(node_id: str, node_type: str, title: str, agent_did: str) -> None:
    await broadcaster.broadcast("graph_node_created", {
        "node_id": node_id, "node_type": node_type,
        "title": title, "agent_did": agent_did,
    })


async def emit_part_published(part_id: str, part_name: str, agent_did: str) -> None:
    await broadcaster.broadcast("part_published", {
        "part_id": part_id, "name": part_name, "agent_did": agent_did,
    })


async def emit_skill_published(skill_id: str, skill_name: str, agent_did: str) -> None:
    await broadcaster.broadcast("skill_published", {
        "skill_id": skill_id, "name": skill_name, "agent_did": agent_did,
    })


async def emit_print_complete(job_id: str, printer_id: str, agent_did: str) -> None:
    await broadcaster.broadcast("print_complete", {
        "job_id": job_id, "printer_id": printer_id, "agent_did": agent_did,
    })


async def emit_skill_executed(run_id: str, skill_id: str, status: str, agent_did: str) -> None:
    await broadcaster.broadcast("skill_executed", {
        "run_id": run_id, "skill_id": skill_id,
        "status": status, "agent_did": agent_did,
    })


async def emit_token_earned(did: str, amount: int, reason: str) -> None:
    await broadcaster.broadcast("token_earned", {
        "did": did, "amount": amount, "reason": reason,
    })
