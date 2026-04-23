"""Cross-system identity hub endpoints for L5."""
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.database import get_session
from ...models.identity import Identity, IdentityType


router = APIRouter(prefix="/v1/identities", tags=["Identities"])

IDENTITY_SERVICE_KEY = os.environ.get("IDENTITY_SERVICE_KEY", "")


async def require_service_key(x_service_key: Optional[str] = Header(None)):
    if not IDENTITY_SERVICE_KEY:
        raise HTTPException(500, "IDENTITY_SERVICE_KEY not configured on server")
    if x_service_key != IDENTITY_SERVICE_KEY:
        raise HTTPException(403, "Invalid or missing X-Service-Key header")
    return True


class IdentityCreate(BaseModel):
    identity_type: IdentityType
    supabase_user_id: Optional[uuid.UUID] = None
    nwo_did: Optional[str] = None
    cardiac_root_token_id: Optional[str] = None
    cardiac_hash: Optional[str] = None
    primary_wallet: Optional[str] = None
    display_name: Optional[str] = None
    owned_by: Optional[uuid.UUID] = None
    metadata: dict = Field(default_factory=dict)


class IdentityUpdate(BaseModel):
    supabase_user_id: Optional[uuid.UUID] = None
    nwo_did: Optional[str] = None
    cardiac_root_token_id: Optional[str] = None
    cardiac_hash: Optional[str] = None
    primary_wallet: Optional[str] = None
    display_name: Optional[str] = None
    owned_by: Optional[uuid.UUID] = None
    metadata: Optional[dict] = None


def _normalize_wallet(wallet: Optional[str]) -> Optional[str]:
    if wallet is None:
        return None
    wallet = wallet.strip()
    if wallet.startswith("0x") and len(wallet) == 42:
        return wallet.lower()
    return wallet


@router.get("/resolve")
async def resolve_identity(
    supabase_user_id: Optional[uuid.UUID] = Query(None),
    nwo_did: Optional[str] = Query(None),
    cardiac_root_token_id: Optional[str] = Query(None),
    cardiac_hash: Optional[str] = Query(None),
    primary_wallet: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    provided = [
        ("supabase_user_id", supabase_user_id),
        ("nwo_did", nwo_did),
        ("cardiac_root_token_id", cardiac_root_token_id),
        ("cardiac_hash", cardiac_hash),
        ("primary_wallet", _normalize_wallet(primary_wallet)),
    ]
    non_null = [(k, v) for k, v in provided if v is not None]

    if len(non_null) == 0:
        raise HTTPException(400, "Provide exactly one anchor query parameter")
    if len(non_null) > 1:
        raise HTTPException(
            400,
            f"Provide exactly ONE anchor parameter; got: {[k for k, _ in non_null]}",
        )

    column_name, value = non_null[0]
    column = getattr(Identity, column_name)

    result = await session.execute(select(Identity).where(column == value))
    identity = result.scalar_one_or_none()

    if identity is None:
        raise HTTPException(404, f"No identity found where {column_name} = {value}")

    return identity.to_dict()


@router.get("/{identity_id}")
async def get_identity(
    identity_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Identity).where(Identity.id == identity_id)
    )
    identity = result.scalar_one_or_none()
    if identity is None:
        raise HTTPException(404, "Identity not found")
    return identity.to_dict()


@router.get("/{identity_id}/owned")
async def list_owned(
    identity_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Identity).where(Identity.owned_by == identity_id)
    )
    owned = result.scalars().all()
    return {"total": len(owned), "identities": [i.to_dict() for i in owned]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_identity(
    body: IdentityCreate,
    _: bool = Depends(require_service_key),
    session: AsyncSession = Depends(get_session),
):
    wallet = _normalize_wallet(body.primary_wallet)

    conflict_filters = []
    if body.supabase_user_id:
        conflict_filters.append(Identity.supabase_user_id == body.supabase_user_id)
    if body.nwo_did:
        conflict_filters.append(Identity.nwo_did == body.nwo_did)
    if body.cardiac_root_token_id:
        conflict_filters.append(Identity.cardiac_root_token_id == body.cardiac_root_token_id)
    if body.cardiac_hash:
        conflict_filters.append(Identity.cardiac_hash == body.cardiac_hash)
    if wallet:
        conflict_filters.append(Identity.primary_wallet == wallet)

    if conflict_filters:
        existing = await session.execute(select(Identity).where(or_(*conflict_filters)))
        conflicting = existing.scalar_one_or_none()
        if conflicting:
            raise HTTPException(
                409,
                f"Anchor already claimed by identity {conflicting.id} "
                f"(type: {conflicting.identity_type}). Use PATCH to link to existing.",
            )

    if body.owned_by:
        owner = await session.execute(select(Identity).where(Identity.id == body.owned_by))
        if owner.scalar_one_or_none() is None:
            raise HTTPException(400, f"owned_by identity {body.owned_by} does not exist")

    identity = Identity(
        supabase_user_id=body.supabase_user_id,
        nwo_did=body.nwo_did,
        cardiac_root_token_id=body.cardiac_root_token_id,
        cardiac_hash=body.cardiac_hash,
        primary_wallet=wallet,
        identity_type=body.identity_type.value,
        display_name=body.display_name,
        owned_by=body.owned_by,
        metadata_=body.metadata,
    )
    session.add(identity)
    await session.commit()
    await session.refresh(identity)
    return identity.to_dict()


@router.patch("/{identity_id}")
async def update_identity(
    identity_id: uuid.UUID,
    body: IdentityUpdate,
    _: bool = Depends(require_service_key),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Identity).where(Identity.id == identity_id))
    identity = result.scalar_one_or_none()
    if identity is None:
        raise HTTPException(404, "Identity not found")

    conflict_filters = []
    if body.supabase_user_id and body.supabase_user_id != identity.supabase_user_id:
        conflict_filters.append(Identity.supabase_user_id == body.supabase_user_id)
    if body.nwo_did and body.nwo_did != identity.nwo_did:
        conflict_filters.append(Identity.nwo_did == body.nwo_did)
    if body.cardiac_root_token_id and body.cardiac_root_token_id != identity.cardiac_root_token_id:
        conflict_filters.append(Identity.cardiac_root_token_id == body.cardiac_root_token_id)
    if body.cardiac_hash and body.cardiac_hash != identity.cardiac_hash:
        conflict_filters.append(Identity.cardiac_hash == body.cardiac_hash)
    wallet = _normalize_wallet(body.primary_wallet)
    if wallet and wallet != identity.primary_wallet:
        conflict_filters.append(Identity.primary_wallet == wallet)

    if conflict_filters:
        existing = await session.execute(
            select(Identity).where(or_(*conflict_filters), Identity.id != identity_id)
        )
        conflict = existing.scalar_one_or_none()
        if conflict:
            raise HTTPException(
                409,
                f"Anchor already claimed by a different identity ({conflict.id})",
            )

    for field in (
        "supabase_user_id", "nwo_did", "cardiac_root_token_id",
        "cardiac_hash", "display_name", "owned_by"
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(identity, field, value)

    if wallet is not None:
        identity.primary_wallet = wallet
    if body.metadata is not None:
        identity.metadata_ = {**(identity.metadata_ or {}), **body.metadata}

    await session.commit()
    await session.refresh(identity)
    return identity.to_dict()
