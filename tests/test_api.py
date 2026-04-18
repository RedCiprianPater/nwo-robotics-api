"""Integration tests for the Layer 5 API routes."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-tests")
os.environ.setdefault("AGENT_REGISTRATION_BONUS", "0")
os.environ.setdefault("LAYER1_URL", "http://localhost:8000")
os.environ.setdefault("LAYER2_URL", "http://localhost:8001")
os.environ.setdefault("LAYER3_URL", "http://localhost:8002")
os.environ.setdefault("LAYER4_URL", "http://localhost:8003")

from src.api.main import app
from src.models.database import get_session
from src.models.orm import Base

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSession = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_session():
    async with TestSession() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── System endpoints ──────────────────────────────────────────────────────────

def test_root_returns_service_info(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "NWO Robotics API Gateway"
    assert "docs" in data
    assert "graph" in data


def test_root_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_v1_health_runs(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert "layers" in data
    assert "total_agents" in data
    assert "ws_connections" in data


# ── Agent registration ────────────────────────────────────────────────────────

def test_register_agent(client):
    r = client.post("/v1/agents/register", json={
        "name": "Test Bot",
        "public_key": "aabbccdd" * 8,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["did"].startswith("did:nwo:")
    assert data["name"] == "Test Bot"


def test_register_agent_idempotent(client):
    pk = "11223344" * 8
    r1 = client.post("/v1/agents/register", json={"name": "Bot A", "public_key": pk})
    r2 = client.post("/v1/agents/register", json={"name": "Bot B", "public_key": pk})
    assert r1.json()["did"] == r2.json()["did"]


def test_get_nonce(client):
    r = client.get("/v1/agents/nonce", params={"did": "did:nwo:test-123"})
    assert r.status_code == 200
    data = r.json()
    assert "nonce" in data
    assert len(data["nonce"]) == 64


def test_auth_invalid_signature_returns_401(client):
    r = client.post("/v1/agents/auth", json={
        "did": "did:nwo:nonexistent",
        "nonce": "abc",
        "signature": "def",
    })
    assert r.status_code == 401


def test_resolve_did(client):
    # Register first
    reg = client.post("/v1/agents/register", json={
        "name": "Resolve Bot",
        "public_key": "55667788" * 8,
    })
    did = reg.json()["did"]

    r = client.get(f"/v1/agents/{did}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == did
    assert data["name"] == "Resolve Bot"
    assert "@context" in data
    assert "verificationMethod" in data


def test_resolve_unknown_did_returns_404(client):
    r = client.get("/v1/agents/did:nwo:does-not-exist")
    assert r.status_code == 404


# ── Token endpoints ────────────────────────────────────────────────────────────

def test_token_balance_for_registered_agent(client):
    reg = client.post("/v1/agents/register", json={
        "name": "Token Bot", "public_key": "99aabbcc" * 8
    })
    did = reg.json()["did"]
    r = client.get(f"/v1/tokens/balance/{did}")
    assert r.status_code == 200
    data = r.json()
    assert "balance" in data
    assert "total_earned" in data


def test_token_balance_unknown_agent_returns_404(client):
    r = client.get("/v1/tokens/balance/did:nwo:ghost")
    assert r.status_code == 404


def test_token_ledger_returns_transactions(client):
    reg = client.post("/v1/agents/register", json={
        "name": "Ledger Bot", "public_key": "ddeeff00" * 8
    })
    did = reg.json()["did"]
    r = client.get(f"/v1/tokens/ledger/{did}")
    assert r.status_code == 200
    data = r.json()
    assert "transactions" in data
    assert isinstance(data["transactions"], list)


# ── Graph endpoints ────────────────────────────────────────────────────────────

def test_graph_nodes_requires_auth(client):
    r = client.post("/v1/graph/nodes", json={
        "node_type": "design", "title": "test"
    })
    assert r.status_code == 401


def test_graph_query_public_accessible(client):
    r = client.get("/v1/graph/nodes")
    assert r.status_code == 200
    data = r.json()
    assert "nodes" in data
    assert "total" in data


def test_get_nonexistent_graph_node(client):
    r = client.get("/v1/graph/nodes/does-not-exist")
    assert r.status_code == 404


# ── Admin dashboard ────────────────────────────────────────────────────────────

def test_admin_dashboard_no_password(client):
    """Without ADMIN_PASSWORD set, dashboard should be accessible."""
    r = client.get("/v1/admin/dashboard")
    assert r.status_code == 200
    assert "NWO ROBOTICS" in r.text


def test_admin_dashboard_wrong_password(client):
    with patch.dict(os.environ, {"ADMIN_PASSWORD": "secret123"}):
        r = client.get("/v1/admin/dashboard", headers={"X-Admin-Password": "wrong"})
        assert r.status_code == 401
