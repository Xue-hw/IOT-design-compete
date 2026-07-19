from __future__ import annotations

import uuid

from ..database import Database
from ..schemas import TelemetryIn


REMINDER_COOLDOWN_S = 60


def create_rule_reminders(db: Database, item: TelemetryIn, now: int) -> list[dict]:
    """Create deterministic real-time reminders for S3/P4/Web polling."""

    config = db.get_config(item.device_id)
    candidates: list[tuple[str, str, int, int]] = []

    if item.light.lux < float(config["light_min_lux"]):
        candidates.append(("too_dim", "当前光线偏暗，建议打开台灯。", 2, 120))
    elif item.light.lux > float(config["light_max_lux"]):
        candidates.append(("too_bright", "当前光线偏亮，建议降低灯光或调整位置。", 2, 120))

    if (
        item.power.valid
        and item.power.battery_pct <= int(config["low_battery_pct"])
        and not item.power.charging
    ):
        candidates.append(("low_battery", "设备电量较低，请及时充电。", 3, 300))

    if (
        item.focus.valid
        and item.focus.state.lower() == "running"
        and item.focus.remaining_s == 0
    ):
        candidates.append(("take_break", "本轮专注已结束，建议休息 5 分钟。", 2, 300))

    created: list[dict] = []
    for reminder_type, text_value, priority, ttl_s in candidates:
        if db.has_recent_reminder(
            item.device_id,
            reminder_type,
            now - REMINDER_COOLDOWN_S,
        ):
            continue
        reminder_id = f"r-{uuid.uuid4().hex[:8]}"
        db.add_reminder(
            reminder_id,
            item.device_id,
            now,
            reminder_type,
            text_value,
            priority,
            ttl_s,
        )
        created.append(
            {
                "id": reminder_id,
                "type": reminder_type,
                "text": text_value,
                "priority": priority,
                "ttl_s": ttl_s,
                "created_at": now,
            }
        )
    return created
