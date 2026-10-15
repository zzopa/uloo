"""Run with Dify's Python from vendor/dify/api; configure the Core service bridge."""

import json
import sys
from pathlib import Path

from dotenv import set_key
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor/dify/api"))

from app_factory import create_app
from configs import dify_config
from extensions.ext_database import db
from models.account import Account, TenantAccountJoin
from models.model import App
from services.app_service import AppService, CreateAppParams


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python ../../../scripts/configure_dify_tools.py <workspace-uuid>")
    workspace = sys.argv[1]
    _, app = create_app()
    with app.app_context():
        membership = db.session.scalar(
            select(TenantAccountJoin).where(TenantAccountJoin.tenant_id == workspace, TenantAccountJoin.role == "owner")
        )
        if membership is None:
            raise SystemExit("Workspace owner not found; no settings changed")
        account = db.session.get(Account, membership.account_id)
        context_app = db.session.scalar(
            select(App).where(App.tenant_id == workspace, App.name == "ULOO 工具运行上下文")
        )
        if context_app is None:
            context_app = AppService().create_app(
                workspace,
                CreateAppParams(
                    name="ULOO 工具运行上下文", mode="workflow", description="Agno 调用 Dify 原生工具的服务上下文"
                ),
                account,
                session=db.session,
            )
            db.session.commit()
        if not dify_config.INNER_API_KEY_FOR_PLUGIN or not dify_config.PLUGIN_DAEMON_KEY:
            raise SystemExit("Dify inner API transport is not configured")
        path = ROOT / "services/uloo-core/.env"
        from dotenv import dotenv_values

        callers = json.loads(dotenv_values(path).get("ULOO_DIFY_WORKSPACE_CALLERS") or "{}")
        callers[workspace] = {"app_id": context_app.id, "user_id": account.id}
        set_key(str(path), "ULOO_DIFY_API_URL", "http://localhost:5001")
        set_key(str(path), "ULOO_DIFY_INNER_API_KEY", dify_config.INNER_API_KEY_FOR_PLUGIN)
        set_key(str(path), "ULOO_DIFY_WORKSPACE_CALLERS", json.dumps(callers))
        print("Dify tool service context configured; credentials were not printed.")


if __name__ == "__main__":
    main()
