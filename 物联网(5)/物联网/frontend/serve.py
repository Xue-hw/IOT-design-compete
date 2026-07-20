#!/usr/bin/env python3
"""Zero-dependency static server for the FocusCube dashboard."""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
from pathlib import Path


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the FocusCube Web dashboard.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host, default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=5173, help="Bind port, default: 5173")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    os.chdir(root)
    handler = http.server.SimpleHTTPRequestHandler
    with ReusableTCPServer((args.host, args.port), handler) as server:
        print(f"FocusCube dashboard: http://127.0.0.1:{args.port}")
        print("Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
