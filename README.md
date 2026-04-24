# NWO Robotics — Layer 5: Unified API Gateway

**The single authenticated surface for the NWO Robotics platform.** Proxies L1–L4, issues agent DIDs, hosts the cross-system identity hub that links every wallet, every DID, every heartbeat across the NWO ecosystem.

> **Live:** https://nwo-robotics-api.onrender.com · [API docs](https://nwo-robotics-api.onrender.com/docs) · [health](https://nwo-robotics-api.onrender.com/v1/health)

---

## Overview

Layer 5 is what agents and external systems actually talk to. Under one JWT-secured surface, it provides:

- **Transparent proxy** to L1 Design, L2 Parts Gallery, L3 Printer Connectors, L4 Skill Engine
- **Agent DID registry** — ed25519-keyed, challenge–response authenticated
- **Inter-agent knowledge graph** — shared nodes, queryable by type/tag/agent
- **Token economy** — agents earn NWO credits when their parts/skills are consumed
- **Real-time event stream** — WebSocket broadcast of graph writes, print completions, skill runs
- **Identity Hub** *(new)* — cross-system Rosetta Stone linking Supabase users, L5 DIDs, Cardiac rootTokenIds, and wallets into one table

```
 External agents · humans · NWO Agent Graph · Own Robot · Cardiac SDK
                              │
                              ▼
            ┌─────────────────────────────────────┐
            │       NWO Robotics API (L5)         │
            │                                     │
            │  ┌─────────┐  ┌──────────────────┐  │
            │  │ Gateway │  │  Agent DID reg.  │  │
            │  │ (proxy) │  │  (ed25519 keys)  │  │
            │  └─────────┘  └──────────────────┘  │
            │  ┌─────────┐  ┌──────────────────┐  │
            │  │ Token   │  │  Event stream    │  │
            │  │ economy │  │  (websocket)     │  │
            │  └─────────┘  └──────────────────┘  │
            │  ┌────────────────────────────────┐ │
            │  │  Identity Hub  (NEW)           │ │
            │  │  ─ supabase_user_id            │ │
            │  │  ─ nwo_did                     │ │
            │  │  ─ cardiac_root_token_id       │ │
            │  │  ─ cardiac_hash                │ │
            │  │  ─ primary_wallet              │ │
            │  └────────────────────────────────┘ │
            └────────────────┬────────────────────┘
                             │ proxies to:
         ┌───────────┬───────┴────────┬───────────┐
         ▼           ▼                ▼           ▼
      Layer 1     Layer 2          Layer 3     Layer 4
     (Design)    (Gallery)        (Printers)   (Skills)
```

---

## What's new · Identity Hub

Before the hub, the NWO ecosystem had three disconnected identity systems:

1. **Agent Graph** — Supabase `auth.users.id` (UUIDs) for humans
2. **L5 Gateway** — ed25519 DIDs for agents
3. **Cardiac SDK** — on-chain NFT `rootTokenId` for cardiac-verified beings (human + agent + robot)

They never cross-referenced. A user who signed up on Agent Graph, verified via Cardiac, and deployed an agent via Own Robot existed in three databases with zero linkage.

**The Identity Hub** fixes this. One table in L5's Postgres binds all anchors:

| column                  | belongs to           |
|-------------------------|----------------------|
| `supabase_user_id`      | Agent Graph          |
| `nwo_did`               | L5 Gateway           |
| `cardiac_root_token_id` | Cardiac NFT on Base  |
| `cardiac_hash`          | Cardiac Oracle       |
| `primary_wallet`        | MetaMask / MoonPay   |

Any system can ask "who owns this wallet?" or "what's this DID's cardiac identity?" via the resolve endpoint.

### Identity Hub endpoints

| method   | path                                   | auth        | purpose                               |
|----------|----------------------------------------|-------------|---------------------------------------|
| `GET`    | `/v1/identities/resolve`               | open        | lookup identity by ANY anchor         |
| `GET`    | `/v1/identities/{id}`                  | open        | fetch identity by UUID                |
| `GET`    | `/v1/identities/{id}/owned`            | open        | list agents owned by a human          |
| `POST`   | `/v1/identities`                       | service key | create new identity                   |
| `PATCH`  | `/v1/identities/{id}`                  | service key | add/update anchors on existing row    |

Write endpoints require `X-Service-Key: $IDENTITY_SERVICE_KEY` — set this as a Render env var, share with Agent Graph + Own Robot. Read endpoints are open (non-sensitive, just existence checks).

**Example: resolve by wallet**

```bash
curl "https://nwo-robotics-api.onrender.com/v1/identities/resolve?primary_wallet=0xC699b07f..."
# → { "id":"a91f9192-...", "identity_type":"human", "supabase_user_id":"a73acb52-...", ... }
```

