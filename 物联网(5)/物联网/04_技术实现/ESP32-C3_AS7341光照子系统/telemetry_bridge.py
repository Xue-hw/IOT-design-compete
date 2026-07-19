#!/usr/bin/env python3
"""Bridge ESP32-C3 AS7341 TCP samples to the FocusCube HTTP API.

The C3 firmware emits one JSON spectrum sample per line on TCP port 3333.
This program keeps the real AS7341 light channels, marks unavailable S3
fields invalid, and optionally POSTs the result to ``/api/v1/telemetry``.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_C3_PORT = 3333
DEFAULT_TELEMETRY_PATH = "/api/v1/telemetry"
DEFAULT_MOCK_LUX_SCALE = 0.15
DEFAULT_DEVICE_ID = "focuscube-c3-proxy-01"
DEFAULT_SOURCE = "c3-as7341-proxy"
GAIN_MULTIPLIERS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0)
REFERENCE_GAIN = 256.0
REFERENCE_INTEGRATION_MS = 50.04
REQUIRED_SPECTRUM_FIELDS = (
    "seq", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "Clear",
    "NIR", "gain_code", "atime", "astep",
)


def _require_number(sample: dict[str, Any], field: str) -> float:
    if field not in sample:
        raise ValueError(f"missing spectrum field: {field}")
    value = sample[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"spectrum field {field} must be numeric")
    if value < 0:
        raise ValueError(f"spectrum field {field} must be non-negative")
    return float(value)


def validate_spectrum(sample: dict[str, Any]) -> None:
    """Validate the fields emitted by ``C3_AS7341_WiFiTCP.ino``."""

    for field in REQUIRED_SPECTRUM_FIELDS:
        _require_number(sample, field)
    if "saturated" not in sample or not isinstance(sample["saturated"], bool):
        raise ValueError("spectrum field saturated must be boolean")
    gain_multiplier(sample["gain_code"])
    integration_time_ms(sample["atime"], sample["astep"])


def gain_multiplier(gain_code: Any) -> float:
    if isinstance(gain_code, bool) or not isinstance(gain_code, (int, float)):
        raise ValueError("gain_code must be an integer from 0 to 10")
    if int(gain_code) != gain_code or not 0 <= int(gain_code) < len(GAIN_MULTIPLIERS):
        raise ValueError("gain_code must be an integer from 0 to 10")
    return GAIN_MULTIPLIERS[int(gain_code)]


def integration_time_ms(atime: Any, astep: Any) -> float:
    for name, value, maximum in (("atime", atime, 255), ("astep", astep, 65535)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be an integer")
        if int(value) != value or not 0 <= int(value) <= maximum:
            raise ValueError(f"{name} is outside the AS7341 register range")
    if int(atime) == 0 and int(astep) == 0:
        raise ValueError("atime and astep cannot both be zero")
    return (int(atime) + 1) * (int(astep) + 1) * 2.78 / 1000.0


def adc_full_scale(atime: Any, astep: Any) -> int:
    integration_time_ms(atime, astep)
    return min(65535, (int(atime) + 1) * (int(astep) + 1))


def _weighted_raw_proxy(sample: dict[str, Any]) -> float:
    return (
        0.2 * _require_number(sample, "F4")
        + 0.7 * _require_number(sample, "F5")
        + 0.1 * _require_number(sample, "F7")
    )


def compute_basic_counts_proxy(sample: dict[str, Any]) -> float:
    """Normalize weighted channels for the applied gain and integration time."""

    validate_spectrum(sample)
    gain = gain_multiplier(sample["gain_code"])
    tint_ms = integration_time_ms(sample["atime"], sample["astep"])
    return _weighted_raw_proxy(sample) / (gain * tint_ms)


def compute_lux_proxy(sample: dict[str, Any]) -> float:
    """Return a reference-normalized, currently uncalibrated light proxy."""

    return round(
        compute_basic_counts_proxy(sample) * REFERENCE_GAIN * REFERENCE_INTEGRATION_MS,
        6,
    )


def classify_light(
    lux_value: float, dim_threshold: float, bright_threshold: float
) -> str:
    if dim_threshold >= bright_threshold:
        raise ValueError("dim threshold must be lower than bright threshold")
    if lux_value < dim_threshold:
        return "too_dim"
    if lux_value > bright_threshold:
        return "too_bright"
    return "suitable"


def build_telemetry(
    spectrum: dict[str, Any],
    *,
    timestamp: int,
    device_id: str,
    source: str,
    lux_scale: float,
    dim_threshold: float,
    bright_threshold: float,
    calibrated: bool = False,
    strict_contract: bool = False,
) -> dict[str, Any]:
    """Convert one spectrum sample to the current FocusCube telemetry shape."""

    if lux_scale <= 0:
        raise ValueError("lux scale must be positive")
    basic_counts_proxy = round(compute_basic_counts_proxy(spectrum), 6)
    proxy = round(compute_lux_proxy(spectrum), 3)
    lux_value = round(proxy * lux_scale, 3)
    gain = gain_multiplier(spectrum["gain_code"])
    tint_ms = round(integration_time_ms(spectrum["atime"], spectrum["astep"]), 3)
    full_scale = adc_full_scale(spectrum["atime"], spectrum["astep"])
    light: dict[str, Any] = {
        "lux": lux_value,
        "label": classify_light(lux_value, dim_threshold, bright_threshold),
    }
    if not strict_contract:
        light.update(
            {
                "calibrated": calibrated,
                "proxy": proxy,
                "basic_counts_proxy": basic_counts_proxy,
                "sensor": "AS7341",
                "saturated": spectrum["saturated"],
                "gain": gain,
                "integration_ms": tint_ms,
                "full_scale": full_scale,
            }
        )

    return {
        "device_id": device_id,
        "source": source,
        "ts": int(timestamp),
        "light": light,
        "imu": {"valid": False, "face": 0, "mode": "unknown", "activity": 0.0},
        "focus": {
            "valid": False,
            "state": "idle",
            "remaining_s": 0,
            "session_count": 0,
        },
        "power": {"valid": False, "battery_pct": 0, "charging": False},
    }


def post_json(url: str, payload: dict[str, Any], timeout: float = 5.0) -> tuple[int, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read()
        if not response_body:
            decoded: Any = None
        else:
            try:
                decoded = json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = response_body.decode("utf-8", errors="replace")
        return response.status, decoded


def backend_contract_payload(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Return C's strict API shape without mutating the full local record."""

    payload = dict(telemetry)
    light = telemetry["light"]
    payload["light"] = {"lux": light["lux"], "label": light["label"]}
    return payload


