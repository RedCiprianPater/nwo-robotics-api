"""
Token economy ledger.
Credits earned when an agent's part/skill is used by others.
Credits spent when an agent uses platform compute (design, slice, run skill).

All mutations go through record_transaction() which atomically
updates the balance and appends an immutable ledger entry.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.orm import TokenAccount, TokenTransaction

# Token rates (from env)
_EARN_PART_DOWNLOAD = int(os.getenv("TOKENS_PER_PART_DOWNLOAD", "1"))
_EARN_SKILL_RUN = int(os.getenv("TOKENS_PER_SKILL_RUN", "2"))
_EARN_PRINT_JOB = int(os.getenv("TOKENS_PER_PRINT_JOB", "5"))
_COST_DESIGN = int(os.getenv("TOKENS_COST_PER_DESIGN", "10"))
_COST_SLICE = int(os.getenv("TOKENS_COST_PER_SLICE", "3"))
_COST_SKILL_RUN = int(os.getenv("TOKENS_COST_PER_SKILL_RUN", "1"))


async def get_or_create_account(db: AsyncSession, agent_id: str) -> TokenAccount:
    """Get the token account for an agent, creating one if it doesn't exist."""
    account = (
        await db.execute(select(TokenAccount).where(TokenAccount.agent_id == agent_id))
    ).scalar_one_or_none()

    if not account:
        account = TokenAccount(agent_id=agent_id, balance=0)
        db.add(account)
        await db.flush()

    return account


async def record_transaction(
    db: AsyncSession,
    account: TokenAccount,
    amount: int,
    reason: str,
    reference_id: str | None = None,
) -> TokenTransaction:
    """
    Atomically update the balance and append a ledger entry.

    Args:
        amount: Positive = credit, negative = debit.
        reason: Human-readable transaction reason.
        reference_id: Part ID, skill ID, job ID, etc.
    """
    new_balance = account.balance + amount
    if new_balance < 0:
        raise ValueError(f"Insufficient balance: {account.balance} < {abs(amount)} required")

    # Update account
    await db.execute(
        update(TokenAccount)
        .where(TokenAccount.id == account.id)
        .values(
            balance=new_balance,
            total_earned=TokenAccount.total_earned + max(0, amount),
            total_spent=TokenAccount.total_spent + max(0, -amount),
            updated_at=datetime.now(timezone.utc),
        )
    )
    account.balance = new_balance

    # Append ledger entry
    tx = TokenTransaction(
        account_id=account.id,
        amount=amount,
        reason=reason,
        reference_id=reference_id,
        balance_after=new_balance,
    )
    db.add(tx)
    await db.flush()
    return tx


async def credit_part_download(db: AsyncSession, publisher_agent_id: str, part_id: str) -> None:
    """Credit the publisher when their part is downloaded."""
    account = await get_or_create_account(db, publisher_agent_id)
    await record_transaction(db, account, _EARN_PART_DOWNLOAD, "part_download", part_id)


async def credit_skill_execution(db: AsyncSession, publisher_agent_id: str, skill_id: str) -> None:
    """Credit the publisher when their skill is executed."""
    account = await get_or_create_account(db, publisher_agent_id)
    await record_transaction(db, account, _EARN_SKILL_RUN, "skill_execution", skill_id)


async def credit_print_job(db: AsyncSession, part_publisher_agent_id: str, print_job_id: str) -> None:
    """Credit the part publisher when a print job uses their part."""
    account = await get_or_create_account(db, part_publisher_agent_id)
    await record_transaction(db, account, _EARN_PRINT_JOB, "print_job_used", print_job_id)


async def debit_design_generation(db: AsyncSession, agent_id: str, job_id: str) -> None:
    """Charge the agent for generating a part via Layer 1."""
    account = await get_or_create_account(db, agent_id)
    await record_transaction(db, account, -_COST_DESIGN, "design_generation", job_id)


async def debit_slice(db: AsyncSession, agent_id: str, job_id: str) -> None:
    """Charge the agent for slicing a file via Layer 3."""
    account = await get_or_create_account(db, agent_id)
    await record_transaction(db, account, -_COST_SLICE, "slice_job", job_id)


async def debit_skill_run(db: AsyncSession, agent_id: str, run_id: str) -> None:
    """Charge the agent for executing a skill via Layer 4."""
    account = await get_or_create_account(db, agent_id)
    await record_transaction(db, account, -_COST_SKILL_RUN, "skill_run", run_id)


async def transfer(
    db: AsyncSession,
    from_agent_id: str,
    to_agent_id: str,
    amount: int,
    reason: str = "manual_transfer",
) -> None:
    """Transfer tokens between two agents."""
    from_account = await get_or_create_account(db, from_agent_id)
    to_account = await get_or_create_account(db, to_agent_id)
    await record_transaction(db, from_account, -amount, reason, to_agent_id)
    await record_transaction(db, to_account, amount, reason, from_agent_id)


async def get_balance(db: AsyncSession, agent_id: str) -> TokenAccount:
    return await get_or_create_account(db, agent_id)


async def get_ledger(db: AsyncSession, agent_id: str, limit: int = 50) -> list[TokenTransaction]:
    from sqlalchemy import desc
    account = await get_or_create_account(db, agent_id)
    txs = (
        await db.execute(
            select(TokenTransaction)
            .where(TokenTransaction.account_id == account.id)
            .order_by(desc(TokenTransaction.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return list(txs)
