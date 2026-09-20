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
    assert data["features"] == {
        "agents": True,
        "teams": True,
        "agent_test_runs": False,
        "team_runs": False,
        "streaming": False,
        "memory": False,
    }


async def test_validation_errors_use_stable_envelope(client):
    resp = await client.get("/api/v1/agents/not-a-uuid")
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert data["message"] == "Request validation failed"
    assert data["request_id"] == resp.headers["X-Request-ID"]
    assert data["trace_id"] == resp.headers["X-Trace-ID"]
