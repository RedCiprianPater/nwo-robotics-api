"""Tests for the token economy ledger."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("AGENT_REGISTRATION_BONUS", "100")
os.environ.setdefault("TOKENS_PER_PART_DOWNLOAD", "1")
os.environ.setdefault("TOKENS_PER_SKILL_RUN", "2")
os.environ.setdefault("TOKENS_COST_PER_DESIGN", "10")

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


async def _make_account(db, agent_id: str, starting_balance: int = 50):
    from src.models.orm import TokenAccount
    account = TokenAccount(agent_id=agent_id, balance=starting_balance,
                          total_earned=starting_balance)
    db.add(account)
    await db.flush()
    return account


@pytest.mark.asyncio
async def test_record_transaction_credit(db):
    from src.token_economy.ledger import record_transaction
    account = await _make_account(db, "agent-a", 50)
    tx = await record_transaction(db, account, 10, "test_credit", "ref-1")
    assert account.balance == 60
    assert tx.amount == 10
    assert tx.balance_after == 60
    assert tx.reason == "test_credit"


@pytest.mark.asyncio
async def test_record_transaction_debit(db):
    from src.token_economy.ledger import record_transaction
    account = await _make_account(db, "agent-b", 50)
    tx = await record_transaction(db, account, -20, "test_debit")
    assert account.balance == 30
    assert tx.amount == -20
    assert tx.balance_after == 30


@pytest.mark.asyncio
async def test_debit_below_zero_raises(db):
    from src.token_economy.ledger import record_transaction
    account = await _make_account(db, "agent-c", 5)
    with pytest.raises(ValueError, match="Insufficient balance"):
        await record_transaction(db, account, -10, "overspend")


@pytest.mark.asyncio
async def test_get_or_create_account_creates_new(db):
    from src.token_economy.ledger import get_or_create_account
    account = await get_or_create_account(db, "brand-new-agent")
    assert account is not None
    assert account.balance == 0


@pytest.mark.asyncio
async def test_get_or_create_account_idempotent(db):
    from src.token_economy.ledger import get_or_create_account
    a1 = await get_or_create_account(db, "agent-idempotent")
    a2 = await get_or_create_account(db, "agent-idempotent")
    assert a1.id == a2.id


@pytest.mark.asyncio
async def test_transfer_between_agents(db):
    from src.token_economy.ledger import transfer, get_balance
    import uuid
    id_from = str(uuid.uuid4())
    id_to = str(uuid.uuid4())
    acc_from = await _make_account(db, id_from, 100)
    acc_to = await _make_account(db, id_to, 0)

    await transfer(db, id_from, id_to, 30, "peer_payment")

    from_acc = await get_balance(db, id_from)
    to_acc = await get_balance(db, id_to)
    assert from_acc.balance == 70
    assert to_acc.balance == 30


@pytest.mark.asyncio
async def test_transfer_insufficient_balance_raises(db):
    from src.token_economy.ledger import transfer
    import uuid
    id_from = str(uuid.uuid4())
    id_to = str(uuid.uuid4())
    await _make_account(db, id_from, 5)
    await _make_account(db, id_to, 0)
    with pytest.raises(ValueError):
        await transfer(db, id_from, id_to, 100)


@pytest.mark.asyncio
async def test_credit_part_download(db):
    from src.token_economy.ledger import credit_part_download, get_balance
    import uuid
    agent_id = str(uuid.uuid4())
    account = await _make_account(db, agent_id, 0)
    await credit_part_download(db, agent_id, "part-123")
    acc = await get_balance(db, agent_id)
    assert acc.balance == 1  # TOKENS_PER_PART_DOWNLOAD=1


@pytest.mark.asyncio
async def test_get_ledger_returns_transactions(db):
    from src.token_economy.ledger import get_ledger, record_transaction
    import uuid
    agent_id = str(uuid.uuid4())
    account = await _make_account(db, agent_id, 100)
    await record_transaction(db, account, 5, "event_a")
    await record_transaction(db, account, 10, "event_b")
    txs = await get_ledger(db, agent_id, limit=10)
    assert len(txs) == 2
