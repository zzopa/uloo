"""Verify persisted Agent definitions, real model execution and all Agno Team modes."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = REPO_ROOT / "services" / "uloo-core"
sys.path.insert(0, str(CORE_ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=uuid.UUID)
    parser.add_argument("--base-url", default="http://127.0.0.1:8200/api/v1")
    parser.add_argument("--model-ref", default="deepseek:DeepSeek-V3.1")
    parser.add_argument("--session-file", type=Path, help="Local Dify cookies and CSRF header for BFF verification")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "tmp" / "model-flow-report.json")
    args = parser.parse_args()
    load_dotenv(CORE_ROOT / ".env")
    from uloo.config import settings

    headers = {"Authorization": f"Bearer {settings.api_token}", "X-ULOO-Workspace": str(args.workspace)}
    cookies = {}
    if args.session_file:
        session = json.loads(args.session_file.read_text(encoding="utf-8"))
        headers = session["headers"]
        cookies = session["cookies"]
    report: dict = {"workspace_id": str(args.workspace), "model_ref": args.model_ref, "agents": [], "runs": []}
    suffix = uuid.uuid4().hex[:8]
    with httpx.Client(
        base_url=args.base_url.rstrip("/") + "/", headers=headers, cookies=cookies, timeout=600, trust_env=False
    ) as client:

        def request(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                data = response.json()
                raise RuntimeError(
                    f"{method} {path}: HTTP {response.status_code}, {data.get('code')}, {data.get('message')}"
                )
            return response.json()

        for key, name, role, instructions in (
            (
                "analyst",
                "DeepSeek 分析智能体",
                "Calculate and explain arithmetic accurately",
                ["Calculate the requested result. Show a short independent check. Do not invent facts."],
            ),
            (
                "reviewer",
                "DeepSeek 审核智能体",
                "Independently verify the analyst and produce a final answer",
                [
                    "Verify arithmetic independently. Review the analyst's actual findings when supplied. Report discrepancies."
                ],
            ),
        ):
            agent = request(
                "POST",
                "agents",
                json={
                    "key": f"deepseek-{key}-{suffix}",
                    "name": name,
                    "role": role,
                    "description": "Real-model main-flow verification; no mock outputs.",
                    "instructions": instructions,
                    "model_ref": args.model_ref,
                    "output_schema": (
                        {
                            "type": "object",
                            "properties": {"answer": {"type": "integer"}, "check": {"type": "string"}},
                            "required": ["answer", "check"],
                        }
                        if key == "analyst"
                        else None
                    ),
                },
            )
            # Updating the declaration must reach the subsequent executable definition.
            agent = request(
                "PATCH",
                f"agents/{agent['id']}",
                json={
                    "instructions": instructions + ["Keep your answer under 150 words."],
                    "expected_version": agent["version"],
                },
            )
            assert agent["version"] == 2
            assert request("GET", f"agents/{agent['id']}")["instructions"] == agent["instructions"]
            assert request("POST", f"agents/{agent['id']}/validate")["valid"]
            executed = request("POST", f"agents/{agent['id']}/test-runs", json={"prompt": "Compute 12 + 7."})
            assert executed["status"] == "succeeded" and executed["is_mock"] is False
            assert executed["run_id"] and "19" in json.dumps(executed["output"])
            if key == "analyst":
                assert executed["output"]["answer"] == 19 and executed["output"]["check"]
            report["agents"].append({"definition": agent, "execution": executed})
            print(f"PASS declaration/update/validation/single-Agent: {name}", flush=True)

        agent_ids = [entry["definition"]["id"] for entry in report["agents"]]
        for mode in ("coordinate", "route", "collaborate"):
            team = request(
                "POST",
                "teams",
                json={
                    "key": f"deepseek-{mode}-{suffix}",
                    "name": f"DeepSeek {mode} 验收团队",
                    "mode": mode,
                    "leader_agent_id": agent_ids[0],
                    "member_agent_ids": agent_ids,
                    "instructions": [
                        "Use the supplied approved plan. Delegate arithmetic to the analyst and verification to the reviewer.",
                        "For route mode, select the reviewer and return its answer. For other modes, both members must contribute.",
                        "Include the computed number and verification in a concise final answer.",
                    ],
                    "limits": {"max_iterations": 8, "timeout_seconds": 300, "max_tokens": 4096},
                },
            )
            assert request("POST", f"teams/{team['id']}/validate")["valid"]
            task = request(
                "POST",
                "tasks",
                json={
                    "title": f"DeepSeek {mode} 完整链路验收",
                    "team_id": team["id"],
                    "query": "Compute 12 + 7, have the reviewer independently verify it, and give a concise final result.",
                },
            )
            plan = request("POST", f"tasks/{task['id']}/plan", json={})
            assert plan["recommended_team_id"] == team["id"] and plan["steps"]
            assert plan["is_mock"] is False and plan["planner_run_id"]
            assert all(step["assigned_agent_id"] in agent_ids for step in plan["steps"])
            request("POST", f"tasks/{task['id']}/approve")
            run = request("POST", f"tasks/{task['id']}/runs", json={})
            assert run["status"] == "succeeded" and run["is_mock"] is False and run["agno_run_id"]
            assert "19" in json.dumps(run["output"])
            members = [event for event in run["events"] if event["event_type"] == "agent.completed"]
            expected_members = 1 if mode == "route" else 2
            assert len({event["source_id"] for event in members}) >= expected_members, "Missing real member execution"
            assert all(event["payload"]["run_id"] and event["payload"]["output"] for event in members)
            assert run["usage"] and run["usage"]["total_tokens"] > 0
            saved = request("GET", f"runs/{run['id']}")
            assert saved["output"] == run["output"] and saved["events"] == run["events"]
            assert request("GET", f"tasks/{task['id']}")["status"] == "succeeded"
            report["runs"].append({"team": team, "plan": plan, "run": saved})
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"PASS {mode}: task={task['id']} run={run['id']} member_calls={len(members)}", flush=True)
    print(f"Real model flow verified. Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