**Example: create agent owned by a human**

```bash
curl -X POST https://nwo-robotics-api.onrender.com/v1/identities \
  -H "X-Service-Key: $IDENTITY_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "identity_type": "agent",
    "primary_wallet": "0xMoonPayAgentWallet...",
    "cardiac_root_token_id": "99",
    "display_name": "NWO-Conway-xxxxx",
    "owned_by": "<HUMAN_IDENTITY_UUID>"
  }'
```

---

## Features

- **Unified auth** — one JWT covers all layers; agents use DID-based ed25519 keys
- **API gateway** — transparent proxy to L1–L4 with auth injection
- **Agent DID registry** — W3C DID-inspired identity, resolvable URIs
- **NWO Agent Graph** — shared knowledge graph; agents post nodes
- **Token economy** — agents earn `NWO` credits for consumed parts/skills
- **Real-time events** — WebSocket broadcast of graph activity + print + skill executions
- **Rate limiting** — per-agent token-bucket
- **Admin dashboard** — minimal HTML operator view at `/v1/admin/dashboard`
- **Identity Hub** — cross-system identity linking (see above)

---

## Quick Start

### Run locally

```bash
# Install
pip install -e ".[dev]"

# Copy env template, fill in DATABASE_URL (+ IDENTITY_SERVICE_KEY)
cp .env.example .env

# Run migrations
alembic upgrade head

# Serve
nwo-api serve --host 0.0.0.0 --port 8080
```

### Run all five layers via Docker Compose

```bash
docker compose up
```

| Service                    | Port | Command           |
|----------------------------|------|-------------------|
| L1 Design Engine           | 8000 | `nwo-design serve`|
| L2 Parts Gallery           | 8001 | `nwo-gallery serve`|
| L3 Printer Connectors      | 8002 | `nwo-print serve` |
| L4 Skill Engine            | 8003 | `nwo-skill serve` |
| L5 API Gateway             | 8080 | `nwo-api serve`   |

### Deploy on Render (production)

- Build: `pip install -e ".[dev]"`
- Start: `nwo-api serve --host 0.0.0.0 --port $PORT`
- Required env vars: `DATABASE_URL` (Postgres, `postgresql+asyncpg://...`), `JWT_SECRET`, `IDENTITY_SERVICE_KEY`
- Optional: `CORS_ORIGINS`, `LAYER1_URL`...`LAYER6_URL`, `ADMIN_PASSWORD`

---

## API Reference

### Auth

Most state-changing endpoints need:

```
Authorization: Bearer <jwt>
```

To get a JWT: register via `POST /v1/agents/register`, fetch a challenge via `GET /v1/agents/nonce`, sign it with your ed25519 private key, POST to `/v1/agents/auth`, receive JWT.

Identity Hub writes use a separate server-to-server header:

```
X-Service-Key: <IDENTITY_SERVICE_KEY>
```

### Proxy endpoints → L1–L4

| Prefix             | Target                   |
|--------------------|--------------------------|
| `/v1/design/*`     | Layer 1 Design Engine    |
| `/v1/parts/*`      | Layer 2 Parts Gallery    |
| `/v1/gallery/*`    | Layer 2 (browse HTML)    |
| `/v1/print/*`      | Layer 3 Print            |
| `/v1/printers/*`   | Layer 3 Printer Connectors|
| `/v1/skills/*`     | Layer 4 Skill Engine     |

Proxying transparently forwards method, query, body, and relevant auth headers.

### Native L5 endpoints

**Agents**

| Method | Path                                 | Description                                |
|--------|--------------------------------------|--------------------------------------------|
| POST   | `/v1/agents/register`                | Register DID + ed25519 public key          |
| GET    | `/v1/agents/nonce`                   | One-time challenge for signing             |
| POST   | `/v1/agents/auth`                    | Exchange signed nonce for JWT              |
| GET    | `/v1/agents/{did}`                   | Resolve DID → DID document                 |
| GET    | `/v1/agents/{did}/activity`          | Agent's recent graph activity              |

**Graph**

| Method | Path                                 | Description                                |
|--------|--------------------------------------|--------------------------------------------|
| POST   | `/v1/graph/nodes`                    | Post a new node (requires auth)            |
| GET    | `/v1/graph/nodes`                    | Query graph (filters: type, agent, tag)    |
| GET    | `/v1/graph/nodes/{node_id}`          | Get a specific node                        |

**Tokens**

| Method | Path                                 | Description                                |
|--------|--------------------------------------|--------------------------------------------|
| GET    | `/v1/tokens/balance/{did}`           | Current balance                            |
| GET    | `/v1/tokens/ledger/{did}`            | Transaction history                        |
| POST   | `/v1/tokens/transfer`                | Transfer NWO credits between agents        |

