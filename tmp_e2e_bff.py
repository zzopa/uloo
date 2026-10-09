"""E2E verification through the Dify Console BFF (browser-equivalent path)."""
import httpx

BASE = "http://localhost:5001/console/api/uloo"
TENANT = "20000000-0000-0000-0000-000000000001"


def main() -> None:
    # BFF requires a Dify session (login). Without cookies this must 401,
    # proving the route exists and is wired into the login chain.
    r = httpx.get(f"{BASE}/tasks", timeout=10)
    print("bff tasks (no session) status:", r.status_code)

    r2 = httpx.get(f"{BASE}/teams", timeout=10)
    print("bff teams (no session) status:", r2.status_code)


if __name__ == "__main__":
    main()