def append_jsonl(path: str, payload: dict[str, Any]) -> None:
    """Append one UTF-8 JSON record that C can replay line by line."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def telemetry_endpoint(backend_url: str) -> str:
    cleaned = backend_url.rstrip("/")
    if cleaned.endswith(DEFAULT_TELEMETRY_PATH):
        return cleaned
    return cleaned + DEFAULT_TELEMETRY_PATH


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bridge C3 AS7341 TCP samples to FocusCube telemetry"
    )
    parser.add_argument("c3_host", help="C3 IP printed by the serial monitor")
    parser.add_argument("--c3-port", type=int, default=DEFAULT_C3_PORT)
    parser.add_argument(
        "--backend-url",
        help="C backend base URL or full /api/v1/telemetry URL; omit for dry-run",
    )
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--lux-scale",
        type=float,
        default=DEFAULT_MOCK_LUX_SCALE,
        help="lux scale; default 0.15 is the uncalibrated integration mock value",
    )
    parser.add_argument(
        "--calibrated",
        action="store_true",
        help="mark light.lux as calibrated; only use after reference-meter calibration",
    )
    parser.add_argument("--dim-threshold", type=float, default=200.0)
    parser.add_argument("--bright-threshold", type=float, default=500.0)
    parser.add_argument(
        "--gain-code", type=int, choices=range(0, 11), default=7,
        help="ask the C3 to use AS7341 gain code 7 (64x) by default",
    )
    parser.add_argument(
        "--interval-ms", type=int, default=1000,
        help="ask the C3 to sample at this interval (minimum 100 ms)",
    )
    parser.add_argument(
        "--strict-contract", action="store_true",
        help="also remove AS7341 provenance fields from console and JSONL output",
    )
    parser.add_argument("--limit", type=int, default=0, help="stop after N samples; 0 runs continuously")
    parser.add_argument("--output-jsonl", help="append converted telemetry to this JSONL file")
    parser.add_argument("--http-timeout", type=float, default=5.0)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    if args.interval_ms < 100:
        raise ValueError("interval must be at least 100 ms")
    if args.calibrated and args.lux_scale == DEFAULT_MOCK_LUX_SCALE:
        print(
            "[warning] --calibrated uses the default mock scale 0.15; verify calibration",
            file=sys.stderr,
        )
    endpoint = telemetry_endpoint(args.backend_url) if args.backend_url else None
    print(f"[connect] C3 {args.c3_host}:{args.c3_port}")
    print(f"[target]  {endpoint}" if endpoint else "[dry-run] backend URL omitted")
    if args.output_jsonl:
        print(f"[record]  {args.output_jsonl}")

    delivered = 0
    with socket.create_connection((args.c3_host, args.c3_port), timeout=5.0) as sock:
        sock.settimeout(None)
        sock.sendall(f"g{args.gain_code}\ni{args.interval_ms}\n".encode("ascii"))
        with sock.makefile("r", encoding="utf-8", newline="\n") as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[skip non-json] {line}", file=sys.stderr)
                    continue
                if "seq" not in message:
                    print(f"[C3] {message}")
                    continue
                try:
                    telemetry = build_telemetry(
                        message,
                        timestamp=int(time.time()),
                        device_id=args.device_id,
                        source=args.source,
                        lux_scale=args.lux_scale,
                        dim_threshold=args.dim_threshold,
                        bright_threshold=args.bright_threshold,
                        calibrated=args.calibrated,
                        strict_contract=args.strict_contract,
                    )
                except ValueError as exc:
                    print(f"[invalid sample] {exc}: {message}", file=sys.stderr)
                    continue
                print(json.dumps(telemetry, ensure_ascii=False, separators=(",", ":")))
                if args.output_jsonl:
                    append_jsonl(args.output_jsonl, telemetry)
                if endpoint:
                    try:
                        status, response = post_json(
                            endpoint, backend_contract_payload(telemetry), args.http_timeout
                        )
                        print(f"[POST] HTTP {status} {response}")
                    except urllib.error.HTTPError as exc:
                        error_body = exc.read().decode("utf-8", errors="replace")
                        print(f"[POST failed] HTTP {exc.code}: {error_body}", file=sys.stderr)
                    except (urllib.error.URLError, TimeoutError) as exc:
                        reason = getattr(exc, "reason", str(exc))
                        print(f"[POST failed] {reason}", file=sys.stderr)
                delivered += 1
                if args.limit and delivered >= args.limit:
                    return 0
    return 0


def main() -> int:
    try:
        return run(parse_args())
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
