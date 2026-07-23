#!/usr/bin/env python3
"""Browser smoke test for the FocusCube multi-node installation view."""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
API = "http://82.156.238.244/focuscube"


def inline_app() -> str:
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="styles.css" />', f"<style>{css}</style>")
    html = html.replace('<script src="app.js"></script>', f"<script>{app}</script>")
    html = html.replace('<link rel="icon" href="assets/favicon.svg" type="image/svg+xml" />', "")
    return html


def main() -> None:
    now = int(time.time())
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route_handler(route):
            parsed = urlparse(route.request.url)
            api_path = parsed.path.removeprefix("/focuscube")
            query = parse_qs(parsed.query)
            headers = {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"}
            if api_path == "/api/v1/status":
                assert query["installation_id"] == ["focuscube-base-01"]
                payload = {
                    "ok": True, "now": now, "installation_id": "focuscube-base-01",
                    "view_id": "focuscube-base-01", "online": True, "ready": True,
                    "availability": {
                        "light": {"state": "fresh", "quality": "measured"},
                        "imu": {"state": "fresh", "quality": "measured"},
                        "focus": {"state": "fresh", "quality": "measured"},
                        "edge": {"state": "fresh", "quality": "derived"},
                    },
                    "telemetry": {
                        "light": {"valid": True, "quality": "measured", "lux": 406, "label": "suitable", "source_device_id": "focuscube-c3-01", "ts": now, "stale": False},
                        "imu": {"valid": True, "quality": "measured", "face": "+X", "mode": "focus", "activity": .24, "source_device_id": "focuscube-eye-01", "ts": now, "stale": False},
                        "focus": {"valid": True, "quality": "measured", "state": "running", "remaining_s": 730, "session_count": 4, "source_device_id": "focuscube-eye-01", "ts": now, "stale": False},
                        "edge": {"valid": True, "quality": "derived", "source_device_id": "focuscube-eye-01", "ts": now, "stale": False,
                                 "environment": {"valid": True, "quality": "derived", "state": "suitable", "trend": "stable", "score": .86, "confidence": .93}},
                    },
                    "members": [
                        {"device_id": "focuscube-eye-01", "role": "edge_controller", "online": True, "last_seen": now, "health": {"run_state": "ACTIVE", "c3_connected": True, "c3_control_ok": True}},
                        {"device_id": "focuscube-c3-01", "role": "light_sensor", "online": True, "last_seen": now, "health": {"run_state": "FOCUS_ACTIVE"}},
                    ],
                }
            elif api_path == "/api/v1/report/daily":
                assert query["device_id"] == ["focuscube-base-01"]
                payload = {"device_id": "focuscube-base-01", "date": "2026-07-23", "report_text": "融合日报已返回。",
                           "metrics": {"focus_minutes": 96, "pomodoro_count": 4, "avg_lux": 402, "suitable_light_ratio": .88},
                           "suggestions": ["继续保持当前光照。"], "generated_at": now}
            elif api_path == "/api/v1/reminders":
                payload = []
            elif api_path == "/api/v1/timeseries":
                metric = query["metric"][0]
                if metric == "focus.state":
                    payload = {"metric": metric, "segments": [{"start_ts": now - 900, "end_ts": now, "value": "running", "session_id": "eye-test", "source_device_id": "focuscube-eye-01"}]}
                else:
                    values = {"light.lux": [380, 406], "imu.activity": [.2, .24], "edge.environment.score": [.8, .86]}[metric]
                    source = "focuscube-c3-01" if metric == "light.lux" else "focuscube-eye-01"
                    payload = {"metric": metric, "points": [{"ts": now - 10, "value": values[0], "source_device_id": source}, {"ts": now, "value": values[1], "source_device_id": source}]}
            else:
                route.abort()
                return
            route.fulfill(status=200, headers=headers, body=json.dumps(payload, ensure_ascii=False))

        page.route(f"{API}/**", route_handler)
        page.set_content(inline_app(), wait_until="load")
        page.wait_for_timeout(900)
        assert page.locator("#deviceSelect").input_value() == "focuscube-base-01"
        assert page.locator("#metricLux").inner_text() == "406"
        assert page.locator("#stripBattery").inner_text() == "86%"
        member_text = page.locator("#deviceList").inner_text()
        assert "focuscube-eye-01" in member_text and "focuscube-c3-01" in member_text
        assert "控制 ACK 正常" in member_text
        page.locator('.nav-item[data-page="trends"]').click()
        page.wait_for_timeout(200)
        assert page.locator("#trendChart svg").count() == 1
        page.locator('[data-metric="edge.environment.score"]').click()
        page.wait_for_timeout(100)
        assert "环境适宜度" in page.locator("#trendChartTitle").inner_text()
        page.locator('.nav-item[data-page="report"]').click()
        page.wait_for_timeout(200)
        assert "融合日报" in page.locator("#fullReportText").inner_text()
        assert not errors, errors
        browser.close()
    print("Multi-node Web smoke test passed")


if __name__ == "__main__":
    main()
