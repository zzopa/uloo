"""E2E verification of the task create -> get -> list flow against the live Core."""
import httpx

TOKEN = "uloo-dev-service-token-2026-local"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-ULOO-Workspace": "20000000-0000-0000-0000-000000000001",
}
BASE = "http://localhost:8200/api/v1"


def main() -> None:
    r = httpx.post(
        f"{BASE}/tasks",
        json={
            "title": "E2E 验证任务",
            "query": "验证从输入到创建的完整链路，输出一份检查报告。",
        },
        headers=HEADERS,
        timeout=10,
    )
    print("create status:", r.status_code)
    body = r.json()
    tid = body.get("id")
    print("task id:", tid, "| status:", body.get("status"))

    r2 = httpx.get(f"{BASE}/tasks/{tid}", headers=HEADERS, timeout=10)
    print("get status:", r2.status_code, "| title:", r2.json().get("title"), "| plans:", r2.json().get("plans"))

    r3 = httpx.get(f"{BASE}/tasks", headers=HEADERS, timeout=10)
    data = r3.json()
    print("list status:", r3.status_code, "| total:", data.get("total"), "| first:", data.get("items", [{}])[0].get("title"))


if __name__ == "__main__":
    main()