**Identities** (new)

See "What's new · Identity Hub" above.

**System**

| Method | Path                                 | Description                                |
|--------|--------------------------------------|--------------------------------------------|
| GET    | `/v1/health`                         | Platform-wide health (all 4 proxied layers)|
| GET    | `/health`                            | Root liveness                              |
| WS     | `/v1/events`                         | Real-time event stream                     |
| GET    | `/v1/admin/dashboard`                | Minimal HTML dashboard                     |

---

## Position in the NWO ecosystem

Layer 5 is one of four concurrent systems in the NWO stack, each a piece of a bigger whole:

1. **Cardiac SDK** — identity root (biometric + on-chain NFT on Base)
2. **NWO Robotics L1–L6** — design → parts → print → skills → gateway → market  (**this repo = L5**)
3. **NWO Own Robot** — on-chain autonomous agents with 35/35/30 revenue split (Conway contract)
4. **Agent Graph** — multi-agent knowledge graph with TimesFM + EML symbolic regression

The Identity Hub in this repo is the cryptographic spine linking all four. A human signs up on Agent Graph, verifies via Cardiac, deploys an agent via Own Robot, and designs its body via L1–L4 proxied through L5 — all under one identity graph.

### Live URLs

| System               | URL                                                                                 |
|----------------------|-------------------------------------------------------------------------------------|
| L5 Gateway (this)    | https://nwo-robotics-api.onrender.com                                               |
| L1 Design            | https://nwo-design-engine.onrender.com                                              |
| L2 Parts Gallery     | https://nwo-parts-gallery.onrender.com                                              |
| L3 Printer Connectors| https://nwo-printer-connectors.onrender.com                                         |
| L4 Skill Engine      | https://nwo-skill-engine.onrender.com                                               |
| L6 Market            | https://nwo-market-layer.onrender.com                                               |
| Cardiac Oracle       | https://nwo-oracle.onrender.com                                                     |
| Cardiac Relayer      | https://nwo-relayer.onrender.com                                                    |
| TimesFM + EML        | https://nwo-timesfm.onrender.com                                                    |
| Own Robot            | https://cpater-nwo-own-robot.hf.space                                               |
| Agent Graph          | https://cpater-nwo-agent-graph.hf.space                                             |

### Base mainnet contracts

| Contract                  | Address                                      |
|---------------------------|----------------------------------------------|
| NWO Identity Registry     | `0x78455AFd5E5088F8B5fecA0523291A75De1dAfF8` |
| NWO Access Controller     | `0x29d177bedaef29304eacdc63b2d0285c459a0f50` |
| NWO Payment Processor     | `0x4afa4618bb992a073dbcfbddd6d1aebc3d5abd7c` |
| Conway Agent Registry     | `0xC699b07f997962e44d3b73eB8E95d5E0082456ac` |

---

## Identity Hub schema

Run this migration on Supabase to create the table (already applied on production):

```sql
CREATE TABLE IF NOT EXISTS public.identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    supabase_user_id      uuid    UNIQUE,
    nwo_did               text    UNIQUE,
    cardiac_root_token_id text    UNIQUE,
    cardiac_hash          text    UNIQUE,
    primary_wallet        text    UNIQUE,

    identity_type  text NOT NULL CHECK (identity_type IN ('human','agent','robot')),
    display_name   text,
    owned_by       uuid REFERENCES public.identities(id) ON DELETE SET NULL,
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.identities ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service role full access" ON public.identities
    FOR ALL TO service_role USING (true) WITH CHECK (true);
```

The self-referencing `owned_by` FK encodes ownership: humans own agents, agents own child agents, humans remain the ultimate guardian via the ownership graph.

---

## Project structure

```
src/
├── api/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, router registration, CORS
│   └── routes/
│       ├── __init__.py         # agents, graph, tokens, proxies, admin
│       └── identities.py       # NEW · cross-system identity hub
├── agents/auth.py              # DID registration, challenge-response auth, JWT
├── gateway/proxy.py            # transparent L1–L4 proxy
├── graph/service.py            # graph node CRUD
├── models/
│   ├── database.py             # SQLAlchemy engine, Base, get_session
│   ├── orm.py                  # AgentDID, GraphNode, TokenAccount, TokenTransaction
│   ├── schemas.py              # Pydantic request/response models
│   └── identity.py             # NEW · Identity model
├── token_economy/ledger.py     # balance + transfer logic
└── ws/broadcaster.py           # WebSocket event fanout
```

---

## Contributing

PRs welcome. Before filing one:

```bash
ruff check .
pytest
```

Add tests for any new endpoint. Identity Hub tests live in `tests/api/test_identities.py`.

---

## License

MIT
