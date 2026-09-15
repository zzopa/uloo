"""Export the ULOO Core OpenAPI contract to contracts/openapi.yaml.

Implementation plan section 4 requires the first OpenAPI document to be produced by
the server and locked into ``contracts/openapi.yaml``, which the Dify BFF and the
``ULOO / Agno Team`` strategy treat as the single source of truth.

Usage (from services/uloo-core)::

    .venv/Scripts/python.exe ../../scripts/export_openapi.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.yaml"

sys.path.insert(0, str(REPO_ROOT / "services" / "uloo-core" / "src"))


def render_contract() -> str:
    """Return the OpenAPI document for the current app as stable YAML."""
    import yaml

    from uloo.main import create_app

    document = create_app().openapi()
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=True, width=120)


def main() -> int:
    rendered = render_contract()
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)

    previous = CONTRACT_PATH.read_text(encoding="utf-8") if CONTRACT_PATH.exists() else None
    CONTRACT_PATH.write_text(rendered, encoding="utf-8", newline="\n")

    if previous is None:
        print(f"created {CONTRACT_PATH.relative_to(REPO_ROOT)}")
    elif previous == rendered:
        print(f"unchanged {CONTRACT_PATH.relative_to(REPO_ROOT)}")
    else:
        print(f"updated {CONTRACT_PATH.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
