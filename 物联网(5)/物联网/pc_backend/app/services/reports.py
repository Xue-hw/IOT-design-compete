from __future__ import annotations

import hashlib
import logging
import time
from datetime import date
from typing import Any

from ..config import Settings
from ..database import Database
from .aggregation import aggregate_daily, build_fusion_context
from .llm import build_rule_fallback, call_cloud_llm
from .multinode import INSTALLATION_ID, daily_rows

logger = logging.getLogger(__name__)


def _save_ai_suggestions(
    db: Database,
    device_id: str,
    date_text: str,
    suggestions: list[str],
    now: int,
) -> None:
    """Expose cloud-model suggestions through the team's existing reminders API."""

    for suggestion in suggestions[:3]:
        digest = hashlib.sha1(f"{device_id}|{date_text}|{suggestion}".encode("utf-8")).hexdigest()[:10]
        db.add_reminder(
            reminder_id=f"ai-{digest}",
            device_id=device_id,
            created_at=now,
            reminder_type="ai_suggestion",
            text_value=suggestion,
            priority=1,
            ttl_s=86400,
        )


def get_or_create_daily_report(
    db: Database,
    settings: Settings,
    device_id: str,
    report_date: date,
    refresh: bool = False,
) -> dict[str, Any]:
    date_text = report_date.isoformat()
    if not refresh:
        cached = db.get_cached_report(device_id, date_text)
        if cached is not None:
            return {
                "device_id": device_id,
                "date": date_text,
                "report_text": cached["report_text"],
                "metrics": cached["metrics"],
                "suggestions": cached["suggestions"],
            }

    rows = (
        daily_rows(db, report_date)
        if device_id == INSTALLATION_ID
        else db.telemetry_for_date(device_id, report_date)
    )
    config = db.get_config(device_id)
    metrics = aggregate_daily(rows, config)
    fusion_context = build_fusion_context(metrics)

    generator = "rule_fallback"
    now = int(time.time())
    if metrics.get("sample_count", 0) == 0:
        report_text, suggestions = build_rule_fallback(metrics)
    else:
        try:
            report_text, suggestions = call_cloud_llm(
                settings,
                device_id,
                date_text,
                fusion_context,
            )
            generator = f"{settings.llm_provider}:{settings.llm_model}"
            _save_ai_suggestions(db, device_id, date_text, suggestions, now)
        except Exception as exc:  # cloud failure must not break the demo
            logger.warning("AI Gateway unavailable, using fallback: %s", exc)
            report_text, suggestions = build_rule_fallback(metrics)

    db.save_report(
        device_id=device_id,
        report_date=date_text,
        generated_at=now,
        generator=generator,
        report_text=report_text,
        metrics=metrics,
        suggestions=suggestions,
    )
    return {
        "device_id": device_id,
        "date": date_text,
        "report_text": report_text,
        "metrics": metrics,
        "suggestions": suggestions,
    }
