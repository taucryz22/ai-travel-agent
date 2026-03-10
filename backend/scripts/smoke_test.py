from __future__ import annotations

import sys

import httpx


PAYLOAD = {
    "query": "Хочу выходные в Санкт-Петербурге, люблю рок-бары и современное искусство",
    "days": 2,
    "budget": 15000,
    "mode": "walking",
}


def main() -> int:
    try:
        resp = httpx.post("http://127.0.0.1:8000/api/plan", json=PAYLOAD, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        print(f"Smoke test failed: {exc}")
        return 1

    data = resp.json()
    if not data.get("days"):
        print("Smoke test failed: no days in response")
        return 1
    first_day = data["days"][0]
    if len(first_day.get("stops", [])) < 2:
        print("Smoke test failed: fewer than 2 stops")
        return 1

    print("City:", data.get("city"))
    print("Day:", first_day.get("title"))
    for stop in first_day.get("stops", [])[:2]:
        print(f"- {stop['start']} {stop['name']} ({stop['category']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
