# AI生成
"""Tests for S1-6: Dify caller identity propagation."""


from uloo.runtime.dify_tools import _runtime_caller, clear_runtime_caller, set_runtime_caller
from uloo.security import RequestContext


class TestRequestContextIdentity:
    """RequestContext should carry user_id and app_id (S1-6)."""

    def test_context_has_user_and_app_id(self):
        import uuid

        ctx = RequestContext(
            workspace_id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
            user_id="user-123",
            app_id="app-456",
        )
        assert ctx.user_id == "user-123"
        assert ctx.app_id == "app-456"

    def test_context_defaults_none(self):
        import uuid

        ctx = RequestContext(
            workspace_id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
        )
        assert ctx.user_id is None
        assert ctx.app_id is None


class TestRuntimeCallerContext:
    """Runtime caller context should be settable and clearable (S1-6)."""

    def test_set_and_clear(self):
        clear_runtime_caller()
        assert _runtime_caller.get() is None

        set_runtime_caller("user-1", "app-1", "ws-1")
        caller = _runtime_caller.get()
        assert caller is not None
        assert caller["user_id"] == "user-1"
        assert caller["app_id"] == "app-1"
        assert caller["workspace_id"] == "ws-1"

        clear_runtime_caller()
        assert _runtime_caller.get() is None

    def test_set_with_none_does_nothing(self):
        clear_runtime_caller()
        set_runtime_caller(None, None, "ws-1")
        assert _runtime_caller.get() is None
