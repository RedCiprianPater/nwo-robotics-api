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

@router.get("/tokens/balance/{did}", tags=["Tokens"])
async def token_balance(did: str, db: DB):
    """Get the token balance for an agent DID."""
    agent = (await db.execute(select(AgentDID).where(AgentDID.did == did))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    account = await get_balance(db, agent.id)
    return {
        "did": did,
        "balance": account.balance,
        "total_earned": account.total_earned,
        "total_spent": account.total_spent,
        "updated_at": account.updated_at.isoformat(),
    }


@router.get("/tokens/ledger/{did}", tags=["Tokens"])
async def token_ledger(did: str, db: DB, limit: int = Query(default=50, ge=1, le=200)):
    """Get transaction history for an agent DID."""
    agent = (await db.execute(select(AgentDID).where(AgentDID.did == did))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    account = await get_balance(db, agent.id)
    txs = await get_ledger(db, agent.id, limit=limit)
    return {
        "did": did,
        "balance": account.balance,
        "transactions": [
            {"id": t.id, "amount": t.amount, "reason": t.reason,
             "reference_id": t.reference_id, "balance_after": t.balance_after,
             "created_at": t.created_at.isoformat()}
            for t in txs
        ],
    }


@router.post("/tokens/transfer", tags=["Tokens"])
async def token_transfer(
    req: TokenTransferRequest,
    db: DB,
    agent: AgentDID = Depends(require_agent),
):
    """Transfer tokens between two agents."""
    if agent.did != req.from_did:
        raise HTTPException(status_code=403, detail="Can only transfer from your own account")
    from_agent = (await db.execute(select(AgentDID).where(AgentDID.did == req.from_did))).scalar_one_or_none()
    to_agent = (await db.execute(select(AgentDID).where(AgentDID.did == req.to_did))).scalar_one_or_none()
    if not from_agent or not to_agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        await transfer(db, from_agent.id, to_agent.id, req.amount, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"Transferred {req.amount} tokens from {req.from_did} to {req.to_did}"}


# ── /v1/design, /v1/parts, /v1/print, /v1/skills (proxied) ───────────────────

@router.api_route("/design/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Proxy→L1"])
async def proxy_design(request: Request, path: str, agent: AgentDID | None = Depends(get_current_agent)):
    return await proxy_request(1, f"/design/{path}", request, agent_id=agent.id if agent else None)


@router.api_route("/parts/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Proxy→L2"])
async def proxy_parts(request: Request, path: str, agent: AgentDID | None = Depends(get_current_agent)):
    return await proxy_request(2, f"/parts/{path}", request, agent_id=agent.id if agent else None)


@router.api_route("/gallery/{path:path}", methods=["GET"], tags=["Proxy→L2"])
async def proxy_gallery(request: Request, path: str, agent: AgentDID | None = Depends(get_current_agent)):
    return await proxy_request(2, f"/gallery/{path}", request)


@router.api_route("/print/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Proxy→L3"])
async def proxy_print(request: Request, path: str, agent: AgentDID | None = Depends(get_current_agent)):
    return await proxy_request(3, f"/print/{path}", request, agent_id=agent.id if agent else None)


@router.api_route("/printers/{path:path}", methods=["GET", "POST"], tags=["Proxy→L3"])
async def proxy_printers(request: Request, path: str):
    return await proxy_request(3, f"/printers/{path}", request)


