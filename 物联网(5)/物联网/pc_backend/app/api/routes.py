from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    status,
)

from ..schemas import (
    DeviceConfigUpdate,
    TelemetryIn,
)
from ..device_identity import describe_device
from ..services.reminders import create_rule_reminders
from ..services.reports import get_or_create_daily_report


router = APIRouter(prefix="/api/v1")


def _summary(row: dict[str, Any]) -> str:
    """生成设备状态摘要。

    focus 数据无效时，不把占位状态描述成真实专注状态。
    光照目前仍按数据库保存的 light_label 展示。
    """

    if bool(row["focus_valid"]):
        state_map = {
            "running": "专注中",
            "paused": "已暂停",
            "idle": "空闲",
        }

        focus_state = str(
            row["focus_state"]
        ).lower()

        state_text = state_map.get(
            focus_state,
            str(row["focus_state"]),
        )
    else:
        state_text = "专注数据未接入"

    label_map = {
        "suitable": "光照适宜",
        "too_dim": "光线偏暗",
        "too_bright": "光线偏亮",
    }

    light_label = str(row["light_label"])

    light_text = label_map.get(
        light_label,
        light_label,
    )

    return f"{state_text}，{light_text}"


@router.post(
    "/telemetry",
    status_code=status.HTTP_201_CREATED,
)
def receive_telemetry(
    payload: TelemetryIn,
    request: Request,
) -> dict[str, Any]:
    """接收并保存设备 telemetry。"""

    now = int(time.time())

    request.app.state.db.insert_telemetry(
        payload,
        received_at=now,
    )

    create_rule_reminders(
        request.app.state.db,
        payload,
        now,
    )

    return {
        "ok": True,
        "message": "telemetry accepted",
        "device_id": payload.device_id,
        "stored": True,
        "received_at": now,
    }


@router.get("/status")
def get_status(
    request: Request,
    device_id: str | None = None,
) -> dict[str, Any]:
    """获取设备最新状态。

    未提供 device_id 时，默认查询配置中的 active_device_id。
    显式提供 device_id 时，可以查询历史设备或其他设备。
    """

    now = int(time.time())

    active_device_id = (
        device_id
        or request.app.state.settings.active_device_id
    )

    rows = request.app.state.db.latest_devices(
        device_id=active_device_id,
    )

    devices: list[dict[str, Any]] = []

    for row in rows:
        config = request.app.state.db.get_config(
            row["device_id"]
        )

        online = (
            now - int(row["received_at"])
            <= int(config["online_timeout_s"])
        )

        light = {
            "lux": row["lux"],
            "label": row["light_label"],
        }
        imu = {
            "valid": bool(row["imu_valid"]),
            "face": row["face"],
            "mode": row["mode"],
            "activity": row["activity"],
        }
        focus = {
            "valid": bool(row["focus_valid"]),
            "state": row["focus_state"],
            "remaining_s": row["remaining_s"],
            "session_count": row["session_count"],
        }
        power = {
            "valid": bool(row["power_valid"]),
            "battery_pct": row["battery_pct"],
            "charging": bool(row["charging"]),
        }

        devices.append(
            {
                "device_id": row["device_id"],
                "source": row["source"],
                **describe_device(row["device_id"], row["source"]),
                "online": online,
                "last_seen": int(
                    row["received_at"]
                ),
                "data_ts": int(row["ts"]),
                "summary": _summary(row),
                # Keep the original top-level groups for P4 and existing
                # integrations.
                "light": light,
                "imu": imu,
                "focus": focus,
                "power": power,
                # D's Web dashboard consumes the same real values through a
                # nested compatibility object. Per-group valid flags remain
                # authoritative for subsystems that have not reported data.
                "telemetry": {
                    "valid": True,
                    "light": light,
                    "imu": imu,
                    "focus": focus,
                    "power": power,
                },
            }
        )

    return {
        "ok": True,
        "now": now,
        "active_device_id": active_device_id,
        "devices": devices,
    }


@router.get("/report/daily")
def get_daily_report(
    request: Request,
    device_id: str = Query(
        ...,
        min_length=1,
    ),
    date_value: date | None = Query(
        default=None,
        alias="date",
    ),
    refresh: bool = False,
) -> dict[str, Any]:
    """获取或重新生成指定设备的日报。"""

    report_date = (
        date_value
        or datetime.now(
            request.app.state.db.timezone
        ).date()
    )

    return get_or_create_daily_report(
        request.app.state.db,
        request.app.state.settings,
        device_id,
        report_date,
        refresh=refresh,
    )


