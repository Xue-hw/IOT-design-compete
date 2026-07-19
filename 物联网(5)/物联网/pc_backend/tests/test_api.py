from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


SAMPLE = {
    "device_id": "focuscube-s3-01",
    "source": "s3",
    "ts": 1718000000,
    "light": {"lux": 235.6, "label": "suitable"},
    "imu": {"face": 2, "mode": "focus", "activity": 0.32},
    "focus": {"state": "running", "remaining_s": 940, "session_count": 3},
    "power": {"battery_pct": 78, "charging": False},
}


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_path=str(tmp_path / "test.db"), llm_api_key="")
    return TestClient(create_app(settings))


def test_health(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "focuscube-backend",
        "version": "1.0.0",
    }


def test_exact_telemetry_contract_is_accepted(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.post("/api/v1/telemetry", json=SAMPLE)
    assert response.status_code == 201
    assert response.json()["stored"] is True


def test_status_reflects_latest_telemetry(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/telemetry", json=SAMPLE)
    response = client.get(
        "/api/v1/status",
        params={"device_id": SAMPLE["device_id"]},
    )
    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["device_id"] == "focuscube-s3-01"
    assert device["light"]["lux"] == 235.6
    assert device["focus"]["remaining_s"] == 940
    assert device["power"]["battery_pct"] == 78


def test_c3_proxy_is_not_exposed_as_a_product_node(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    proxy = {
        **SAMPLE,
        "device_id": "focuscube-c3-proxy-01",
        "source": "c3-as7341-proxy",
        "imu": {**SAMPLE["imu"], "valid": False},
        "focus": {**SAMPLE["focus"], "valid": False},
        "power": {**SAMPLE["power"], "valid": False},
    }
    client.post("/api/v1/telemetry", json=proxy)

    response = client.get(
        "/api/v1/status",
        params={"device_id": proxy["device_id"]},
    )

    assert response.status_code == 200
    device = response.json()["devices"][0]
    assert device["product_node"] is False
    assert device["device_role"] == "sensor_proxy"
    assert device["physical_source"] == "c3-as7341-proxy"


def test_status_returns_only_the_explicitly_selected_primary_device(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    eye = {**SAMPLE, "device_id": "focuscube-eye-test-01"}
    cube = {**SAMPLE, "device_id": "focuscube-cube-demo-01"}
    client.post("/api/v1/telemetry", json=eye)
    client.post("/api/v1/telemetry", json=cube)

    response = client.get(
        "/api/v1/status",
        params={"device_id": eye["device_id"]},
    )

    assert response.status_code == 200
    devices = response.json()["devices"]
    assert [device["device_id"] for device in devices] == [eye["device_id"]]
    assert devices[0]["product_node"] is True


def test_daily_report_uses_fused_metrics(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/telemetry", json=SAMPLE)
    report_date = datetime.fromtimestamp(SAMPLE["ts"], ZoneInfo("Asia/Shanghai")).date().isoformat()
    response = client.get(
        "/api/v1/report/daily",
        params={"device_id": SAMPLE["device_id"], "date": report_date},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["sample_count"] == 1
    assert body["metrics"]["avg_lux"] == 235.6
    assert body["metrics"]["pomodoro_count"] == 3
    assert body["report_text"]


def test_reminder_generation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    dim = {**SAMPLE, "light": {"lux": 80, "label": "too_dim"}}
    client.post("/api/v1/telemetry", json=dim)
    response = client.get(
        "/api/v1/reminders",
        params={"device_id": SAMPLE["device_id"], "since": 0},
    )
    assert response.status_code == 200
    assert any(item["type"] == "too_dim" for item in response.json())


def test_timeseries_and_config(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/telemetry", json=SAMPLE)
    report_date = datetime.fromtimestamp(SAMPLE["ts"], ZoneInfo("Asia/Shanghai")).date().isoformat()
    series = client.get(
        "/api/v1/timeseries",
        params={"device_id": SAMPLE["device_id"], "date": report_date, "metric": "lux"},
    )
    assert series.status_code == 200
    assert series.json()["points"][0]["value"] == 235.6

    config = client.put(
        "/api/v1/config",
        params={"device_id": SAMPLE["device_id"]},
        json={"light_min_lux": 180, "light_max_lux": 480},
    )
    assert config.status_code == 200
    assert config.json()["light_min_lux"] == 180


def test_ai_gateway_openai_compatible_call(monkeypatch) -> None:
    from app.services.llm import call_cloud_llm

    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"report_text":"光照与专注状态整体稳定。","suggestions":["保持当前环境。"]}'
                        }
                    }
                ]
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("app.services.llm.httpx.post", fake_post)
    settings = Settings(
        llm_provider="volcengine_ai_gateway",
        llm_base_url="https://gateway.example.com/v1",
        llm_api_key="secret",
        llm_model="model-from-console",
    )
    report_text, suggestions = call_cloud_llm(
        settings,
        "focuscube-s3-01",
        "2026-07-14",
        {"light_environment": {"average_lux": 235.6}},
    )

    assert captured["url"] == "https://gateway.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["json"]["model"] == "model-from-console"
    assert report_text
    assert suggestions
