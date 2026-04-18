"""
Authentication service.
Handles: agent DID registration, challenge-response auth, JWT issuance,
and per-request token verification.

Auth flow:
  1. Agent registers with name + ed25519 public key → gets DID
  2. Agent requests a nonce from the API
  3. Agent signs the nonce with their private key
  4. API verifies signature → issues JWT
  5. JWT included in Authorization: Bearer header for all subsequent requests
"""

from __future__ import annotations

import binascii
import os
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.orm import AgentDID, TokenAccount
from ..models.schemas import AgentRegisterRequest, AgentAuthResponse

_JWT_SECRET = os.getenv("JWT_SECRET", "change-this-in-production")
_JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
_JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
_REG_BONUS = int(os.getenv("AGENT_REGISTRATION_BONUS", "100"))

# In-memory nonce store (replace with Redis in production)
_pending_nonces: dict[str, str] = {}


# ── Registration ──────────────────────────────────────────────────────────────

async def register_agent(db: AsyncSession, req: AgentRegisterRequest) -> AgentDID:
    """Register a new agent or return the existing one if public key matches."""
    existing = (
        await db.execute(select(AgentDID).where(AgentDID.public_key == req.public_key))
    ).scalar_one_or_none()

    if existing:
        return existing

    import uuid
    agent_id = str(uuid.uuid4())
    did = f"did:nwo:{agent_id}"

    agent = AgentDID(
        id=agent_id,
        did=did,
        name=req.name,
        description=req.description,
        public_key=req.public_key,
        robot_type=req.robot_type,
        metadata_=req.metadata,
    )
    db.add(agent)
    await db.flush()

    # Create token account with registration bonus
    account = TokenAccount(
        agent_id=agent_id,
        balance=_REG_BONUS,
        total_earned=_REG_BONUS,
    )
    db.add(account)

    # Record bonus transaction
    from ..token_economy.ledger import record_transaction
    await record_transaction(
        db=db,
        account=account,
        amount=_REG_BONUS,
        reason="registration_bonus",
        reference_id=agent_id,
    )

    return agent


# ── Challenge-response auth ───────────────────────────────────────────────────

def issue_nonce(did: str) -> str:
    """Issue a one-time nonce for the agent to sign."""
    nonce = secrets.token_hex(32)
    _pending_nonces[did] = nonce
    return nonce


def verify_signature(public_key_str: str, nonce: str, signature_hex: str) -> bool:
    """
    Verify an ed25519 signature of the nonce.
    Returns True if valid.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature

        sig_bytes = binascii.unhexlify(signature_hex)

        # Try raw hex key first, then PEM
        try:
            raw = binascii.unhexlify(public_key_str)
            pub_key = Ed25519PublicKey.from_public_bytes(raw)
        except Exception:
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
            pub_key = load_pem_public_key(public_key_str.encode())

        pub_key.verify(sig_bytes, nonce.encode())
        return True
    except Exception:
        return False


async def authenticate_agent(
    db: AsyncSession,
    did: str,
    nonce: str,
    signature: str,
) -> AgentAuthResponse | None:
    """
    Verify challenge-response and issue a JWT if valid.
    Returns None if auth fails.
    """
    # Check nonce is valid
    expected_nonce = _pending_nonces.get(did)
    if not expected_nonce or expected_nonce != nonce:
        return None

    # Load agent
    agent = (
        await db.execute(select(AgentDID).where(AgentDID.did == did, AgentDID.is_active == True))  # noqa: E712
    ).scalar_one_or_none()
    if not agent:
        return None

    # Verify signature
    if not verify_signature(agent.public_key, nonce, signature):
        return None

    # Consume nonce
    _pending_nonces.pop(did, None)

    # Update last_seen
    await db.execute(
        update(AgentDID).where(AgentDID.id == agent.id).values(last_seen_at=datetime.now(timezone.utc))
    )

    # Issue JWT
    token = _create_jwt(agent.id, did)
    return AgentAuthResponse(
        access_token=token,
        token_type="bearer",
        expires_in=_JWT_EXPIRE_MINUTES * 60,
        did=did,
    )


def _create_jwt(agent_id: str, did: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=_JWT_EXPIRE_MINUTES)
    payload = {
        "sub": agent_id,
        "did": did,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


async def verify_jwt(token: str, db: AsyncSession) -> AgentDID | None:
    """Decode a JWT and return the AgentDID, or None if invalid."""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        agent_id = payload.get("sub")
        if not agent_id:
            return None
    except JWTError:
        return None

    return (
        await db.execute(select(AgentDID).where(AgentDID.id == agent_id, AgentDID.is_active == True))  # noqa: E712
    ).scalar_one_or_none()
