from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 FocusCube 智能光环境与专注管理助手。
你会收到结构化传感器日统计，其中可能包含光照、IMU 活动/姿态、专注计时和电量；缺失的类别表示尚无有效数据。
请只基于输入数据生成简洁、可执行、不过度推断的中文复盘。
必须返回 JSON，格式为：
{
  "report_text": "80-160 字中文复盘",
  "suggestions": ["建议1", "建议2"]
}
不要输出 Markdown，不要虚构未提供的健康、身份或环境信息。"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _chat_completions_endpoint(base_url: str) -> str:
    """Accept either the console base URL or the full chat-completions URL."""

    value = base_url.strip().rstrip("/")
    if not value:
        raise RuntimeError("FOCUSCUBE_LLM_BASE_URL is not configured")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def call_cloud_llm(
    settings: Settings,
    device_id: str,
    report_date: str,
    fusion_context: dict[str, Any],
) -> tuple[str, list[str]]:
    if settings.llm_provider != "volcengine_ai_gateway":
        raise RuntimeError("FOCUSCUBE_LLM_PROVIDER must be volcengine_ai_gateway")
    if not settings.llm_api_key.strip():
        raise RuntimeError("FOCUSCUBE_LLM_API_KEY is not configured")
    if not settings.llm_model.strip():
        raise RuntimeError("FOCUSCUBE_LLM_MODEL is not configured")

    endpoint = _chat_completions_endpoint(settings.llm_base_url)
    user_content = json.dumps(
        {
            "device_id": device_id,
            "date": report_date,
            "fused_sensor_summary": fusion_context,
        },
        ensure_ascii=False,
    )
    response = httpx.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
        },
        timeout=settings.llm_timeout_s,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    parsed = _extract_json(content)

    report_text = str(parsed.get("report_text", "")).strip()
    suggestions = [str(item).strip() for item in parsed.get("suggestions", []) if str(item).strip()]
    if not report_text:
        raise ValueError("AI Gateway response has no report_text")
    if not suggestions:
        suggestions = ["继续观察光照与专注状态的变化。"]
    logger.info(
        "AI Gateway report generated with provider=%s model=%s",
        settings.llm_provider,
        settings.llm_model,
    )
    return report_text, suggestions[:5]


def build_rule_fallback(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    if metrics.get("sample_count", 0) == 0:
        return "当天暂无可用于复盘的设备数据。", ["请先让 S3 上传 telemetry 数据。"]

    suitable_ratio = float(metrics.get("suitable_light_ratio", 0) or 0)
    avg_lux = float(metrics.get("avg_lux", 0) or 0)
    focus_minutes = metrics.get("focus_minutes")
    pomodoro_count = metrics.get("pomodoro_count")
    avg_activity = metrics.get("avg_activity")

    if suitable_ratio >= 0.75:
        light_sentence = "当天大部分时间光照处于适宜范围。"
    elif avg_lux < 200:
        light_sentence = "当天整体光照偏暗，需要改善桌面照明。"
    elif avg_lux > 500:
        light_sentence = "当天整体光照偏亮，可适当降低直射光。"
    else:
        light_sentence = "当天光照波动较明显，建议保持环境稳定。"

    sentences = [light_sentence]
    if avg_activity is not None:
        if float(avg_activity) <= 0.6:
            sentences.append("活动度整体较平稳。")
        else:
            sentences.append("活动度偏高，专注过程中可能存在较多移动。")

    if focus_minutes is not None and pomodoro_count is not None:
        sentences.append(
            f"累计完成约 {int(focus_minutes)} 分钟专注，"
            f"共记录 {int(pomodoro_count)} 个完成周期。"
        )

    suggestions: list[str] = []
    if suitable_ratio < 0.75:
        suggestions.append("将桌面照度尽量保持在配置的适宜范围内。")
    if avg_activity is not None and float(avg_activity) > 0.6:
        suggestions.append("专注时减少频繁移动，并在周期结束后集中休息。")
    if focus_minutes is not None:
        suggestions.append("每完成一个专注周期后休息 5 分钟。")
    if not suggestions:
        suggestions.append("继续记录真实传感器数据，等待更多有效样本后再复盘。")

    return "".join(sentences), suggestions[:3]
