"""NWO Robotics API Gateway CLI."""

from __future__ import annotations

import asyncio
import os

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def cli():
    """NWO Robotics API Gateway — Layer 5."""


@cli.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--reload", is_flag=True)
def serve(host, port, reload):
    """Start the unified API gateway."""
    import uvicorn
    _host = host or os.getenv("API_HOST", "0.0.0.0")
    _port = port or int(os.getenv("API_PORT", "8080"))
    console.print(f"\n[bold]NWO Robotics API Gateway[/bold] → http://{_host}:{_port}")
    console.print(f"  Docs    : http://{_host}:{_port}/docs")
    console.print(f"  Health  : http://{_host}:{_port}/v1/health")
    console.print(f"  Events  : ws://{_host}:{_port}/v1/events")
    console.print(f"  Admin   : http://{_host}:{_port}/v1/admin/dashboard\n")
    uvicorn.run("src.api.main:app", host=_host, port=_port, reload=reload)


@cli.command()
def health():
    """Check health of all platform layers."""
    asyncio.run(_health())


async def _health():
    import httpx
    port = int(os.getenv("API_PORT", "8080"))
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"http://localhost:{port}/v1/health")
            data = r.json()
        except Exception as e:
            console.print(f"[red]Gateway unreachable: {e}[/red]")
            return

    overall = "[green]OK[/green]" if data["status"] == "ok" else "[yellow]DEGRADED[/yellow]"
    console.print(f"\nPlatform status: {overall}")
    console.print(f"  Agents      : {data['total_agents']}")
    console.print(f"  Graph nodes : {data['total_graph_nodes']}")
    console.print(f"  WS conns    : {data['ws_connections']}\n")

    t = Table(title="Layer health")
    t.add_column("Layer"); t.add_column("Name"); t.add_column("Status"); t.add_column("Latency")
    for layer in data["layers"]:
        status_str = {"ok": "[green]OK[/green]", "degraded": "[yellow]DEGRADED[/yellow]",
                      "unreachable": "[red]UNREACHABLE[/red]"}.get(layer["status"], layer["status"])
        lat = f"{layer['latency_ms']:.0f} ms" if layer.get("latency_ms") else "—"
        t.add_row(str(layer["layer"]), layer["name"], status_str, lat)
    console.print(t)


@cli.command()
@click.argument("name")
@click.option("--public-key", required=True, help="Hex or PEM ed25519 public key")
@click.option("--robot-type", default=None)
@click.option("--api", default="http://localhost:8080")
def register(name, public_key, robot_type, api):
    """Register a new agent identity."""
    asyncio.run(_register(name, public_key, robot_type, api))


async def _register(name, public_key, robot_type, api_url):
    import httpx
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{api_url}/v1/agents/register", json={
            "name": name,
            "public_key": public_key,
            "robot_type": robot_type,
        })
        data = r.json()
    console.print(f"\n[green]✓[/green] Agent registered")
    console.print(f"  DID  : {data.get('did')}")
    console.print(f"  Name : {data.get('name')}")


@cli.command()
@click.argument("did")
@click.option("--api", default="http://localhost:8080")
def balance(did, api):
    """Check token balance for an agent DID."""
    asyncio.run(_balance(did, api))


async def _balance(did, api_url):
    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{api_url}/v1/tokens/balance/{did}")
        if r.status_code == 404:
            console.print("[red]Agent not found[/red]"); return
        data = r.json()
    console.print(f"\n  DID           : {did}")
    console.print(f"  Balance       : [green]{data['balance']}[/green] NWO tokens")
    console.print(f"  Total earned  : {data['total_earned']}")
    console.print(f"  Total spent   : {data['total_spent']}")


@cli.command()
@click.option("--node-type", default=None)
@click.option("--agent-did", default=None)
@click.option("--limit", default=20, type=int)
@click.option("--api", default="http://localhost:8080")
def graph(node_type, agent_did, limit, api):
    """Query the NWO Agent Graph."""
    asyncio.run(_graph(node_type, agent_did, limit, api))


async def _graph(node_type, agent_did, limit, api_url):
    import httpx
    params = {"limit": limit}
    if node_type: params["node_type"] = node_type
    if agent_did: params["agent_did"] = agent_did

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{api_url}/v1/graph/nodes", params=params)
        data = r.json()

    t = Table(title=f"Agent Graph ({data['total']} nodes)")
    t.add_column("Type"); t.add_column("Title"); t.add_column("Agent"); t.add_column("Time")
    for n in data["nodes"]:
        t.add_row(n["node_type"], n["title"][:50], n["agent_did"][:20],
                  n["created_at"][:19].replace("T", " "))
    console.print(t)


if __name__ == "__main__":
    cli()
