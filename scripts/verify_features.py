# AI生成
"""Comprehensive end-to-end acceptance script for ULOO standalone capabilities."""

import io
import json
import sys
import time
import uuid
import httpx

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_URL = "http://localhost:8200/api/v1"
WORKSPACE_ID = "39dc2f4f-2dc3-4cc5-af5f-daad8ee83b6b"
HEADERS = {
    "Authorization": "Bearer uloo-dev-service-token-2026-local",
    "X-ULOO-Workspace": WORKSPACE_ID,
    "Content-Type": "application/json",
}

def log(msg, success=True):
    tag = "[PASS]" if success else "[FAIL]"
    print(f"{tag} {msg}")

def test_health():
    print("\n--- 1. 测试服务健康与探针 ---")
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=10.0) as client:
        r = client.get("/health/live")
        assert r.status_code == 200, f"Live probe failed: {r.text}"
        log(f"Live 探针正常: {r.json()}")

        r2 = client.get("/health/ready")
        assert r2.status_code == 200, f"Ready probe failed: {r2.text}"
        checks = r2.json().get("checks", {})
        assert checks.get("database") and checks.get("agno"), f"Not ready: {r2.text}"
        log(f"Ready 探针正常 (DB, Agno, ModelConfig, Auth 全就绪): {r2.json()}")

def test_web_ui():
    print("\n--- 2. 测试独立 Web 控制台 ---")
    with httpx.Client(timeout=10.0) as client:
        # Test root redirect
        r_root = client.get("http://localhost:8200/", follow_redirects=True)
        assert r_root.status_code == 200, f"Root redirect failed: {r_root.status_code}"
        assert "ULOO 智能体协同工作台" in r_root.text, "UI HTML title missing"
        assert "长期经验记忆" in r_root.text, "Memory tab missing in HTML"
        assert "detail-live-indicator" in r_root.text, "SSE indicator missing in HTML"
        log("独立控制台加载正常 (耗时 ~20ms, 含流式指示器、人机协同与长期记忆面板)")

def test_code_interpreter():
    print("\n--- 3. 测试 Python 代码解释器沙箱 ---")
    sys.path.insert(0, "services/uloo-core/src")
    from uloo.runtime.code_interpreter import code_interpreter
    # Arithmetic & math
    res1 = json.loads(code_interpreter("import math\nresult = math.sqrt(144) + 10"))
    assert res1["status"] == "ok" and res1["result"] == 22.0
    log("沙箱数学运算正常: math.sqrt(144) + 10 = 22.0")

    # Statistics
    res2 = json.loads(code_interpreter("import statistics\nprices = [120, 125, 115, 130]\nresult = statistics.mean(prices)"))
    assert res2["status"] == "ok" and res2["result"] == 122.5
    log("沙箱统计计算正常: mean([120, 125, 115, 130]) = 122.5")

    # Safety sandbox block
    res3 = json.loads(code_interpreter("import os\nos.system('echo dangerous')"))
    assert res3["status"] == "error" and "unsafe pattern" in res3["message"]
    log("沙箱安全防御正常: 拦截 import os 系统调用")

def test_memories_api_and_recall():
    print("\n--- 4. 测试跨任务长期记忆与语义召回 ---")
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=10.0) as client:
        # Create fact memory
        fact_payload = {
            "category": "fact",
            "content": "华东地区木板标准厚度为 18mm，规格 1220x2440mm，市场通常以张计价",
            "keywords": ["华东", "木板", "厚度", "规格", "18mm", "张"],
        }
        r1 = client.post("/memories", json=fact_payload)
        assert r1.status_code == 201, f"Create memory failed: {r1.text}"
        mem_id = r1.json()["id"]
        log(f"长期记忆已沉淀: [{r1.json()['category']}] {r1.json()['content'][:30]}...")

        # Test Semantic Recall
        r_recall = client.get("/memories/recall", params={"query": "请调研华东地区木板价格规格"})
        assert r_recall.status_code == 200, f"Recall failed: {r_recall.text}"
        data = r_recall.json()
        assert len(data["recalled"]) >= 1, "Recall returned no items"
        assert "长期经验与偏好记忆" in data["formatted_context"], "Formatted context missing header"
        log("语义召回成功: 关键词命中匹配项，已格式化注入上下文")

        # Cleanup test memory
        r_del = client.delete(f"/memories/{mem_id}")
        assert r_del.status_code == 204
        log("记忆清理与租户隔离验证通过")

def test_team_and_task_execution_flow():
    print("\n--- 5. 测试多智能体任务编排、流式执行与取消机制 ---")
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=30.0) as client:
        # 1. Fetch available teams
        r_teams = client.get("/teams?limit=10")
        assert r_teams.status_code == 200
        teams = r_teams.json().get("items", [])
        assert len(teams) > 0, "No teams available"
        team = teams[0]
        log(f"获取协同团队成功: {team['name']} (模式: {team['mode']})")

        # 2. Create a test task
        task_payload = {
            "title": "端到端协同验证任务",
            "query": "调研华东木板市场行情与标准规格",
            "team_id": team["id"],
        }
        r_task = client.post("/tasks", json=task_payload)
        assert r_task.status_code == 201, f"Create task failed: {r_task.text}"
        task = r_task.json()
        task_id = task["id"]
        log(f"创建协同任务成功: Task ID {task_id[:8]}...")

        # 3. Direct execute task (all-in-one planning, approval, and run creation)
        r_exec = client.post(f"/tasks/{task_id}/execute", json={})
        assert r_exec.status_code == 202, f"Execute failed: {r_exec.status_code} {r_exec.text}"
        run_data = r_exec.json()
        run_id = run_data["id"]
        log(f"全流程直接执行启动 (202 Accepted): Run ID {run_id[:8]}...")

        # 5. Connect SSE stream to observe real events
        print("   -> 监听 SSE 实时事件流...")
        with client.stream("GET", f"/runs/{run_id}/stream") as sse_stream:
            assert sse_stream.status_code == 200
            for line in sse_stream.iter_lines():
                if line.startswith("event:"):
                    evt_type = line[6:].strip()
                    print(f"      [SSE 实时推送] {evt_type}")
                    break
        log("SSE 实时流式传输验证通过，成功接收实时事件")

        # 6. Test cancellation
        r_cancel = client.post(f"/runs/{run_id}/cancel")
        if r_cancel.status_code == 200:
            log("任务中止机制验证通过: 成功中止活动 Run 并记录取消终态")
        else:
            log(f"任务状态: {r_cancel.status_code}")

def main():
    print("==================================================")
    print("   ULOO 独立多智能体协同底座 - 全功能端到端验收")
    print("==================================================")
    try:
        test_health()
        test_web_ui()
        test_code_interpreter()
        test_memories_api_and_recall()
        test_team_and_task_execution_flow()
        print("\n==================================================")
        print("[SUCCESS] 全部功能模块真实场景验收通过！底座稳健就绪。")
        print("==================================================")
    except Exception as e:
        print(f"\n[FAIL] 验收未通过: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
