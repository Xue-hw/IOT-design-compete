from __future__ import annotations

from collections import Counter
from statistics import fmean
from typing import Any


def _round(value: float, digits: int = 2) -> float:
    return round(float(value), digits)


def aggregate_daily(rows: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Fuse only valid observations into daily metrics."""

    if not rows:
        return {
            "sample_count": 0,
            "focus_minutes": None,
            "pomodoro_count": None,
            "avg_lux": 0,
            "min_lux": 0,
            "max_lux": 0,
            "suitable_light_ratio": 0,
            "avg_activity": None,
            "avg_battery_pct": None,
            "dominant_mode": None,
            "imu_sample_count": 0,
            "focus_sample_count": 0,
            "power_sample_count": 0,
        }

    min_lux = float(config["light_min_lux"])
    max_lux = float(config["light_max_lux"])
    session_minutes = int(config["focus_session_minutes"])

    # Light data is already real in the current test chain, so every row is used.
    lux_values = [float(row["lux"]) for row in rows]
    suitable_count = sum(min_lux <= value <= max_lux for value in lux_values)

    # Placeholder IMU/focus/power values must not enter aggregation.
    imu_rows = [row for row in rows if bool(row.get("imu_valid", 0))]
    focus_rows = [row for row in rows if bool(row.get("focus_valid", 0))]
    power_rows = [row for row in rows if bool(row.get("power_valid", 0))]

    avg_activity: float | None = None
    dominant_mode: str | None = None
    if imu_rows:
        activity_values = [float(row["activity"]) for row in imu_rows]
        modes = Counter(str(row["mode"]) for row in imu_rows)
        avg_activity = _round(fmean(activity_values), 3)
        dominant_mode = modes.most_common(1)[0][0]

    focus_minutes: int | None = None
    pomodoro_count: int | None = None
    if focus_rows:
        session_count = max(int(row["session_count"]) for row in focus_rows)
        latest_focus = focus_rows[-1]
        current_elapsed_minutes = 0.0
        if str(latest_focus["focus_state"]).lower() == "running":
            current_elapsed_minutes = max(
                0.0,
                session_minutes - int(latest_focus["remaining_s"]) / 60.0,
            )
        focus_minutes = int(
            round(session_count * session_minutes + current_elapsed_minutes)
        )
        pomodoro_count = session_count

    avg_battery_pct: float | None = None
    if power_rows:
        battery_values = [int(row["battery_pct"]) for row in power_rows]
        avg_battery_pct = _round(fmean(battery_values), 1)

    return {
        "sample_count": len(rows),
        "focus_minutes": focus_minutes,
        "pomodoro_count": pomodoro_count,
        "avg_lux": _round(fmean(lux_values), 1),
        "min_lux": _round(min(lux_values), 1),
        "max_lux": _round(max(lux_values), 1),
        "suitable_light_ratio": _round(suitable_count / len(rows), 3),
        "avg_activity": avg_activity,
        "avg_battery_pct": avg_battery_pct,
        "dominant_mode": dominant_mode,
        "imu_sample_count": len(imu_rows),
        "focus_sample_count": len(focus_rows),
        "power_sample_count": len(power_rows),
    }


def build_fusion_context(metrics: dict[str, Any]) -> dict[str, Any]:
    """Send only sensor categories that contain valid observations."""

    context: dict[str, Any] = {
        "light_environment": {
            "average_lux": metrics.get("avg_lux", 0),
            "minimum_lux": metrics.get("min_lux", 0),
            "maximum_lux": metrics.get("max_lux", 0),
            "suitable_ratio": metrics.get("suitable_light_ratio", 0),
        }
    }

    if int(metrics.get("imu_sample_count", 0)) > 0:
        context["motion_and_posture"] = {
            "average_activity": metrics.get("avg_activity"),
            "dominant_mode": metrics.get("dominant_mode"),
        }

    if int(metrics.get("focus_sample_count", 0)) > 0:
        context["focus_behavior"] = {
            "focus_minutes": metrics.get("focus_minutes"),
            "pomodoro_count": metrics.get("pomodoro_count"),
        }

    if int(metrics.get("power_sample_count", 0)) > 0:
        context["device_power"] = {
            "average_battery_pct": metrics.get("avg_battery_pct"),
        }

    return context