@router.get("/reminders")
def get_reminders(
    request: Request,
    device_id: str = Query(
        ...,
        min_length=1,
    ),
    since: int = Query(
        default=0,
        ge=0,
    ),
) -> list[dict[str, Any]]:
    """获取指定设备的提醒。"""

    return request.app.state.db.get_reminders(
        device_id,
        since,
    )


@router.get("/timeseries")
def get_timeseries(
    request: Request,
    device_id: str = Query(
        ...,
        min_length=1,
    ),
    date_value: date | None = Query(
        default=None,
        alias="date",
    ),
    metric: str = Query(
        default="lux",
    ),
) -> dict[str, Any]:
    """获取指定设备当天的时间序列数据。

    光照始终参与时序展示。

    IMU、focus、power 指标只有对应 valid=true
    时才进入时间序列。
    """

    allowed = {
        "lux": "lux",
        "light.lux": "lux",
        "activity": "activity",
        "imu.activity": "activity",
        "battery_pct": "battery_pct",
        "power.battery_pct": "battery_pct",
        "remaining_s": "remaining_s",
        "focus.remaining_s": "remaining_s",
        "session_count": "session_count",
        "focus.session_count": "session_count",
        "focus.state": "focus_state",
    }

    if metric not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                "metric must be one of: "
                + ", ".join(allowed)
            ),
        )

    report_date = (
        date_value
        or datetime.now(
            request.app.state.db.timezone
        ).date()
    )

    rows = (
        request.app.state.db.telemetry_for_date(
            device_id,
            report_date,
        )
    )

    validity_columns = {
        "activity": "imu_valid",
        "imu.activity": "imu_valid",
        "remaining_s": "focus_valid",
        "focus.remaining_s": "focus_valid",
        "session_count": "focus_valid",
        "focus.session_count": "focus_valid",
        "focus.state": "focus_valid",
        "battery_pct": "power_valid",
        "power.battery_pct": "power_valid",
    }

    validity_column = validity_columns.get(
        metric
    )

    if validity_column is not None:
        rows = [
            row
            for row in rows
            if bool(row[validity_column])
        ]

    if metric == "focus.state":
        segments: list[dict[str, Any]] = []
        for row in rows:
            ts = int(row["ts"])
            state_value = str(row["focus_state"])
            if segments and segments[-1]["state"] == state_value:
                segments[-1]["end"] = max(
                    segments[-1]["end"], ts + 1
                )
            else:
                if segments:
                    segments[-1]["end"] = max(
                        segments[-1]["end"], ts
                    )
                segments.append(
                    {
                        "start": ts,
                        "end": ts + 1,
                        "state": state_value,
                    }
                )

        return {
            "device_id": device_id,
            "date": report_date.isoformat(),
            "metric": metric,
            "segments": segments,
        }

    database_column = allowed[metric]

    points = [
        {
            "ts": int(row["ts"]),
            "value": row[database_column],
        }
        for row in rows
    ]

    return {
        "device_id": device_id,
        "date": report_date.isoformat(),
        "metric": metric,
        "points": points,
    }


@router.get("/config")
def get_config(
    request: Request,
    device_id: str = Query(
        ...,
        min_length=1,
    ),
) -> dict[str, Any]:
    """获取指定设备配置。"""

    return request.app.state.db.get_config(
        device_id
    )


@router.put("/config")
def update_config(
    payload: DeviceConfigUpdate,
    request: Request,
    device_id: str = Query(
        ...,
        min_length=1,
    ),
) -> dict[str, Any]:
    """修改指定设备配置。"""

    updates = payload.model_dump(
        exclude_none=True
    )

    current = (
        request.app.state.db.get_config(
            device_id
        )
    )

    prospective = {
        **current,
        **updates,
    }

    if (
        float(
            prospective["light_min_lux"]
        )
        >= float(
            prospective["light_max_lux"]
        )
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "light_min_lux must be lower "
                "than light_max_lux"
            ),
        )

    return request.app.state.db.update_config(
        device_id,
        updates,
    )
