from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from datetime import date
from typing import Any

from fastapi import HTTPException

from ..database import Database

INSTALLATION_ID = "focuscube-base-01"
# C3 在 IDLE 状态下每 60 秒发送一次 heartbeat。节点在线窗口必须覆盖
# 一个完整周期以及网络抖动；这与业务传感数据的 15 秒 freshness 窗口分开。
MEMBER_ONLINE_TIMEOUT_S = 120
BUSINESS_DATA_FRESHNESS_S = 15
REGISTRY = {
    "focuscube-eye-01": {
        "installation_id": INSTALLATION_ID,
        "source": "s3-eye-edge",
        "role": "edge_controller",
        "owned": {"imu", "focus", "edge", "health"},
    },
    "focuscube-c3-01": {
        "installation_id": INSTALLATION_ID,
        "source": "c3-as7341",
        "role": "light_sensor",
        "owned": {"light", "health"},
    },
}
BLOCKS = ("light", "imu", "focus", "power", "edge", "health")
QUALITY_BAD = {"partial", "missing", "invalid"}
DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
INT = re.compile(r"^(?:0|[1-9]\d*)$")

SPECS: dict[str, dict[str, tuple[str, Any]]] = {
    "light": {
        "sample_seq": ("int", (0, None)), "lux": ("float", (0, None)),
        "label": ("enum", {"too_dim", "suitable", "too_bright", "unknown"}),
        "sensor": ("enum", {"AS7341"}), "calibrated": ("bool", None),
        "saturated": ("bool", None), "gain": ("float", (0, None)),
        "integration_ms": ("float", (0, None)),
    },
    "imu": {
        "face": ("enum", {"+X", "-X", "+Y", "-Y", "+Z", "-Z", "move", "unknown"}),
        "mode": ("enum", {"focus", "audio", "vision", "presence", "light", "system", "unknown"}),
        "activity": ("float", (0, 1)),
    },
    "focus": {
        "state": ("enum", {"idle", "running", "completed", "ended", "error", "unknown"}),
        "remaining_s": ("int", (0, None)), "session_count": ("int", (0, None)),
    },
    "power": {"battery_pct": ("int", (0, 100)), "charging": ("bool", None)},
}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _warning(code: str, field: str | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code}
    if field:
        result["field"] = field
    result.update(extra)
    return result


def _convert(value: Any, kind: str, bounds: Any) -> tuple[Any, bool]:
    coerced = False
    if kind == "bool":
        if isinstance(value, bool):
            result = value
        elif value in (0, 1) and not isinstance(value, str):
            result, coerced = bool(value), True
        elif isinstance(value, str) and value.lower() in {"true", "false"}:
            result, coerced = value.lower() == "true", True
        else:
            raise ValueError
    elif kind in {"int", "float"}:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, str):
            pattern = INT if kind == "int" else DECIMAL
            if not pattern.fullmatch(value):
                raise ValueError
            result, coerced = (int(value) if kind == "int" else float(value)), True
        elif kind == "int" and isinstance(value, int):
            result = value
        elif kind == "float" and isinstance(value, (int, float)):
            result = float(value)
        else:
            raise ValueError
        if not math.isfinite(float(result)):
            raise ValueError
        low, high = bounds
        if result < low or (high is not None and result > high):
            raise ValueError
        if kind == "float" and low == 0 and bounds[1] is None and result == 0 and False:
            raise ValueError
    else:
        if value not in bounds:
            raise ValueError
        result = value
    return result, coerced