@router.api_route("/skills/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["Proxy→L4"])
async def proxy_skills(request: Request, path: str, agent: AgentDID | None = Depends(get_current_agent)):
    return await proxy_request(4, f"/skills/{path}", request, agent_id=agent.id if agent else None)


# ── /v1/health ────────────────────────────────────────────────────────────────

@router.get("/health", tags=["System"])
async def platform_health(db: DB):
    """Check health of all five layers."""
    import asyncio
    layer_checks = await asyncio.gather(
        check_layer_health(1), check_layer_health(2),
        check_layer_health(3), check_layer_health(4),
    )
    total_agents = (await db.execute(select(func.count()).select_from(AgentDID))).scalar() or 0
    total_nodes = (await db.execute(select(func.count()).select_from(GraphNode))).scalar() or 0
    all_ok = all(l["status"] == "ok" for l in layer_checks)
    return {
        "status": "ok" if all_ok else "degraded",
        "layers": layer_checks,
        "total_agents": total_agents,
        "total_graph_nodes": total_nodes,
        "ws_connections": broadcaster.connection_count,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── /v1/events (WebSocket) ────────────────────────────────────────────────────

@router.websocket("/events")
async def events_stream(websocket: WebSocket):
    """Real-time event stream. Broadcasts graph activity, print jobs, skill runs."""
    await broadcaster.connect(websocket)
    try:
        while True:
            # Keep connection alive; server pushes events
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.disconnect(websocket)


# ── /v1/admin ─────────────────────────────────────────────────────────────────

@router.get("/admin/dashboard", response_class=HTMLResponse, tags=["Admin"])
async def admin_dashboard(
    db: DB,
    x_admin_password: str | None = Header(default=None, alias="X-Admin-Password"),
):
    """Minimal HTML admin dashboard."""
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if admin_pw and x_admin_password != admin_pw:
        raise HTTPException(status_code=401, detail="Admin password required in X-Admin-Password header")

    total_agents = (await db.execute(select(func.count()).select_from(AgentDID))).scalar() or 0
    total_nodes = (await db.execute(select(func.count()).select_from(GraphNode))).scalar() or 0
    total_tokens = (await db.execute(select(func.sum(TokenAccount.balance)))).scalar() or 0
    recent_nodes = (await db.execute(
        select(GraphNode, AgentDID.did, AgentDID.name)
        .join(AgentDID, GraphNode.agent_id == AgentDID.id)
        .order_by(desc(GraphNode.created_at)).limit(10)
    )).all()

    node_rows = "".join(
        f"<tr><td>{n.node_type}</td><td>{n.title[:50]}</td><td>{did}</td>"
        f"<td>{n.created_at.strftime('%H:%M:%S')}</td></tr>"
        for n, did, name in recent_nodes
    )

    return f"""<!DOCTYPE html>
<html>
<head><title>NWO Admin</title>
<style>
  body {{ font-family: 'JetBrains Mono', monospace; background:#0a1f0f; color:#00d65a; padding:40px; }}
  h1 {{ font-size:28px; margin-bottom:24px; }}
  .stats {{ display:flex; gap:32px; margin-bottom:32px; }}
  .stat {{ background:#0d3d1f; padding:20px 32px; border:1px solid #1b8a3a; }}
  .num {{ font-size:40px; font-weight:700; color:#00d65a; }}
  .label {{ font-size:12px; color:#4a5d50; text-transform:uppercase; letter-spacing:0.1em; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; padding:8px; border-bottom:1px solid #1b8a3a; color:#4a5d50; font-size:11px; text-transform:uppercase; }}
  td {{ padding:8px; border-bottom:1px solid #0d3d1f; font-size:13px; }}
  tr:hover td {{ background:#0d3d1f; }}
</style>
</head>
<body>
<h1>// NWO ROBOTICS · ADMIN</h1>
<div class="stats">
  <div class="stat"><div class="num">{total_agents}</div><div class="label">Agents</div></div>
  <div class="stat"><div class="num">{total_nodes}</div><div class="label">Graph nodes</div></div>
  <div class="stat"><div class="num">{total_tokens}</div><div class="label">Tokens in circulation</div></div>
  <div class="stat"><div class="num">{broadcaster.connection_count}</div><div class="label">WS connections</div></div>
</div>
<h2 style="margin-bottom:16px;font-size:16px;">Recent graph activity</h2>
<table>
  <thead><tr><th>Type</th><th>Title</th><th>Agent DID</th><th>Time</th></tr></thead>
  <tbody>{node_rows}</tbody>
</table>
</body></html>"""
