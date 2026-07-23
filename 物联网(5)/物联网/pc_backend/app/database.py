from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from .schemas import TelemetryIn


DEFAULT_CONFIG = {
    "light_min_lux": 200.0,
    "light_max_lux": 500.0,
    "low_battery_pct": 20,
    "online_timeout_s": 30,
    "focus_session_minutes": 25,
}


class Database:
    def __init__(self, path: Path, timezone_name: str) -> None:
        self.path = path
        self.timezone = ZoneInfo(timezone_name)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    lux REAL NOT NULL,
                    light_label TEXT NOT NULL,
                    face INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    activity REAL NOT NULL,
                    imu_valid INTEGER NOT NULL DEFAULT 0,
                    focus_state TEXT NOT NULL,
                    remaining_s INTEGER NOT NULL,
                    session_count INTEGER NOT NULL,
                    focus_valid INTEGER NOT NULL DEFAULT 0,
                    battery_pct INTEGER NOT NULL,
                    charging INTEGER NOT NULL,
                    power_valid INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts
                    ON telemetry(device_id, ts);
                CREATE INDEX IF NOT EXISTS idx_telemetry_received
                    ON telemetry(received_at);

                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    ttl_s INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_device_created
                    ON reminders(device_id, created_at);

                CREATE TABLE IF NOT EXISTS reports (
                    device_id TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    generated_at INTEGER NOT NULL,
                    generator TEXT NOT NULL,
                    report_text TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    suggestions_json TEXT NOT NULL,
                    PRIMARY KEY(device_id, report_date)
                );

                CREATE TABLE IF NOT EXISTS device_config (
                    device_id TEXT PRIMARY KEY,
                    light_min_lux REAL NOT NULL,
                    light_max_lux REAL NOT NULL,
                    low_battery_pct INTEGER NOT NULL,
                    online_timeout_s INTEGER NOT NULL,
                    focus_session_minutes INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS device_registry (
                    device_id TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    source TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    installation_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    boot_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    ts INTEGER NOT NULL,
                    received_at INTEGER NOT NULL,
                    time_quality TEXT NOT NULL DEFAULT 'device',
                    session_id TEXT,
                    business_accepted INTEGER NOT NULL DEFAULT 0,
                    raw_payload_json TEXT NOT NULL,
                    normalized_payload_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_events_installation_ts
                    ON telemetry_events(installation_id, ts);
                CREATE INDEX IF NOT EXISTS idx_events_session_ts
                    ON telemetry_events(session_id, ts);
                CREATE TABLE IF NOT EXISTS light_samples (
                    event_id INTEGER PRIMARY KEY,
                    valid INTEGER NOT NULL, quality TEXT NOT NULL,
                    invalid_reason TEXT, missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    sample_seq INTEGER, lux REAL, device_label TEXT, server_label TEXT,
                    sensor TEXT, saturated INTEGER, calibrated INTEGER,
                    gain REAL, integration_ms REAL,
                    FOREIGN KEY(event_id) REFERENCES telemetry_events(id)
                );
                CREATE TABLE IF NOT EXISTS imu_samples (
                    event_id INTEGER PRIMARY KEY,
                    valid INTEGER NOT NULL, quality TEXT NOT NULL,
                    invalid_reason TEXT, missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    face TEXT, mode TEXT, activity REAL,
                    FOREIGN KEY(event_id) REFERENCES telemetry_events(id)
                );
                CREATE TABLE IF NOT EXISTS focus_events (
                    event_id INTEGER PRIMARY KEY,
                    valid INTEGER NOT NULL, quality TEXT NOT NULL,
                    invalid_reason TEXT, missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    state TEXT, remaining_s INTEGER, session_count INTEGER, ended_reason TEXT,
                    FOREIGN KEY(event_id) REFERENCES telemetry_events(id)
                );
                CREATE TABLE IF NOT EXISTS edge_results (
                    event_id INTEGER PRIMARY KEY,
                    environment_json TEXT, motion_json TEXT,
                    FOREIGN KEY(event_id) REFERENCES telemetry_events(id)
                );
                CREATE TABLE IF NOT EXISTS power_samples (
                    event_id INTEGER PRIMARY KEY,
                    valid INTEGER NOT NULL, quality TEXT NOT NULL,
                    invalid_reason TEXT, missing_fields_json TEXT NOT NULL DEFAULT '[]',
                    battery_pct INTEGER, charging INTEGER,
                    FOREIGN KEY(event_id) REFERENCES telemetry_events(id)
                );
                CREATE TABLE IF NOT EXISTS health_samples (
                    event_id INTEGER PRIMARY KEY,
                    health_json TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES telemetry_events(id)
                );
                CREATE TABLE IF NOT EXISTS focus_sessions (
                    session_id TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL,
                    eye_device_id TEXT NOT NULL,
                    c3_device_id TEXT,
                    started_at INTEGER,
                    ended_at INTEGER,
                    state TEXT NOT NULL,
                    ended_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY, applied_at INTEGER NOT NULL
                );
                """
            )
            db.executemany(
                """INSERT INTO device_registry(device_id, installation_id, role, source, enabled)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(device_id) DO UPDATE SET
                     installation_id=excluded.installation_id, role=excluded.role,
                     source=excluded.source, enabled=1""",
                [
                    ("focuscube-eye-01", "focuscube-base-01", "edge_controller", "s3-eye-edge"),
                    ("focuscube-c3-01", "focuscube-base-01", "light_sensor", "c3-as7341"),
                ],
            )

            # 兼容已有 SQLite 数据库：为旧 telemetry 表补充有效性字段。
            existing_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(telemetry)").fetchall()
            }
            validity_columns = {
                "imu_valid": "INTEGER NOT NULL DEFAULT 0",
                "focus_valid": "INTEGER NOT NULL DEFAULT 0",
                "power_valid": "INTEGER NOT NULL DEFAULT 0",
            }
            for column_name, definition in validity_columns.items():
                if column_name not in existing_columns:
                    db.execute(
                        f"ALTER TABLE telemetry ADD COLUMN {column_name} {definition}"
                    )

    def _ensure_config(self, db: sqlite3.Connection, device_id: str) -> None:
        db.execute(
            """
            INSERT OR IGNORE INTO device_config(
                device_id, light_min_lux, light_max_lux,
                low_battery_pct, online_timeout_s, focus_session_minutes
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                DEFAULT_CONFIG["light_min_lux"],
                DEFAULT_CONFIG["light_max_lux"],
                DEFAULT_CONFIG["low_battery_pct"],
                DEFAULT_CONFIG["online_timeout_s"],
                DEFAULT_CONFIG["focus_session_minutes"],
            ),
        )

    def insert_telemetry(self, item: TelemetryIn, received_at: int) -> int:
        payload = item.model_dump()
        report_date = datetime.fromtimestamp(item.ts, self.timezone).date().isoformat()
        with self._lock, self.connect() as db:
            self._ensure_config(db, item.device_id)
            cursor = db.execute(
                """
                INSERT INTO telemetry(
                    device_id, source, ts, received_at, lux, light_label,
                    face, mode, activity, imu_valid,
                    focus_state, remaining_s, session_count, focus_valid,
                    battery_pct, charging, power_valid, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.device_id,
                    item.source,
                    item.ts,
                    received_at,
                    item.light.lux,
                    item.light.label,
                    item.imu.face,
                    item.imu.mode,
                    item.imu.activity,
                    1 if item.imu.valid else 0,
                    item.focus.state,
                    item.focus.remaining_s,
                    item.focus.session_count,
                    1 if item.focus.valid else 0,
                    item.power.battery_pct,
                    1 if item.power.charging else 0,
                    1 if item.power.valid else 0,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            # 新数据进入后，使对应日期的日报缓存失效。
            db.execute(
                "DELETE FROM reports WHERE device_id = ? AND report_date = ?",
                (item.device_id, report_date),
            )
            return int(cursor.lastrowid)

    def latest_devices(self, device_id: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE t.device_id = ?" if device_id else ""
        params: tuple[Any, ...] = (device_id,) if device_id else ()
        query = f"""
            SELECT t.*
            FROM telemetry t
            JOIN (
                SELECT device_id, MAX(id) AS max_id
                FROM telemetry
                GROUP BY device_id
            ) latest ON latest.max_id = t.id
            {where}
            ORDER BY t.device_id
        """
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def date_bounds(self, report_date: date) -> tuple[int, int]:
        start = datetime.combine(report_date, time.min, tzinfo=self.timezone)
        end = datetime.combine(report_date, time.max, tzinfo=self.timezone)
        return int(start.timestamp()), int(end.timestamp()) + 1

    def telemetry_for_date(self, device_id: str, report_date: date) -> list[dict[str, Any]]:
        start_ts, end_ts = self.date_bounds(report_date)
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT * FROM telemetry
                WHERE device_id = ? AND ts >= ? AND ts < ?
                ORDER BY ts ASC, id ASC
                """,
                (device_id, start_ts, end_ts),
            ).fetchall()
            return [dict(row) for row in rows]

    def add_reminder(
        self,
        reminder_id: str,
        device_id: str,
        created_at: int,
        reminder_type: str,
        text_value: str,
        priority: int,
        ttl_s: int,
    ) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO reminders(
                    id, device_id, created_at, type, text, priority, ttl_s
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (reminder_id, device_id, created_at, reminder_type, text_value, priority, ttl_s),
            )

    def has_recent_reminder(self, device_id: str, reminder_type: str, since: int) -> bool:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT 1 FROM reminders
                WHERE device_id = ? AND type = ? AND created_at >= ?
                LIMIT 1
                """,
                (device_id, reminder_type, since),
            ).fetchone()
            return row is not None

    def get_reminders(self, device_id: str, since: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, type, text, priority, ttl_s, created_at
                FROM reminders
                WHERE device_id = ? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (device_id, since),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_config(self, device_id: str) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            self._ensure_config(db, device_id)
            row = db.execute(
                "SELECT * FROM device_config WHERE device_id = ?", (device_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def update_config(self, device_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self.connect() as db:
            self._ensure_config(db, device_id)
            if updates:
                columns = ", ".join(f"{name} = ?" for name in updates)
                values = list(updates.values()) + [device_id]
                db.execute(f"UPDATE device_config SET {columns} WHERE device_id = ?", values)
            row = db.execute(
                "SELECT * FROM device_config WHERE device_id = ?", (device_id,)
            ).fetchone()
            assert row is not None
            return dict(row)

    def get_cached_report(self, device_id: str, report_date: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM reports WHERE device_id = ? AND report_date = ?",
                (device_id, report_date),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["metrics"] = json.loads(result.pop("metrics_json"))
            result["suggestions"] = json.loads(result.pop("suggestions_json"))
            return result

    def save_report(
        self,
        device_id: str,
        report_date: str,
        generated_at: int,
        generator: str,
        report_text: str,
        metrics: dict[str, Any],
        suggestions: list[str],
    ) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                """
                INSERT INTO reports(
                    device_id, report_date, generated_at, generator,
                    report_text, metrics_json, suggestions_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, report_date) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    generator = excluded.generator,
                    report_text = excluded.report_text,
                    metrics_json = excluded.metrics_json,
                    suggestions_json = excluded.suggestions_json
                """,
                (
                    device_id,
                    report_date,
                    generated_at,
                    generator,
                    report_text,
                    json.dumps(metrics, ensure_ascii=False),
                    json.dumps(suggestions, ensure_ascii=False),
                ),
            )
