#!/usr/bin/env python3
"""Browser smoke test for the real-interface FocusCube D dashboard."""
from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = "http://192.168.1.165:8000"


def inline_app() -> str:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="styles.css" />', f"<style>{css}</style>")
    html = html.replace('<script src="app.js"></script>', f"<script>{app}</script>")
    html = html.replace('<link rel="icon" href="assets/favicon.svg" type="image/svg+xml" />', "")
    return html


def series(metric: str) -> dict:
    now = int(time.time())
    values = {
        "light.lux": [320, 340, 365, 390, 410, 426],
        "imu.activity": [0.18, 0.21, 0.22, 0.27, 0.24, 0.26],
        "power.battery_pct": [84, 84, 83, 83, 82, 82],
    }[metric]
    return {"metric": metric, "points": [{"ts": now - (len(values) - i) * 10, "value": value} for i, value in enumerate(values)]}


def main() -> None:
    status_calls = {"count": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route_handler(route):
            url = route.request.url
            now = int(time.time())
            headers = {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"}
            if url == f"{API}/api/v1/status":
                status_calls["count"] += 1
                phase = status_calls["count"]
                valid = phase != 2
                telemetry = {
                    "light": {"lux": 406 if phase == 1 else 426 if valid else 0, "label": "suitable" if valid else "unknown"},
                    "imu": {"face": 2 if valid else 0, "activity": 0.24 if phase == 1 else 0.26 if valid else 0},
                    "focus": {"state": "running", "remaining_s": 730, "session_count": 4},
                    "power": {"battery_pct": 83 if phase == 1 else 82 if valid else 0, "charging": False},
                }
                if phase >= 2:
                    telemetry["valid"] = valid
                payload = {
                    "ok": True,
                    "now": now,
                    "devices": [
                        {"device_id": "focuscube-s3-01", "source": "s3", "online": False, "last_seen": 0, "summary": "S3 等待实物接入", "telemetry": telemetry},
                        {"device_id": "focuscube-p4-01", "source": "p4", "online": True, "last_seen": now - 1, "summary": "七寸屏展示正常"},
                    ],
                }
                route.fulfill(status=200, headers=headers, body=json.dumps(payload, ensure_ascii=False))
            elif "/api/v1/report/daily" in url:
                payload = {
                    "device_id": "focuscube-s3-01",
                    "date": "2026-07-15",
                    "report_text": "后端真实日报已返回。本次统计仅使用有效数据。",
                    "metrics": {"focus_minutes": 96, "pomodoro_count": 4, "avg_lux": 402, "suitable_light_ratio": 0.88},
                    "suggestions": ["继续保持当前光照。", "完成本轮后短暂休息。"],
                    "generated_at": now,
                }
                route.fulfill(status=200, headers=headers, body=json.dumps(payload, ensure_ascii=False))
            elif "/api/v1/reminders" in url:
                route.fulfill(status=200, headers=headers, body=json.dumps([{"id": "r1", "type": "focus", "text": "完成本轮后休息。", "priority": 1, "ttl_s": 120, "ts": now}], ensure_ascii=False))
            elif "metric=focus.state" in url:
                payload = {"metric": "focus.state", "segments": [{"start": now - 1800, "end": now - 300, "state": "focus"}, {"start": now - 300, "end": now, "state": "break"}]}
                route.fulfill(status=200, headers=headers, body=json.dumps(payload))
            elif "/api/v1/timeseries" in url:
                metric = next(key for key in ["light.lux", "imu.activity", "power.battery_pct"] if key.replace(".", "%2E") in url or f"metric={key}" in url)
                route.fulfill(status=200, headers=headers, body=json.dumps(series(metric)))
            else:
                route.abort()

        page.route(f"{API}/**", route_handler)
        page.set_content(inline_app(), wait_until="load")
        page.wait_for_timeout(650)

        # First status response omits valid: it must be treated as true.
        assert page.locator("#metricLux").inner_text() == "406"
        assert page.locator("#stripBattery").inner_text() == "83%"
        assert "face 2" in page.locator("#deviceList").inner_text()
        assert "离线（当前正常）" in page.locator("#deviceList").inner_text()

        # Second poll returns valid:false: placeholder zeros must disappear.
        page.wait_for_timeout(1650)
        assert "等待真实数据" in page.locator("#heroSummary").inner_text()
        assert page.locator("#metricLux").inner_text() == "--"
        assert page.locator("#gaugeBattery").inner_text() == "--"
        device_text = page.locator("#deviceList").inner_text()
        assert "face 0" not in device_text
        assert "电量 0%" not in device_text

        # Third poll returns valid:true and restores actual values.
        page.wait_for_timeout(2050)
        assert page.locator("#metricLux").inner_text() == "426"
        assert page.locator("#stripBattery").inner_text() == "82%"
        assert "face 2" in page.locator("#deviceList").inner_text()
        assert int(page.locator("#frameCounter").inner_text()) >= 3

        page.locator('.nav-item[data-page="trends"]').click()
        page.wait_for_timeout(250)
        assert page.locator("#trendChart svg").count() == 1
        assert "426" in page.locator("#gaugeLux").inner_text()
        page.screenshot(path=str(ROOT / "preview" / "trends_real_api.png"), full_page=True)

        page.locator('.nav-item[data-page="report"]').click()
        page.wait_for_timeout(250)
        assert "后端真实日报" in page.locator("#fullReportText").inner_text()
        assert "100" in page.locator("#dailyScore").inner_text()
        page.screenshot(path=str(ROOT / "preview" / "report_real_api.png"), full_page=True)

        page.locator('.nav-item[data-page="diagnostics"]').click()
        page.locator("#runDiagnosticsBtn").click()
        page.wait_for_timeout(800)
        assert "状态接口已连接" in page.locator("#diagResult").inner_text()
        assert page.locator("#requestLog .log-entry").count() >= 4
        page.screenshot(path=str(ROOT / "preview" / "diagnostics_real_api.png"), full_page=True)

        page.locator('.nav-item[data-page="overview"]').click()
        page.wait_for_timeout(250)
        assert not errors, errors
        page.screenshot(path=str(ROOT / "preview" / "dashboard_real_api.png"), full_page=True)
        browser.close()
    print("Real-interface smoke test passed")


if __name__ == "__main__":
    main()
