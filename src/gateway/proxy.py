"""
Reverse proxy gateway.
Transparently forwards requests to L1–L4 services,
injecting auth headers and optionally deducting tokens.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

_LAYER_URLS = {
    1: os.getenv("LAYER1_URL", "http://localhost:8000"),
    2: os.getenv("LAYER2_URL", "http://localhost:8001"),
    3: os.getenv("LAYER3_URL", "http://localhost:8002"),
    4: os.getenv("LAYER4_URL", "http://localhost:8003"),
}

_client = httpx.AsyncClient(timeout=120.0, follow_redirects=True)


async def proxy_request(
    layer: int,
    path: str,
    request: Request,
    agent_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """
    Forward a request to the given layer service, streaming the response back.

    Args:
        layer: Layer number (1-4).
        path: Downstream path (e.g. "/design/generate").
        request: Original FastAPI request.
        agent_id: If provided, injects X-Agent-ID header.
        extra_headers: Additional headers to inject.
    """
    base_url = _LAYER_URLS.get(layer)
    if not base_url:
        raise HTTPException(status_code=500, detail=f"Layer {layer} URL not configured")

    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    # Forward all headers except Host
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    if agent_id:
        headers["X-Agent-ID"] = agent_id
    if extra_headers:
        headers.update(extra_headers)

    body = await request.body()

    upstream_resp = await _client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=body,
        params=dict(request.query_params),
    )

    return StreamingResponse(
        content=upstream_resp.aiter_bytes(),
        status_code=upstream_resp.status_code,
        headers=dict(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type", "application/json"),
    )


async def check_layer_health(layer: int) -> dict[str, Any]:
    """Ping a layer's /health endpoint and return status + latency."""
    base_url = _LAYER_URLS.get(layer)
    names = {1: "Design Engine", 2: "Parts Gallery", 3: "Printer Connectors", 4: "Skill Engine"}
    if not base_url:
        return {"layer": layer, "name": names.get(layer, "Unknown"),
                "url": "", "status": "unconfigured", "latency_ms": None}
    t0 = time.monotonic()
    try:
        r = await _client.get(f"{base_url.rstrip('/')}/health", timeout=5.0)
        latency = (time.monotonic() - t0) * 1000
        status = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        latency = None
        status = "unreachable"
    return {
        "layer": layer, "name": names.get(layer, "Unknown"),
        "url": base_url, "status": status,
        "latency_ms": round(latency, 1) if latency else None,
    }
