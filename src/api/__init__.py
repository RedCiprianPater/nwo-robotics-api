"""FastAPI routes for the NWO Robotics API Gateway (Layer 5)."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    APIRouter, Depends, Header, HTTPException,
    Query, Request, WebSocket, WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.auth import authenticate_agent, issue_nonce, register_agent, verify_jwt
from ..gateway.proxy import check_layer_health, proxy_request
from ..graph.service import create_node, get_node, query_nodes
from ..models.database import get_session
from ..models.orm import AgentDID, GraphNode, TokenAccount, TokenTransaction
from ..models.schemas import (
    AgentAuthRequest, AgentRegisterRequest,
    GraphEdgeCreate, GraphNodeCreate,
    PlatformHealth, LayerHealth,
    TokenTransferRequest,
)
from ..token_economy.ledger import get_balance, get_ledger, transfer
from ..ws.broadcaster import broadcaster

router = APIRouter(prefix="/v1")
DB = Annotated[AsyncSession, Depends(get_session)]


# ── Auth dependency ────────────────────────────────────────────────────────────

async def get_current_agent(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> AgentDID | None:
    """Extract agent from JWT. Returns None if no/invalid token (allows optional auth)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    return await verify_jwt(token, db)


async def require_agent(
    agent: AgentDID | None = Depends(get_current_agent),
) -> AgentDID:
    """Like get_current_agent but raises 401 if not authenticated."""
    if not agent:
        raise HTTPException(status_code=401, detail="Authentication required")
    return agent


# ── /v1/agents ────────────────────────────────────────────────────────────────

@router.post("/agents/register", tags=["Agents"])
async def register(req: AgentRegisterRequest, db: DB):
    """Register a new agent identity and receive a DID."""
    agent = await register_agent(db, req)
    return {
        "did": agent.did,
        "id": agent.id,
        "name": agent.name,
        "message": f"Agent registered. DID: {agent.did}",
    }


@router.get("/agents/nonce", tags=["Agents"])
async def get_nonce(did: str = Query(...)):
    """Get a one-time nonce for challenge-response authentication."""
    nonce = issue_nonce(did)
    return {"did": did, "nonce": nonce}


@router.post("/agents/auth", tags=["Agents"])
async def auth(req: AgentAuthRequest, db: DB):
    """Exchange a signed nonce for a JWT access token."""
    result = await authenticate_agent(db, req.did, req.nonce, req.signature)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid signature or nonce")
    return result


@router.get("/agents/{did}", tags=["Agents"])
async def resolve_did(did: str, db: DB):
    """Resolve an agent DID and return the DID document."""
    agent = (
        await db.execute(select(AgentDID).where(AgentDID.did == did))
    ).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="DID not found")
    return {
        "@context": ["https://www.w3.org/ns/did/v1", "https://nworobotics.cloud/did/v1"],
        "id": agent.did,
        "name": agent.name,
        "description": agent.description,
        "robot_type": agent.robot_type,
        "verificationMethod": [{
            "id": f"{agent.did}#key-1",
            "type": "Ed25519VerificationKey2020",
            "controller": agent.did,
            "publicKeyHex": agent.public_key,
        }],
        "authentication": [f"{agent.did}#key-1"],
        "stats": {
            "parts_published": agent.parts_published,
            "skills_published": agent.skills_published,
            "parts_downloaded": agent.parts_downloaded,
            "skills_executed": agent.skills_executed,
        },
        "created": agent.created_at.isoformat(),
        "last_seen": agent.last_seen_at.isoformat(),
    }


@router.get("/agents/{did}/activity", tags=["Agents"])
async def agent_activity(did: str, db: DB, limit: int = Query(default=20, ge=1, le=100)):
    """Recent graph nodes posted by an agent."""
    agent = (
        await db.execute(select(AgentDID).where(AgentDID.did == did))
    ).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="DID not found")
    nodes = (await db.execute(
        select(GraphNode)
        .where(GraphNode.agent_id == agent.id)
        .order_by(desc(GraphNode.created_at))
        .limit(limit)
    )).scalars().all()
    return [{"id": n.id, "node_type": n.node_type, "title": n.title,
             "created_at": n.created_at.isoformat()} for n in nodes]


# ── /v1/graph ─────────────────────────────────────────────────────────────────

@router.post("/graph/nodes", tags=["Graph"])
async def post_node(
    req: GraphNodeCreate,
    db: DB,
    agent: AgentDID = Depends(require_agent),
):
    """Post a new node to the NWO Agent Graph."""
    node, node_dict = await create_node(db, agent, req)
    return node_dict


@router.get("/graph/nodes", tags=["Graph"])
async def list_nodes(
    db: DB,
    node_type: str | None = Query(default=None),
    agent_did: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: AgentDID | None = Depends(get_current_agent),
):
    """Query the agent graph with optional filters."""
    return await query_nodes(db, node_type=node_type, agent_did=agent_did,
                             tag=tag, limit=limit, offset=offset)


@router.get("/graph/nodes/{node_id}", tags=["Graph"])
async def get_graph_node(node_id: str, db: DB):
    """Get a specific graph node by ID."""
    node = await get_node(db, node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


# ── /v1/tokens ────────────────────────────────────────────────────────────────

@router.get("/tokens/balance/{did}", tags=[
