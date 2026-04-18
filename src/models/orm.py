"""
SQLAlchemy ORM models for the NWO API Gateway.

Tables:
  agent_dids      — W3C DID-inspired agent identities
  graph_nodes     — the NWO Agent Graph (shared knowledge graph)
  graph_edges     — directed relationships between graph nodes
  token_accounts  — current token balances per agent
  token_ledger    — immutable credit/debit transaction log
  api_keys        — per-agent API keys (hashed)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ── Agent DID ─────────────────────────────────────────────────────────────────

class AgentDID(Base):
    """
    W3C DID-inspired agent identity.
    DID format: did:nwo:{agent_id}
    The agent controls the identity via their ed25519 private key.
    """
    __tablename__ = "agent_dids"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    did: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    # e.g. "did:nwo:abc123"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    key_algorithm: Mapped[str] = mapped_column(String(16), default="ed25519")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_robot: Mapped[bool] = mapped_column(Boolean, default=True)
    robot_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # e.g. "unitree_g1", "custom_6dof_arm", "voron_printer"

    # Stats aggregated from L1–L4
    parts_published: Mapped[int] = mapped_column(Integer, default=0)
    skills_published: Mapped[int] = mapped_column(Integer, default=0)
    parts_downloaded: Mapped[int] = mapped_column(Integer, default=0)
    skills_executed: Mapped[int] = mapped_column(Integer, default=0)
    print_jobs_submitted: Mapped[int] = mapped_column(Integer, default=0)

    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    graph_nodes: Mapped[list["GraphNode"]] = relationship("GraphNode", back_populates="agent")
    token_account: Mapped["TokenAccount"] = relationship("TokenAccount", back_populates="agent", uselist=False)
    api_keys: Mapped[list["ApiKey"]] = relationship("ApiKey", back_populates="agent")

    def __repr__(self) -> str:
        return f"<AgentDID did={self.did} name={self.name}>"


# ── NWO Agent Graph ────────────────────────────────────────────────────────────

class GraphNode(Base):
    """
    A node in the NWO Agent Graph.
    Agents post nodes to share knowledge: design decisions, sensor readings,
    capability announcements, coordination messages.
    """
    __tablename__ = "graph_nodes"
    __table_args__ = (
        Index("ix_nodes_agent_id", "agent_id"),
        Index("ix_nodes_node_type", "node_type"),
        Index("ix_nodes_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_dids.id"), nullable=False)

    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "design", "part_published", "skill_published", "sensor_reading",
    #      "capability", "intent", "observation", "print_job", "coordination"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Structured payload — varies by node_type
    # For "part_published": {"part_id": "...", "part_name": "...", "file_format": "stl"}
    # For "sensor_reading": {"sensor": "imu", "values": {...}}
    # For "capability": {"skill_id": "...", "skill_name": "..."}
    # For "intent": {"goal": "print bracket", "layer1_prompt": "..."}

    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)

    # Cross-layer references
    layer1_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    layer2_part_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    layer3_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    layer4_skill_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent: Mapped["AgentDID"] = relationship("AgentDID", back_populates="graph_nodes")
    outgoing_edges: Mapped[list["GraphEdge"]] = relationship(
        "GraphEdge", foreign_keys="GraphEdge.source_node_id", back_populates="source"
    )
    incoming_edges: Mapped[list["GraphEdge"]] = relationship(
        "GraphEdge", foreign_keys="GraphEdge.target_node_id", back_populates="target"
    )


class GraphEdge(Base):
    """Directed edge between two graph nodes."""
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("source_node_id", "target_node_id", "relation", name="uq_edge"),
        Index("ix_edges_source", "source_node_id"),
        Index("ix_edges_target", "target_node_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("graph_nodes.id"), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("graph_nodes.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "depends_on", "extends", "uses_skill", "printed_from", "calibrated_by"
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    source: Mapped["GraphNode"] = relationship("GraphNode", foreign_keys=[source_node_id], back_populates="outgoing_edges")
    target: Mapped["GraphNode"] = relationship("GraphNode", foreign_keys=[target_node_id], back_populates="incoming_edges")


# ── Token Economy ──────────────────────────────────────────────────────────────

class TokenAccount(Base):
    """Current token balance for an agent."""
    __tablename__ = "token_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_dids.id"), nullable=False, unique=True)
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_earned: Mapped[int] = mapped_column(Integer, default=0)
    total_spent: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent: Mapped["AgentDID"] = relationship("AgentDID", back_populates="token_account")
    transactions: Mapped[list["TokenTransaction"]] = relationship("TokenTransaction", back_populates="account")


class TokenTransaction(Base):
    """Immutable token credit/debit ledger entry."""
    __tablename__ = "token_ledger"
    __table_args__ = (
        Index("ix_ledger_account_id", "account_id"),
        Index("ix_ledger_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("token_accounts.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # Positive = credit (earned), negative = debit (spent)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    # e.g. "part_download", "skill_execution", "design_generation", "registration_bonus"
    reference_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Part ID, skill ID, job ID etc.
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    account: Mapped["TokenAccount"] = relationship("TokenAccount", back_populates="transactions")


# ── API Keys ──────────────────────────────────────────────────────────────────

class ApiKey(Base):
    """Per-agent API keys (bcrypt-hashed)."""
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_dids.id"), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent: Mapped["AgentDID"] = relationship("AgentDID", back_populates="api_keys")
