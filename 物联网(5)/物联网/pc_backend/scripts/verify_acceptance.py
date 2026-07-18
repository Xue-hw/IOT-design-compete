from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def main() -> None:
    project_root = PROJECT_ROOT
    database_path = project_root / "data" / "acceptance.db"
    if database_path.exists():
        database_path.unlink()

    settings = Settings(database_path=str(database_path), llm_api_key="")
    client = TestClient(create_app(settings))
    payload = json.loads((project_root / "examples" / "telemetry.json").read_text(encoding="utf-8"))

    post_response = client.post("/api/v1/telemetry", json=payload)
    status_response = client.get(
        "/api/v1/status",
        params={"device_id": payload["device_id"]},
    )
    result = {
        "telemetry_http_status": post_response.status_code,
        "telemetry_response": post_response.json(),
        "status_http_status": status_response.status_code,
        "status_response": status_response.json(),
        "checks": {
            "telemetry_returns_2xx": 200 <= post_response.status_code < 300,
            "status_contains_device": any(
                item.get("device_id") == payload["device_id"]
                for item in status_response.json().get("devices", [])
            ),
        },
    }
    output = project_root / "docs" / "acceptance_result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
