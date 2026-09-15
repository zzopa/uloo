"""Tests for health endpoints."""


async def test_liveness(client):
    resp = await client.get("/api/v1/health/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_readiness_structure(client):
    resp = await client.get("/api/v1/health/ready")
    data = resp.json()
    assert "status" in data
    assert "checks" in data
    assert "database" in data["checks"]
    assert "agno" in data["checks"]
    assert "model_config" in data["checks"]


async def test_capabilities(client):
    resp = await client.get("/api/v1/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "modes" in data
    assert "streaming" in data
