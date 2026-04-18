"""NWO Agent Graph service — node creation, querying, edge management."""

from __future__ import annotations

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.orm import AgentDID, GraphEdge, GraphNode
from ..models.schemas import GraphEdgeCreate, GraphNodeCreate
from ..ws.broadcaster import emit_graph_node


async def create_node(
    db: AsyncSession,
    agent: AgentDID,
    req: GraphNodeCreate,
) -> tuple[GraphNode, dict]:
    """Create a graph node and broadcast it to WebSocket subscribers."""
    node = GraphNode(
        agent_id=agent.id,
        node_type=req.node_type,
        title=req.title,
        body=req.body,
        data=req.data,
        tags=req.tags,
        is_public=req.is_public,
        layer1_job_id=req.layer1_job_id,
        layer2_part_id=req.layer2_part_id,
        layer3_job_id=req.layer3_job_id,
        layer4_skill_id=req.layer4_skill_id,
    )
    db.add(node)
    await db.flush()

    # Broadcast
    await emit_graph_node(node.id, node.node_type, node.title, agent.did)

    node_dict = _node_to_dict(node, agent)
    return node, node_dict


async def get_node(db: AsyncSession, node_id: str) -> dict | None:
    row = (await db.execute(
        select(GraphNode, AgentDID)
        .join(AgentDID, GraphNode.agent_id == AgentDID.id)
        .where(GraphNode.id == node_id)
    )).first()
    if not row:
        return None
    node, agent = row
    edge_count = (await db.execute(
        select(__import__("sqlalchemy").func.count())
        .select_from(GraphEdge)
        .where((GraphEdge.source_node_id == node_id) | (GraphEdge.target_node_id == node_id))
    )).scalar() or 0
    d = _node_to_dict(node, agent)
    d["edge_count"] = int(edge_count)
    return d


async def query_nodes(
    db: AsyncSession,
    node_type: str | None = None,
    agent_did: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    from sqlalchemy import func
    filters = [GraphNode.is_public == True]  # noqa: E712
    if node_type:
        filters.append(GraphNode.node_type == node_type)
    if agent_did:
        agent = (await db.execute(
            select(AgentDID).where(AgentDID.did == agent_did)
        )).scalar_one_or_none()
        if agent:
            filters.append(GraphNode.agent_id == agent.id)
    if tag:
        filters.append(GraphNode.tags.any(tag))

    stmt = (
        select(GraphNode, AgentDID)
        .join(AgentDID, GraphNode.agent_id == AgentDID.id)
        .where(and_(*filters))
        .order_by(desc(GraphNode.created_at))
        .limit(limit).offset(offset)
    )
    count_stmt = select(func.count()).select_from(GraphNode).where(and_(*filters))

    rows = (await db.execute(stmt)).all()
    total = (await db.execute(count_stmt)).scalar() or 0

    return {
        "total": int(total),
        "nodes": [_node_to_dict(n, a) for n, a in rows],
    }


async def create_edge(db: AsyncSession, req: GraphEdgeCreate) -> GraphEdge:
    edge = GraphEdge(
        source_node_id=req.source_node_id,
        target_node_id=req.target_node_id,
        relation=req.relation,
        weight=req.weight,
        metadata_=req.metadata,
    )
    db.add(edge)
    await db.flush()
    return edge


def _node_to_dict(node: GraphNode, agent: AgentDID) -> dict:
    return {
        "id": node.id,
        "agent_id": node.agent_id,
        "agent_did": agent.did,
        "agent_name": agent.name,
        "node_type": node.node_type,
        "title": node.title,
        "body": node.body,
        "data": node.data,
        "tags": node.tags or [],
        "is_public": node.is_public,
        "layer1_job_id": node.layer1_job_id,
        "layer2_part_id": node.layer2_part_id,
        "layer3_job_id": node.layer3_job_id,
        "layer4_skill_id": node.layer4_skill_id,
        "created_at": node.created_at.isoformat(),
        "edge_count": 0,
    }
