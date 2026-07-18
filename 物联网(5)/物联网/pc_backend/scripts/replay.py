from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay FocusCube telemetry to the backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--file", default="examples/telemetry.json")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    template = json.loads(Path(args.file).read_text(encoding="utf-8"))
    for index in range(args.count):
        payload = copy.deepcopy(template)
        payload["ts"] = int(time.time())
        payload["light"]["lux"] = round(float(template["light"]["lux"]) + index * 5, 1)
        response = httpx.post(
            args.base_url.rstrip("/") + "/api/v1/telemetry",
            json=payload,
            timeout=10,
        )
        print(response.status_code, response.json())
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
