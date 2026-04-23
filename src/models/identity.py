"""Cross-system identity hub model."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class IdentityType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    ROBOT = "robot"


class Identity(Base):
    __tablename__ = "identities"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('human','agent','robot')",
            name="identities_type_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    supabase_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True
    )
    nwo_did: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    cardiac_root_token_id: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    cardiac_hash: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    primary_wallet: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)

    identity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owned_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identities.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner = relationship(
        "Identity", remote_side="Identity.id", foreign_keys=[owned_by],
        backref="owned_children",
    )

    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "supabase_user_id": str(self.supabase_user_id) if self.supabase_user_id else None,
            "nwo_did": self.nwo_did,
            "cardiac_root_token_id": self.cardiac_root_token_id,
            "cardiac_hash": self.cardiac_hash,
            "primary_wallet": self.primary_wallet,
            "identity_type": self.identity_type,
            "display_name": self.display_name,
            "owned_by": str(self.owned_by) if self.owned_by else None,
            "metadata": self.metadata_,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
