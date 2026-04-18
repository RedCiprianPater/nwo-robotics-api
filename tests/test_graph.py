"""Tests for the NWO Agent Graph service."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")

from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.models.orm import Base

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSession = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db():
    async with TestSession() as session:
        yield session
        await session.commit()


def _make_agent(agent_id: str = "agent-001"):
    from unittest.mock import MagicMock
    agent = MagicMock()
    agent.id = agent_id
    agent.did = f"did:nwo:{agent_id}"
    agent.name = "Test Agent"
    return agent


@pytest.mark.asyncio
async def test_create_node_persists(db):
    from src.graph.service import create_node
    from src.models.schemas import GraphNodeCreate

    agent = _make_agent()
    req = GraphNodeCreate(
        node_type="design",
        title="Designed a servo bracket",
        body="Used L1 to generate MG996R bracket",
        data={"part_name": "bracket", "prompt": "servo bracket MG996R"},
        tags=["servo", "bracket"],
    )

    with patch("src.graph.service.emit_graph_node", new_callable=AsyncMock):
        node, node_dict = await create_node(db, agent, req)

    assert node.id is not None
    assert node_dict["node_type"] == "design"
    assert node_dict["title"] == "Designed a servo bracket"
    assert node_dict["agent_did"] == "did:nwo:agent-001"
    assert "servo" in node_dict["tags"]


@pytest.mark.asyncio
async def test_get_node_returns_dict(db):
    from src.graph.service import create_node, get_node
    from src.models.schemas import GraphNodeCreate

    agent = _make_agent()
    req = GraphNodeCreate(node_type="capability", title="Can do motion planning")

    with patch("src.graph.service.emit_graph_node", new_callable=AsyncMock):
        node, _ = await create_node(db, agent, req)

    await db.commit()

    result = await get_node(db, node.id)
    assert result is not None
    assert result["id"] == node.id
    assert result["node_type"] == "capability"
    assert "edge_count" in result


@pytest.mark.asyncio
async def test_get_nonexistent_node_returns_none(db):
    from src.graph.service import get_node
    result = await get_node(db, "does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_query_nodes_returns_public_only(db):
    from src.graph.service import create_node, query_nodes
    from src.models.schemas import GraphNodeCreate

    agent = _make_agent()

    with patch("src.graph.service.emit_graph_node", new_callable=AsyncMock):
        await create_node(db, agent, GraphNodeCreate(node_type="sensor_reading", title="Public node", is_public=True))
        await create_node(db, agent, GraphNodeCreate(node_type="intent", title="Private node", is_public=False))

    await db.commit()

    result = await query_nodes(db, limit=50)
    titles = [n["title"] for n in result["nodes"]]
    assert "Public node" in titles
    assert "Private node" not in titles


@pytest.mark.asyncio
async def test_query_nodes_filter_by_type(db):
    from src.graph.service import create_node, query_nodes
    from src.models.schemas import GraphNodeCreate

    agent = _make_agent()

    with patch("src.graph.service.emit_graph_node", new_callable=AsyncMock):
        await create_node(db, agent, GraphNodeCreate(node_type="design", title="A design"))
        await create_node(db, agent, GraphNodeCreate(node_type="skill_published", title="A skill"))

    await db.commit()

    result = await query_nodes(db, node_type="design")
    assert all(n["node_type"] == "design" for n in result["nodes"])


@pytest.mark.asyncio
async def test_create_node_broadcasts_event(db):
    from src.graph.service import create_node
    from src.models.schemas import GraphNodeCreate

    agent = _make_agent()
    req = GraphNodeCreate(node_type="part_published", title="Published bracket v2")

    with patch("src.graph.service.emit_graph_node", new_callable=AsyncMock) as mock_emit:
        await create_node(db, agent, req)
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert "part_published" in call_kwargs[0]


@pytest.mark.asyncio
async def test_query_nodes_returns_total(db):
    from src.graph.service import create_node, query_nodes
    from src.models.schemas import GraphNodeCreate

    agent = _make_agent()

    with patch("src.graph.service.emit_graph_node", new_callable=AsyncMock):
        for i in range(5):
            await create_node(db, agent, GraphNodeCreate(node_type="observation", title=f"Obs {i}"))

    await db.commit()

    result = await query_nodes(db, limit=2)
    assert result["total"] == 5
    assert len(result["nodes"]) == 2
