"""Export the ULOO Core OpenAPI contract to contracts/openapi.yaml.

Implementation plan section 4 requires the first OpenAPI document to be produced by
the server and locked into ``contracts/openapi.yaml``, which the Dify BFF and the
``ULOO / Agno Team`` strategy treat as the single source of truth.

Usage (from services/uloo-core)::

    .venv/Scripts/python.exe ../../scripts/export_openapi.py
"""

from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description="Export or verify the locked ULOO OpenAPI contract")
    parser.add_argument("--check", action="store_true", help="fail without writing when the contract is stale")
    args = parser.parse_args()

    rendered = render_contract()
    previous = CONTRACT_PATH.read_text(encoding="utf-8") if CONTRACT_PATH.exists() else None

    if args.check:
        if previous != rendered:
            print(f"stale {CONTRACT_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
            return 1
        print(f"unchanged {CONTRACT_PATH.relative_to(REPO_ROOT)}")
        return 0

    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
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
