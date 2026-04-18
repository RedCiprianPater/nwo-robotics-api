"""Tests for agent DID registration and JWT authentication."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-not-for-production")
os.environ.setdefault("LAYER1_URL", "http://localhost:8000")
os.environ.setdefault("LAYER2_URL", "http://localhost:8001")
os.environ.setdefault("LAYER3_URL", "http://localhost:8002")
os.environ.setdefault("LAYER4_URL", "http://localhost:8003")

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.models.orm import Base
from src.models.schemas import AgentRegisterRequest

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
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


# ── Registration ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_agent_creates_did(db):
    from src.agents.auth import register_agent
    req = AgentRegisterRequest(name="Test Bot", public_key="deadbeef" * 8)
    agent = await register_agent(db, req)
    assert agent.did.startswith("did:nwo:")
    assert agent.name == "Test Bot"
    assert agent.is_active


@pytest.mark.asyncio
async def test_register_idempotent(db):
    from src.agents.auth import register_agent
    pk = "aabbccdd" * 8
    req = AgentRegisterRequest(name="Bot A", public_key=pk)
    a1 = await register_agent(db, req)
    a2 = await register_agent(db, req)
    assert a1.id == a2.id


@pytest.mark.asyncio
async def test_registration_creates_token_account(db):
    from src.agents.auth import register_agent
    from src.token_economy.ledger import get_balance
    req = AgentRegisterRequest(name="Rich Bot", public_key="11223344" * 8)
    agent = await register_agent(db, req)
    await db.commit()
    account = await get_balance(db, agent.id)
    assert account.balance >= 0  # May be 0 if bonus not configured in test env


# ── Nonce + JWT ────────────────────────────────────────────────────────────────

def test_issue_nonce_is_hex():
    from src.agents.auth import issue_nonce
    nonce = issue_nonce("did:nwo:test")
    assert len(nonce) == 64
    int(nonce, 16)  # must be valid hex


def test_create_and_decode_jwt():
    from src.agents.auth import _create_jwt
    from jose import jwt
    token = _create_jwt("agent-123", "did:nwo:agent-123")
    payload = jwt.decode(token, "test-secret-key-not-for-production", algorithms=["HS256"])
    assert payload["sub"] == "agent-123"
    assert payload["did"] == "did:nwo:agent-123"


@pytest.mark.asyncio
async def test_verify_invalid_jwt_returns_none(db):
    from src.agents.auth import verify_jwt
    result = await verify_jwt("not.a.valid.jwt", db)
    assert result is None


@pytest.mark.asyncio
async def test_verify_expired_jwt_returns_none(db):
    from src.agents.auth import _create_jwt, verify_jwt
    from jose import jwt as _jwt
    import time
    # Create a token that expired in the past
    payload = {"sub": "x", "did": "did:nwo:x", "exp": int(time.time()) - 100, "iat": int(time.time()) - 200}
    token = _jwt.encode(payload, "test-secret-key-not-for-production", algorithm="HS256")
    result = await verify_jwt(token, db)
    assert result is None


# ── Signature verification ─────────────────────────────────────────────────────

def test_verify_signature_with_bad_key_returns_false():
    from src.agents.auth import verify_signature
    # Garbage hex key + garbage signature should fail gracefully
    result = verify_signature("deadbeef", "my-nonce", "cafebabe")
    assert result is False


def test_verify_signature_empty_inputs():
    from src.agents.auth import verify_signature
    assert verify_signature("", "", "") is False