def _normalize_simple(name: str, raw: Any, ts: int, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        warnings.append(_warning("field_invalid", name))
        return {"valid": False, "quality": "invalid", "missing_fields": list(SPECS[name]), "invalid_reason": "block_not_object"}
    known = set(SPECS[name]) | {"valid", "quality", "missing_fields", "invalid_reason", "ended_reason"}
    for key in raw.keys() - known:
        warnings.append(_warning("unknown_field_ignored", f"{name}.{key}"))
    result = {key: raw.get(key) for key in ("invalid_reason", "ended_reason") if key in raw}
    invalid = False
    for field, (kind, bounds) in SPECS[name].items():
        if field not in raw:
            continue
        try:
            result[field], coerced = _convert(raw[field], kind, bounds)
            if coerced:
                warnings.append(_warning("field_coerced", f"{name}.{field}"))
        except (ValueError, TypeError):
            invalid = True
            warnings.append(_warning("field_invalid", f"{name}.{field}"))
    missing = [field for field in SPECS[name] if field not in result]
    defaults_invalid = result.get("label") == "unknown" or result.get("face") == "unknown" or result.get("state") == "unknown"
    saturated = name == "light" and result.get("saturated") is True
    if invalid or defaults_invalid or saturated:
        quality, valid = "invalid", False
        if saturated:
            warnings.append(_warning("light_saturated", name))
    elif len(missing) == len(SPECS[name]) or ts == 0:
        quality, valid = "missing", False
    elif missing:
        quality, valid = "partial", False
    elif raw.get("valid") is False:
        quality, valid = raw.get("quality") if raw.get("quality") in QUALITY_BAD else "invalid", False
    elif raw.get("quality") == "estimated":
        quality, valid = "estimated", True
    else:
        quality, valid = "measured", True
    result.update(valid=valid, quality=quality, missing_fields=missing)
    if not valid and not result.get("invalid_reason"):
        result["invalid_reason"] = "unsynced_clock" if ts == 0 else f"{name}_{quality}"
    if missing and quality == "partial":
        warnings.append(_warning("block_incomplete", name, missing_fields=missing))
    return result


def _normalize_edge(raw: Any, ts: int, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        warnings.append(_warning("field_invalid", "edge"))
        return {"valid": False, "quality": "invalid", "invalid_reason": "block_not_object"}
    result: dict[str, Any] = {}
    for key in raw.keys() - {"valid", "quality", "missing_fields", "invalid_reason", "environment", "motion"}:
        warnings.append(_warning("unknown_field_ignored", f"edge.{key}"))
    requirements = {
        "environment": {"source_device_id", "source_boot_id", "source_seq", "state", "trend", "score", "confidence", "window_s", "algorithm", "inference_ms"},
        "motion": {"class", "confidence", "algorithm", "inference_ms"},
    }
    for section, required in requirements.items():
        value = raw.get(section)
        if not isinstance(value, dict):
            continue
        missing = sorted(required - value.keys())
        valid = not missing and ts > 0 and value.get("state") != "unknown" and value.get("class") != "unknown"
        normalized = dict(value)
        normalized.update(valid=valid, quality="derived" if valid else ("missing" if not value else "partial"), missing_fields=missing)
        result[section] = normalized
        if missing:
            warnings.append(_warning("block_incomplete", f"edge.{section}", missing_fields=missing))
    result["valid"] = any(v.get("valid") for v in result.values() if isinstance(v, dict))
    result["quality"] = "derived" if result["valid"] else "missing"
    if not result["valid"]:
        result["invalid_reason"] = "edge_missing"
    return result


def ingest_v2(db: Database, raw: dict[str, Any], received_at: int | None = None) -> tuple[int, dict[str, Any]]:
    now = received_at or int(time.time())
    required = {"schema_version", "message_id", "event_type", "device_id", "installation_id", "source", "boot_id", "seq"}
    missing = sorted(required - raw.keys())
    if missing:
        raise _error(422, "invalid_envelope", f"missing fields: {', '.join(missing)}")
    if raw.get("schema_version") != 2:
        raise _error(422, "unsupported_schema_version", "only schema_version=2 is supported")
    device_id = raw.get("device_id")
    registration = REGISTRY.get(device_id)
    if not registration or raw.get("installation_id") != registration["installation_id"] or raw.get("source") != registration["source"]:
        raise _error(422, "device_identity_mismatch", "device_id/source/installation_id do not match registry")
    event_type = raw.get("event_type")
    if event_type not in {"sample", "session_start", "session_update", "session_end", "heartbeat"}:
        raise _error(422, "invalid_envelope", "invalid event_type")
    try:
        seq, ts = int(raw["seq"]), int(raw.get("ts", 0))
    except (TypeError, ValueError):
        raise _error(422, "invalid_envelope", "seq and ts must be integers")
    if seq < 0 or ts < 0 or not isinstance(raw.get("boot_id"), str) or not raw["boot_id"]:
        raise _error(422, "invalid_envelope", "invalid boot_id, seq, or ts")
    expected_id = f"{device_id}:{raw['boot_id']}:{seq}"
    if raw.get("message_id") != expected_id:
        raise _error(422, "message_id_mismatch", f"message_id must equal {expected_id}")
    try:
        raw_json = _canonical(raw)
    except (ValueError, TypeError):
        raise _error(422, "invalid_json_number", "NaN and Infinity are not allowed")
    payload_hash = hashlib.sha256(raw_json.encode()).hexdigest()
    warnings: list[dict[str, Any]] = []
    for key in raw.keys() - required - {"ts", "session_id"} - set(BLOCKS):
        warnings.append(_warning("unknown_field_ignored", key))
    accepted: list[str] = []
    diagnostics: list[str] = []
    ignored: list[str] = []
    normalized: dict[str, Any] = {key: raw.get(key) for key in required | {"ts", "session_id"} if key in raw}
    for name in BLOCKS:
        if name not in raw:
            continue
        if name not in registration["owned"] or (event_type == "heartbeat" and name != "health"):
            ignored.append(name)
            warnings.append(_warning("ignored_block_not_owned" if name not in registration["owned"] else "heartbeat_business_block_ignored", name))
            continue
        if name == "health":
            if isinstance(raw[name], dict):
                normalized[name] = dict(raw[name])
                accepted.append(name)
            else:
                diagnostics.append(name)
                warnings.append(_warning("field_invalid", name))
            continue
        block = _normalize_edge(raw[name], ts, warnings) if name == "edge" else _normalize_simple(name, raw[name], ts, warnings)
        normalized[name] = block
        if block.get("valid") and block.get("quality") in {"measured", "derived"}:
            accepted.append(name)
        else:
            diagnostics.append(name)
    if event_type.startswith("session_"):
        focus = normalized.get("focus")
        terminal_ok = event_type != "session_end" or focus and focus.get("state") in {"completed", "ended", "error"}
        if not raw.get("session_id") or not focus or not focus.get("valid") or not terminal_ok:
            warnings.append(_warning("session_transition_skipped", "focus"))
    business_accepted = any(name != "health" for name in accepted)
    normalized_json = _canonical(normalized)
    installation_config = db.get_config(INSTALLATION_ID)
    with db._lock, db.connect() as conn:
        existing = conn.execute("SELECT payload_hash FROM telemetry_events WHERE message_id=?", (raw["message_id"],)).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise _error(409, "message_id_conflict", "message_id already exists with different payload")
            return 200, {"ok": True, "stored": False, "duplicate": True, "message_id": raw["message_id"]}
        cursor = conn.execute(
            """INSERT INTO telemetry_events(message_id,payload_hash,schema_version,event_type,device_id,
               installation_id,source,boot_id,seq,ts,received_at,time_quality,session_id,
               business_accepted,raw_payload_json,normalized_payload_json,warnings_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (raw["message_id"], payload_hash, 2, event_type, device_id, INSTALLATION_ID,
             raw["source"], raw["boot_id"], seq, ts, now, "device" if ts > 0 else "unsynced",
             raw.get("session_id"), int(business_accepted), raw_json, normalized_json, _canonical(warnings)),
        )
        event_id = int(cursor.lastrowid)
        _insert_blocks(conn, event_id, normalized, installation_config)
        _update_session(conn, raw, normalized, warnings)
        _insert_reminders(conn, raw["message_id"], normalized, installation_config, now)
        if ts > 0:
            report_date = __import__("datetime").datetime.fromtimestamp(ts, db.timezone).date().isoformat()
            conn.execute("DELETE FROM reports WHERE device_id=? AND report_date=?", (INSTALLATION_ID, report_date))
    return 201, {
        "ok": True, "message": "telemetry accepted", "schema_version": 2,
        "message_id": raw["message_id"], "device_id": device_id, "installation_id": INSTALLATION_ID,
        "stored": True, "duplicate": False, "business_accepted": business_accepted,
        "accepted_blocks": accepted, "diagnostic_blocks": diagnostics,
        "ignored_blocks": ignored, "warnings": warnings, "received_at": now,
    }


def _insert_reminders(
    conn: sqlite3.Connection,
    message_id: str,
    payload: dict[str, Any],
    config: dict[str, Any],
    now: int,
) -> None:
    candidates: list[tuple[str, str, int, int]] = []
    light = payload.get("light")
    if light and light.get("valid") and light.get("quality") == "measured" and not light.get("saturated"):
        if light["lux"] < float(config["light_min_lux"]):
            candidates.append(("too_dim", "当前光线偏暗，建议打开台灯。", 2, 120))
        elif light["lux"] > float(config["light_max_lux"]):
            candidates.append(("too_bright", "当前光线偏亮，建议降低灯光或调整位置。", 2, 120))
    focus = payload.get("focus")
    if focus and focus.get("valid") and focus.get("quality") == "measured" and focus.get("state") in {"completed", "ended"}:
        candidates.append(("take_break", "本轮专注已结束，建议休息 5 分钟。", 2, 300))
    for reminder_type, text_value, priority, ttl_s in candidates:
        reminder_id = "v2-" + hashlib.sha1(f"{message_id}:{reminder_type}".encode()).hexdigest()[:12]
        conn.execute(
            """INSERT OR IGNORE INTO reminders(id,device_id,created_at,type,text,priority,ttl_s)
               VALUES(?,?,?,?,?,?,?)""",
            (reminder_id, INSTALLATION_ID, now, reminder_type, text_value, priority, ttl_s),
        )


def _insert_blocks(conn: sqlite3.Connection, event_id: int, payload: dict[str, Any], config: dict[str, Any]) -> None:
    common = lambda block: (event_id, int(block["valid"]), block["quality"], block.get("invalid_reason"), _canonical(block.get("missing_fields", [])))
    if "light" in payload:
        b = payload["light"]
        server_label = None
        if b.get("valid") and b.get("quality") == "measured":
            server_label = "too_dim" if b["lux"] < config["light_min_lux"] else "too_bright" if b["lux"] > config["light_max_lux"] else "suitable"
        conn.execute("""INSERT INTO light_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     common(b) + (b.get("sample_seq"), b.get("lux"), b.get("label"), server_label, b.get("sensor"),
                                  _boolint(b.get("saturated")), _boolint(b.get("calibrated")), b.get("gain"), b.get("integration_ms")))
    if "imu" in payload:
        b = payload["imu"]
        conn.execute("INSERT INTO imu_samples VALUES(?,?,?,?,?,?,?,?)", common(b) + (b.get("face"), b.get("mode"), b.get("activity")))
    if "focus" in payload:
        b = payload["focus"]
        conn.execute("INSERT INTO focus_events VALUES(?,?,?,?,?,?,?,?,?)", common(b) + (b.get("state"), b.get("remaining_s"), b.get("session_count"), b.get("ended_reason")))
    if "power" in payload:
        b = payload["power"]
        conn.execute("INSERT INTO power_samples VALUES(?,?,?,?,?,?,?)", common(b) + (b.get("battery_pct"), _boolint(b.get("charging"))))
    if "edge" in payload:
        b = payload["edge"]
        conn.execute("INSERT INTO edge_results VALUES(?,?,?)", (event_id, _canonical(b.get("environment")) if b.get("environment") else None, _canonical(b.get("motion")) if b.get("motion") else None))
    if "health" in payload:
        conn.execute("INSERT INTO health_samples VALUES(?,?)", (event_id, _canonical(payload["health"])))


def _boolint(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _update_session(conn: sqlite3.Connection, raw: dict[str, Any], normalized: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    session_id, focus = raw.get("session_id"), normalized.get("focus")
    if not session_id:
        return
    current = conn.execute("SELECT * FROM focus_sessions WHERE session_id=?", (session_id,)).fetchone()
    if raw["device_id"] == "focuscube-c3-01":
        if current and current["installation_id"] != INSTALLATION_ID:
            raise _error(409, "session_installation_conflict", "session belongs to another installation")
        if current and current["c3_device_id"] not in (None, raw["device_id"]):
            raise _error(409, "session_device_conflict", "session already has another C3")
        if current:
            conn.execute("UPDATE focus_sessions SET c3_device_id=? WHERE session_id=?", (raw["device_id"], session_id))
        else:
            conn.execute("INSERT INTO focus_sessions VALUES(?,?,?,?,?,?,?,?)", (session_id, INSTALLATION_ID, "focuscube-eye-01", raw["device_id"], None, None, "provisional", None))
        return
    if not focus or not focus.get("valid"):
        return
    if not current:
        conn.execute("INSERT INTO focus_sessions VALUES(?,?,?,?,?,?,?,?)",
                     (session_id, INSTALLATION_ID, raw["device_id"], None, raw["ts"] if raw["event_type"] == "session_start" else None,
                      raw["ts"] if raw["event_type"] == "session_end" else None, focus["state"], focus.get("ended_reason")))
    elif current["eye_device_id"] != raw["device_id"]:
        raise _error(409, "session_device_conflict", "session belongs to another EYE")
    elif raw["event_type"] == "session_start":
        if current["started_at"] not in (None, raw["ts"]):
            raise _error(409, "session_id_conflict", "session start conflicts with existing fact")
        conn.execute("UPDATE focus_sessions SET started_at=?,state=? WHERE session_id=?", (raw["ts"], focus["state"], session_id))
    elif raw["event_type"] == "session_end":
        conn.execute("UPDATE focus_sessions SET ended_at=?,state=?,ended_reason=? WHERE session_id=?",
                     (raw["ts"], focus["state"], focus.get("ended_reason"), session_id))
    else:
        conn.execute("UPDATE focus_sessions SET state=? WHERE session_id=?", (focus["state"], session_id))


def installation_status(db: Database, now: int | None = None) -> dict[str, Any]:
    now = now or int(time.time())
    members = []
    telemetry: dict[str, Any] = {}
    availability: dict[str, Any] = {}
    with db.connect() as conn:
        for device_id, registration in REGISTRY.items():
            row = conn.execute(
                """SELECT e.received_at,e.ts,h.health_json FROM telemetry_events e
                   LEFT JOIN health_samples h ON h.event_id=e.id
                   WHERE e.device_id=? ORDER BY e.id DESC LIMIT 1""", (device_id,)
            ).fetchone()
            last_seen = int(row["received_at"]) if row else None
            health = json.loads(row["health_json"]) if row and row["health_json"] else None
            members.append({
                "device_id": device_id,
                "role": registration["role"],
                "online": bool(last_seen and now - last_seen <= MEMBER_ONLINE_TIMEOUT_S),
                "last_seen": last_seen,
                "online_timeout_s": MEMBER_ONLINE_TIMEOUT_S,
                "health": health,
            })
        queries = {
            "light": ("light_samples", "focuscube-c3-01"),
            "imu": ("imu_samples", "focuscube-eye-01"),
            "focus": ("focus_events", "focuscube-eye-01"),
            "edge": ("edge_results", "focuscube-eye-01"),
        }
        for name, (table, device_id) in queries.items():
            latest = conn.execute(
                f"""SELECT e.ts,e.received_at,e.session_id,t.* FROM telemetry_events e
                    JOIN {table} t ON t.event_id=e.id WHERE e.device_id=? ORDER BY e.id DESC LIMIT 1""", (device_id,)
            ).fetchone()
            if name == "edge":
                candidates = conn.execute(
                    """SELECT e.ts,e.received_at,e.session_id,t.* FROM telemetry_events e
                       JOIN edge_results t ON t.event_id=e.id WHERE e.device_id=? AND e.ts>0
                       ORDER BY e.ts DESC,e.id DESC""", (device_id,)
                ).fetchall()
                valid = next(
                    (row for row in candidates if row["environment_json"] and json.loads(row["environment_json"]).get("valid")),
                    None,
                )
            else:
                valid = conn.execute(
                    f"""SELECT e.ts,e.received_at,e.session_id,t.* FROM telemetry_events e
                        JOIN {table} t ON t.event_id=e.id WHERE e.device_id=? AND e.ts>0
                        AND t.valid=1 AND t.quality IN ('measured','derived')
                        ORDER BY e.ts DESC,e.id DESC LIMIT 1""", (device_id,)
                ).fetchone()
            availability[name] = _availability(name, latest, now)
            if valid:
                telemetry[name] = _status_block(name, dict(valid), device_id, now)
    online = any(member["online"] for member in members)
    ready = all(member["online"] for member in members)
    compat = _compat_device(telemetry, online)
    return {
        "ok": True, "now": now, "installation_id": INSTALLATION_ID, "view_id": INSTALLATION_ID,
        "active_device_id": INSTALLATION_ID, "online": online, "ready": ready,
        "availability": availability, "telemetry": telemetry, "members": members, "devices": [compat],
    }


def _availability(name: str, row: sqlite3.Row | None, now: int) -> dict[str, Any]:
    if not row:
        return {"state": "missing", "reason": "never_reported"}
    data = dict(row)
    if name == "edge":
        env = json.loads(data["environment_json"]) if data.get("environment_json") else None
        if not env or not env.get("valid"):
            return {"state": "invalid", "reason": (env or {}).get("invalid_reason", "latest_read_invalid")}
        quality = env.get("quality", "derived")
    else:
        if not data.get("valid"):
            return {"state": data.get("quality", "invalid"), "reason": data.get("invalid_reason", "latest_read_invalid")}
        quality = data.get("quality")
    stale = now - int(data["ts"]) > BUSINESS_DATA_FRESHNESS_S
    return {"state": "stale" if stale else ("estimated" if quality == "estimated" else "fresh"), "quality": quality, "ts": int(data["ts"])}


def _status_block(name: str, row: dict[str, Any], device_id: str, now: int) -> dict[str, Any]:
    base = {
        "valid": True,
        "source_device_id": device_id,
        "ts": int(row["ts"]),
        "stale": now - int(row["ts"]) > BUSINESS_DATA_FRESHNESS_S,
    }
    if name == "light":
        base.update(quality=row["quality"], sample_seq=row["sample_seq"], lux=row["lux"], label=row["server_label"] or row["device_label"], saturated=bool(row["saturated"]))
    elif name == "imu":
        base.update(quality=row["quality"], face=row["face"], mode=row["mode"], activity=row["activity"])
    elif name == "focus":
        base.update(quality=row["quality"], state=row["state"], remaining_s=row["remaining_s"], session_count=row["session_count"], session_id=row["session_id"], ended_reason=row["ended_reason"])
        if row["state"] in {"completed", "ended", "error"}:
            base["stale"] = False
    else:
        env = json.loads(row["environment_json"]) if row.get("environment_json") else None
        motion = json.loads(row["motion_json"]) if row.get("motion_json") else None
        base.update(quality="derived", environment=env, motion=motion)
    return base


def _compat_device(telemetry: dict[str, Any], online: bool) -> dict[str, Any]:
    face_map = {"+X": 0, "-X": 1, "+Y": 2, "-Y": 3, "+Z": 4, "-Z": 5, "move": 6, "unknown": -1}
    compatible = {key: dict(value) for key, value in telemetry.items() if key in {"light", "imu", "focus", "power"}}
    if "imu" in compatible:
        compatible["imu"]["face"] = face_map.get(compatible["imu"].get("face"), -1)
    compatible["power"] = {"valid": False, "quality": "missing", "battery_pct": 0, "charging": False}
    return {"device_id": INSTALLATION_ID, "source": "fusion", "online": online, **compatible, "telemetry": dict(compatible)}


def physical_status(db: Database, device_id: str, now: int | None = None) -> dict[str, Any]:
    registration = REGISTRY.get(device_id)
    if not registration:
        raise _error(404, "target_not_found", "status target does not exist")
    full = installation_status(db, now)
    member = next(item for item in full["members"] if item["device_id"] == device_id)
    return {"ok": True, "now": full["now"], "active_device_id": device_id, "device": member, "members": [member], "devices": [member]}


def event_timeseries(db: Database, metric: str, report_date: date) -> dict[str, Any]:
    allowed = {"light.lux", "imu.activity", "focus.remaining_s", "focus.state", "edge.environment.score"}
    if metric not in allowed:
        raise _error(400, "unsupported_metric", f"unsupported metric: {metric}")
    start, end = db.date_bounds(report_date)
    with db.connect() as conn:
        if metric == "focus.state":
            rows = conn.execute(
                """SELECT s.started_at,s.ended_at,s.state,s.session_id,s.eye_device_id
                   FROM focus_sessions s WHERE s.installation_id=? AND
                   COALESCE(s.ended_at,s.started_at,0)>=? AND COALESCE(s.started_at,0)<? ORDER BY s.started_at""",
                (INSTALLATION_ID, start, end),
            ).fetchall()
            return {"device_id": INSTALLATION_ID, "date": report_date.isoformat(), "metric": metric,
                    "segments": [{"start_ts": row["started_at"], "end_ts": row["ended_at"], "value": row["state"], "session_id": row["session_id"], "source_device_id": row["eye_device_id"]} for row in rows]}
        table, column, device = {
            "light.lux": ("light_samples", "lux", "focuscube-c3-01"),
            "imu.activity": ("imu_samples", "activity", "focuscube-eye-01"),
            "focus.remaining_s": ("focus_events", "remaining_s", "focuscube-eye-01"),
        }.get(metric, (None, None, None))
        if metric == "edge.environment.score":
            rows = conn.execute("""SELECT e.ts,e.device_id,r.environment_json FROM telemetry_events e JOIN edge_results r ON r.event_id=e.id
                                   WHERE e.installation_id=? AND e.ts>=? AND e.ts<? ORDER BY e.ts""", (INSTALLATION_ID, start, end)).fetchall()
            points = []
            for row in rows:
                env = json.loads(row["environment_json"]) if row["environment_json"] else {}
                if env.get("valid") and env.get("quality") == "derived" and isinstance(env.get("score"), (int, float)):
                    points.append({"ts": row["ts"], "value": env["score"], "source_device_id": row["device_id"]})
        else:
            rows = conn.execute(
                f"""SELECT e.ts,e.device_id,t.{column} value FROM telemetry_events e JOIN {table} t ON t.event_id=e.id
                    WHERE e.device_id=? AND e.ts>=? AND e.ts<? AND t.valid=1 AND t.quality='measured' ORDER BY e.ts""",
                (device, start, end),
            ).fetchall()
            points = [{"ts": row["ts"], "value": row["value"], "source_device_id": row["device_id"]} for row in rows]
    return {"device_id": INSTALLATION_ID, "date": report_date.isoformat(), "metric": metric, "points": points}


def daily_rows(db: Database, report_date: date) -> list[dict[str, Any]]:
    start, end = db.date_bounds(report_date)
    with db.connect() as conn:
        light = conn.execute("""SELECT e.ts,l.lux FROM telemetry_events e JOIN light_samples l ON l.event_id=e.id
                               WHERE e.device_id='focuscube-c3-01' AND e.ts>=? AND e.ts<? AND l.valid=1 AND l.quality='measured' AND l.saturated=0""", (start, end)).fetchall()
        imu = conn.execute("""SELECT e.ts,i.activity,i.mode FROM telemetry_events e JOIN imu_samples i ON i.event_id=e.id
                             WHERE e.device_id='focuscube-eye-01' AND e.ts>=? AND e.ts<? AND i.valid=1 AND i.quality='measured'""", (start, end)).fetchall()
        focus = conn.execute("""SELECT e.ts,f.remaining_s,f.session_count,f.state FROM telemetry_events e JOIN focus_events f ON f.event_id=e.id
                               WHERE e.device_id='focuscube-eye-01' AND e.ts>=? AND e.ts<? AND f.valid=1 AND f.quality='measured'""", (start, end)).fetchall()
    timestamps = sorted({row["ts"] for row in [*light, *imu, *focus]})
    rows = []
    for ts in timestamps:
        l = next((r for r in reversed(light) if r["ts"] <= ts), None)
        i = next((r for r in reversed(imu) if r["ts"] <= ts), None)
        f = next((r for r in reversed(focus) if r["ts"] <= ts), None)
        if not l:
            continue
        rows.append({"ts": ts, "lux": l["lux"], "imu_valid": bool(i), "activity": i["activity"] if i else 0,
                     "mode": i["mode"] if i else "unknown", "focus_valid": bool(f), "remaining_s": f["remaining_s"] if f else 0,
                     "session_count": f["session_count"] if f else 0, "focus_state": f["state"] if f else "unknown",
                     "power_valid": 0, "battery_pct": 0})
    return rows
