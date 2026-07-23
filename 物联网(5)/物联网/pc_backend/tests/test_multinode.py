from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.multinode import installation_status


TS = 1784736010
C3 = {
    "schema_version": 2,
    "message_id": "focuscube-c3-01:c3-test:1",
    "event_type": "sample",
    "device_id": "focuscube-c3-01",
    "installation_id": "focuscube-base-01",
    "source": "c3-as7341",
    "boot_id": "c3-test",
    "seq": 1,
    "ts": TS,
    "session_id": "eye-test-session",
    "light": {
        "sample_seq": 8, "lux": 360.72, "label": "suitable", "sensor": "AS7341",
        "calibrated": False, "saturated": False, "gain": 64.0, "integration_ms": 50.04,
    },
    "health": {"run_state": "FOCUS_ACTIVE"},
}
EYE = {
    "schema_version": 2,
    "message_id": "focuscube-eye-01:eye-test:1",
    "event_type": "session_start",
    "device_id": "focuscube-eye-01",
    "installation_id": "focuscube-base-01",
    "source": "s3-eye-edge",
    "boot_id": "eye-test",
    "seq": 1,
    "ts": TS,
    "session_id": "eye-test-session",
    "imu": {"face": "+X", "mode": "focus", "activity": 0.18},
    "focus": {"state": "running", "remaining_s": 1470, "session_count": 3},
    "edge": {"environment": {
        "source_device_id": "focuscube-c3-01", "source_boot_id": "c3-test",
        "source_seq": 8, "state": "suitable", "trend": "stable", "score": 0.86,
        "confidence": 0.93, "window_s": 30, "algorithm": "spectral-rule-v1", "inference_ms": 1.2,
    }},
    "health": {"run_state": "ACTIVE", "c3_connected": True, "c3_control_ok": True},
}


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(database_path=str(tmp_path / "multi.db"))))


def test_v2_ingestion_duplicate_conflict_and_ownership(tmp_path: Path) -> None:
    api = client(tmp_path)
    first = api.post("/api/v1/telemetry", json=C3)
    assert first.status_code == 201
    assert first.json()["accepted_blocks"] == ["light", "health"]
    duplicate = api.post("/api/v1/telemetry", json=C3)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    conflict = api.post("/api/v1/telemetry", json={**C3, "ts": TS + 1})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "message_id_conflict"

    eye_with_light = {**EYE, "light": C3["light"]}
    response = api.post("/api/v1/telemetry", json=eye_with_light)
    assert response.status_code == 201
    assert "light" in response.json()["ignored_blocks"]
    assert any(item["code"] == "ignored_block_not_owned" for item in response.json()["warnings"])


def test_partial_and_coerced_blocks_are_diagnostic(tmp_path: Path) -> None:
    api = client(tmp_path)
    partial = {
        **C3,
        "message_id": "focuscube-c3-01:c3-test:2",
        "seq": 2,
        "light": {"sample_seq": "9", "lux": "320.5"},
    }
    response = api.post("/api/v1/telemetry", json=partial)
    assert response.status_code == 201
    body = response.json()
    assert body["business_accepted"] is False
    assert body["diagnostic_blocks"] == ["light"]
    assert any(item["code"] == "field_coerced" for item in body["warnings"])
    series = api.get("/api/v1/timeseries", params={
        "device_id": "focuscube-base-01",
        "date": datetime.fromtimestamp(TS, ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "metric": "light.lux",
    })
    assert series.json()["points"] == []


def test_fused_status_members_and_timeseries(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.post("/api/v1/telemetry", json=C3).status_code == 201
    assert api.post("/api/v1/telemetry", json=EYE).status_code == 201
    status = api.get("/api/v1/status", params={"installation_id": "focuscube-base-01"})
    assert status.status_code == 200
    body = status.json()
    assert body["telemetry"]["light"]["source_device_id"] == "focuscube-c3-01"
    assert body["telemetry"]["imu"]["source_device_id"] == "focuscube-eye-01"
    assert body["telemetry"]["edge"]["environment"]["score"] == 0.86
    assert {item["device_id"] for item in body["members"]} == {"focuscube-eye-01", "focuscube-c3-01"}
    assert body["devices"][0]["telemetry"]["imu"]["face"] == 0

    report_date = datetime.fromtimestamp(TS, ZoneInfo("Asia/Shanghai")).date().isoformat()
    light = api.get("/api/v1/timeseries", params={
        "device_id": "focuscube-base-01", "date": report_date, "metric": "light.lux",
    }).json()
    assert light["points"][0]["source_device_id"] == "focuscube-c3-01"
    edge = api.get("/api/v1/timeseries", params={
        "device_id": "focuscube-base-01", "date": report_date, "metric": "edge.environment.score",
    }).json()
    assert edge["points"][0]["value"] == 0.86


def test_fused_daily_report_uses_multinode_rows(tmp_path: Path) -> None:
    api = client(tmp_path)
    assert api.post("/api/v1/telemetry", json=C3).status_code == 201
    assert api.post("/api/v1/telemetry", json=EYE).status_code == 201

    report_date = datetime.fromtimestamp(TS, ZoneInfo("Asia/Shanghai")).date().isoformat()
    response = api.get("/api/v1/report/daily", params={
        "device_id": "focuscube-base-01",
        "date": report_date,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "focuscube-base-01"
    assert body["metrics"]["avg_lux"] == 360.7
    assert body["metrics"]["avg_activity"] == 0.18
    assert body["metrics"]["focus_sample_count"] == 1


def test_status_target_and_identity_errors(tmp_path: Path) -> None:
    api = client(tmp_path)
    ambiguous = api.get("/api/v1/status", params={
        "device_id": "focuscube-base-01", "installation_id": "focuscube-base-01",
    })
    assert ambiguous.status_code == 400
    assert ambiguous.json()["detail"]["code"] == "ambiguous_status_target"
    unknown = api.get("/api/v1/status", params={"installation_id": "other-base"})
    assert unknown.status_code == 404
    bad_id = api.post("/api/v1/telemetry", json={**C3, "source": "wrong"})
    assert bad_id.status_code == 422
    assert bad_id.json()["detail"]["code"] == "device_identity_mismatch"


def test_idle_heartbeat_stays_online_for_120_seconds(tmp_path: Path) -> None:
    api = client(tmp_path)
    response = api.post("/api/v1/telemetry", json=C3)
    received_at = response.json()["received_at"]

    after_17_seconds = installation_status(api.app.state.db, now=received_at + 17)
    c3 = next(item for item in after_17_seconds["members"] if item["device_id"] == "focuscube-c3-01")
    assert c3["online"] is True
    assert c3["online_timeout_s"] == 120

    after_121_seconds = installation_status(api.app.state.db, now=received_at + 121)
    c3 = next(item for item in after_121_seconds["members"] if item["device_id"] == "focuscube-c3-01")
    assert c3["online"] is False
