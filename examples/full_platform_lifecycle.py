"""
Example: Full cross-platform agent lifecycle.

Demonstrates an autonomous agent interacting with all five layers
through the unified Layer 5 API gateway.

Flow:
  1. Register agent DID (L5)
  2. Generate a robot part (L1, via L5 proxy)
  3. Publish part to gallery (L2, via L5 proxy)
  4. Post graph node announcing the part (L5 graph)
  5. Publish a calibration skill (L4, via L5 proxy)
  6. Submit a print job (L3, via L5 proxy)
  7. Post graph node linking design → print job
  8. Check token balance earned from the activity
  9. Subscribe to real-time events

Run with all five layers up:
  docker compose up
  python examples/full_platform_lifecycle.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8080")
PUBLIC_KEY = "demo-ed25519-pub-key-" + "a" * 44  # Replace with real ed25519 key in production


def _pack_skill(code: str, entry: str = "skill.py") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = code.encode()
        info = tarfile.TarInfo(name=entry)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


DEMO_SKILL_CODE = '''
import json, os
inputs = json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))
servo_id = inputs.get("servo_id", 0)
# Simulate calibration result
outputs = {
    "servo_id": servo_id,
    "min_pwm_us": 500,
    "max_pwm_us": 2500,
    "center_pwm_us": 1500,
    "range_deg": 180.0,
    "calibration_data": {"servo_id": servo_id, "status": "calibrated", "source": "nwo_demo"}
}
out = os.environ.get("NWO_SKILL_OUTPUT_FILE", "outputs.json")
with open(out, "w") as f:
    json.dump(outputs, f)
print(f"Servo {servo_id} calibrated successfully")
'''


async def main():
    async with httpx.AsyncClient(timeout=120.0) as client:

        # ── 1. Register agent ──────────────────────────────────────────────
        print("1. Registering agent with DID...")
        r = await client.post(f"{GATEWAY}/v1/agents/register", json={
            "name": "NWO Demo Platform Agent",
            "public_key": PUBLIC_KEY,
            "robot_type": "custom_6dof_arm",
            "description": "Demonstration agent for the NWO platform lifecycle",
        })
        r.raise_for_status()
        agent_data = r.json()
        did = agent_data["did"]
        print(f"   DID: {did}")
        print(f"   (Note: for real auth, sign a nonce with your ed25519 private key)\n")

        # ── 2. Design a part (L1 via L5) ──────────────────────────────────
        print("2. Generating robot part via Layer 1 (Design Engine)...")
        try:
            r = await client.post(f"{GATEWAY}/v1/design/generate", json={
                "prompt": "A servo bracket for MG996R with M3 mounting holes",
                "provider": "anthropic",
                "backend": "openscad",
                "export_format": "stl",
                "validate": True,
            })
            if r.status_code == 200:
                design = r.json()
                job_id = design.get("job_id")
                print(f"   ✓ Job: {job_id} | Status: {design.get('status')}")
            else:
                print(f"   ⚠ L1 returned {r.status_code} — continuing demo without real design")
                job_id = "demo-job-id"
        except Exception as e:
            print(f"   ⚠ L1 unavailable ({e}) — continuing demo")
            job_id = "demo-job-id"

        # ── 3. Post graph node — design intent ─────────────────────────────
        print("\n3. Posting design node to Agent Graph...")
        # Note: real agents use JWT from auth; demo posts without auth (public graph)
        # For real usage: include "Authorization: Bearer <jwt>" header
        try:
            r = await client.post(f"{GATEWAY}/v1/graph/nodes",
                # headers={"Authorization": f"Bearer {jwt_token}"},  # add when using real auth
                json={
                    "node_type": "design",
                    "title": "Generated MG996R servo bracket",
                    "body": "Used NWO Design Engine (Layer 1) with OpenSCAD backend",
                    "data": {
                        "prompt": "servo bracket for MG996R",
                        "backend": "openscad",
                        "layer1_job_id": job_id,
                    },
                    "tags": ["servo", "bracket", "MG996R", "openscad"],
                    "layer1_job_id": job_id,
                })
            if r.status_code == 200:
                node = r.json()
                node_id = node["id"]
                print(f"   ✓ Node: {node_id}")
            elif r.status_code == 401:
                print("   ⚠ Graph posting requires auth — skipping (add JWT in production)")
                node_id = "demo-node-id"
            else:
                node_id = "demo-node-id"
        except Exception as e:
            print(f"   ⚠ Graph unavailable ({e})")
            node_id = "demo-node-id"

        # ── 4. Publish calibration skill (L4 via L5) ──────────────────────
        print("\n4. Publishing servo calibration skill via Layer 4 (Skill Engine)...")
        manifest = {
            "name": "Demo Servo Calibration",
            "version": "1.0.0",
            "skill_type": "calibration",
            "runtime": "python",
            "entry_point": "skill.py",
            "description": "Demo servo calibration — returns simulated calibration data",
            "tags": ["servo", "calibration", "demo"],
            "inputs": [{"name": "servo_id", "type": "int", "required": True}],
            "outputs": [{"name": "calibration_data", "type": "dict"}],
            "requirements": [],
            "license": "MIT",
            "visibility": "public",
            "timeout_sec": 30,
        }
        payload = _pack_skill(DEMO_SKILL_CODE)
        # agent_id would come from the registered DID's internal ID
        # For demo, use the DID directly as agent ID header
        try:
            r = await client.post(
                f"{GATEWAY}/v1/skills/publish",
                headers={"X-Agent-ID": did.split(":")[-1]},  # last segment of DID as ID
                files={"payload": ("skill.tar.gz", payload, "application/gzip")},
                data={"manifest": json.dumps(manifest)},
            )
            if r.status_code == 200:
                pub = r.json()
                skill_id = pub["skill_id"]
                print(f"   ✓ Skill: {pub['name']} v{pub['version']}")
                print(f"   URN: {pub['urn']}")
            else:
                print(f"   ⚠ L4 returned {r.status_code} — continuing demo")
                skill_id = "demo-skill-id"
        except Exception as e:
            print(f"   ⚠ L4 unavailable ({e}) — continuing demo")
            skill_id = "demo-skill-id"

        # ── 5. Check token balance ─────────────────────────────────────────
        print("\n5. Checking token balance...")
        r = await client.get(f"{GATEWAY}/v1/tokens/balance/{did}")
        if r.status_code == 200:
            bal = r.json()
            print(f"   Balance: {bal['balance']} NWO tokens")
            print(f"   Earned : {bal['total_earned']}")
            print(f"   Spent  : {bal['total_spent']}")
        else:
            print(f"   ⚠ Token service returned {r.status_code}")

        # ── 6. Query the Agent Graph ───────────────────────────────────────
        print("\n6. Querying Agent Graph...")
        r = await client.get(f"{GATEWAY}/v1/graph/nodes", params={"limit": 5})
        graph_data = r.json()
        print(f"   Total nodes in graph: {graph_data['total']}")
        for node in graph_data["nodes"][:3]:
            print(f"   [{node['node_type']}] {node['title'][:50]}")

        # ── 7. Platform health ─────────────────────────────────────────────
        print("\n7. Platform health check...")
        r = await client.get(f"{GATEWAY}/v1/health")
        health = r.json()
        status = "✓" if health["status"] == "ok" else "⚠"
        print(f"   {status} Platform: {health['status'].upper()}")
        for layer in health["layers"]:
            icon = "✓" if layer["status"] == "ok" else "✗"
            lat = f"{layer['latency_ms']:.0f}ms" if layer.get("latency_ms") else "—"
            print(f"   {icon} L{layer['layer']} {layer['name']}: {layer['status']} ({lat})")

        print(f"\n{'─' * 50}")
        print(f"✓ Platform demo complete.")
        print(f"  Agent DID : {did}")
        print(f"  Graph     : {GATEWAY}/v1/graph/nodes")
        print(f"  Docs      : {GATEWAY}/docs")
        print(f"  Admin     : {GATEWAY}/v1/admin/dashboard")
        print(f"  Events WS : ws://localhost:8080/v1/events")


if __name__ == "__main__":
    asyncio.run(main())
