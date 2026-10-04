import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PAYLOAD = {
    "demands": [{"id": "A", "material": "M", "length": 300, "quantity": 2}],
    "new_stock": [
        {"id": "S1", "material": "M", "length": 1000, "price": 50, "available": 2}
    ],
    "remnants": [{"id": "R1", "material": "M", "length": 400}],
    "kerf_width": 4,
    "remnant_threshold": 100,
    "time_budget_ms": 3000,
}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_optimize_ok():
    r = client.post("/optimize", json=PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("optimal", "feasible")
    assert body["metrics"]["purchase_cost"] >= 0
    for bar in body["bars"]:
        total = sum(c["length"] for c in bar["cuts"]) + bar["kerf_loss"] + bar["remnant_length"]
        assert total == bar["bar_length"]


def test_validation_error_422():
    bad = dict(PAYLOAD, kerf_width=True)
    r = client.post("/optimize", json=bad)
    assert r.status_code == 422


def test_health_during_solve():
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            heavy = dict(PAYLOAD, time_budget_ms=2000)
            task = asyncio.create_task(ac.post("/optimize", json=heavy))
            await asyncio.sleep(0.05)
            r = await ac.get("/health")
            assert r.status_code == 200
            resp = await task
            assert resp.status_code == 200

    asyncio.run(run())
