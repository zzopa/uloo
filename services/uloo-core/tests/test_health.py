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
    assert data["checks"]["service_auth"] is True


async def test_capabilities(client):
    resp = await client.get("/api/v1/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "modes" in data
    assert data["tools"] == ["web-search"]
    assert data["features"] == {
        "agents": True,
        "teams": True,
        "agent_test_runs": True,
        "team_runs": True,
        "streaming": False,
        "memory": False,
    }


async def test_capabilities_models_picker_lists_selectable_model_ids(client, monkeypatch):
    """capabilities.models must surface selectable model ids per provider so the UI
    can render a picker instead of asking the user to type `provider:model-id`."""
    from uloo.config import settings

    monkeypatch.setattr(settings, "model_providers", {"deepseek": "https://api.deepseek.com"})
    monkeypatch.setattr(
        settings,
        "model_catalog",
        {"deepseek": ["DeepSeek-V3.1", "deepseek-chat", "deepseek-chat"]},  # dup -> deduped
    )
    monkeypatch.setattr(settings, "planner_model_ref", "deepseek:DeepSeek-V3.1")

    resp = await client.get("/api/v1/capabilities")
    assert resp.status_code == 200
    models = resp.json()["models"]["deepseek"]
    assert models["base_url"] == "https://api.deepseek.com"
    # catalog takes priority; duplicates removed, order preserved
    assert models["models"] == ["DeepSeek-V3.1", "deepseek-chat"]


async def test_capabilities_models_fallback_to_planner_ref(client, monkeypatch):
    """When no explicit catalog is set, the planner_model_ref still yields one
    selectable model id so the picker is never empty for a configured provider."""
    from uloo.config import settings

    monkeypatch.setattr(settings, "model_providers", {"deepseek": ""})
    monkeypatch.setattr(settings, "model_catalog", {})
    monkeypatch.setattr(settings, "planner_model_ref", "deepseek:DeepSeek-V3.1")

    resp = await client.get("/api/v1/capabilities")
    models = resp.json()["models"]["deepseek"]
    assert models["base_url"] == "default"
    assert models["models"] == ["DeepSeek-V3.1"]


async def test_validation_errors_use_stable_envelope(client):
    resp = await client.get("/api/v1/agents/not-a-uuid")
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert data["message"] == "Request validation failed"
    assert data["request_id"] == resp.headers["X-Request-ID"]
    assert data["trace_id"] == resp.headers["X-Trace-ID"]
