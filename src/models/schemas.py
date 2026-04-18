"""Pydantic schemas for the NWO API Gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Agent DID ─────────────────────────────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    public_key: str = Field(..., description="PEM or hex-encoded ed25519 public key")
    description: str | None = None
    robot_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAuthRequest(BaseModel):
    did: str
    nonce: str = Field(..., description="Random nonce to sign")
    signature: str = Field(..., description="hex-encoded ed25519 signature of the nonce")


class AgentAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    did: str


class AgentResponse(BaseModel):
    id: str
    did: str
    name: str
    description: str | None
    robot_type: str | None
    is_active: bool
    is_robot: bool
    parts_published: int
    skills_published: int
    parts_downloaded: int
    skills_executed: int
    print_jobs_submitted: int
    created_at: datetime
    last_seen_at: datetime
    model_config = {"from_attributes": True}


# ── Graph ─────────────────────────────────────────────────────────────────────

class GraphNodeCreate(BaseModel):
    node_type: str = Field(..., description="e.g. design, part_published, sensor_reading, capability")
    title: str = Field(..., min_length=1, max_length=256)
    body: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    is_public: bool = True

    # Optional cross-layer references
    layer1_job_id: str | None = None
    layer2_part_id: str | None = None
    layer3_job_id: str | None = None
    layer4_skill_id: str | None = None


class GraphNodeResponse(BaseModel):
    id: str
    agent_id: str
    agent_did: str
    agent_name: str
    node_type: str
    title: str
    body: str | None
    data: dict[str, Any]
    tags: list[str]
    is_public: bool
    layer1_job_id: str | None
    layer2_part_id: str | None
    layer3_job_id: str | None
    layer4_skill_id: str | None
    created_at: datetime
    edge_count: int = 0


class GraphEdgeCreate(BaseModel):
    source_node_id: str
    target_node_id: str
    relation: str
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphQueryResponse(BaseModel):
    total: int
    nodes: list[GraphNodeResponse]


# ── Token economy ─────────────────────────────────────────────────────────────

class TokenBalanceResponse(BaseModel):
    did: str
    balance: int
    total_earned: int
    total_spent: int
    updated_at: datetime


class TokenTransactionResponse(BaseModel):
    id: str
    amount: int
    reason: str
    reference_id: str | None
    balance_after: int
    created_at: datetime
    model_config = {"from_attributes": True}


class TokenLedgerResponse(BaseModel):
    did: str
    balance: int
    transactions: list[TokenTransactionResponse]


class TokenTransferRequest(BaseModel):
    from_did: str
    to_did: str
    amount: int = Field(..., ge=1)
    reason: str = Field(default="manual_transfer", max_length=128)


# ── Health ────────────────────────────────────────────────────────────────────

class LayerHealth(BaseModel):
    layer: int
    name: str
    url: str
    status: str    # "ok" | "degraded" | "unreachable"
    latency_ms: float | None


class PlatformHealth(BaseModel):
    status: str
    layers: list[LayerHealth]
    total_agents: int
    total_graph_nodes: int
    checked_at: datetime
