import json
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from unittest.mock import patch

from telemetry_bridge import (
    adc_full_scale,
    append_jsonl,
    build_telemetry,
    classify_light,
    compute_basic_counts_proxy,
    compute_lux_proxy,
    gain_multiplier,
    integration_time_ms,
    parse_args,
    post_json,
    run,
    telemetry_endpoint,
)


SPECTRUM_SAMPLE = {
    "seq": 7,
    "F1": 100,
    "F2": 200,
    "F3": 300,
    "F4": 400,
    "F5": 500,
    "F6": 600,
    "F7": 700,
    "F8": 800,
    "Clear": 900,
    "NIR": 1000,
    "gain_code": 7,
    "atime": 29,
    "astep": 599,
    "saturated": False,
}


class TelemetryConversionTests(unittest.TestCase):
    def test_gain_and_timing_metadata_match_as7341_configuration(self):
        self.assertEqual(gain_multiplier(7), 64.0)
        self.assertEqual(gain_multiplier(9), 256.0)
        self.assertAlmostEqual(integration_time_ms(29, 599), 50.04)
        self.assertEqual(adc_full_scale(29, 599), 18000)

    def test_compute_lux_proxy_normalizes_to_reference_gain_and_time(self):
        self.assertAlmostEqual(
            compute_basic_counts_proxy(SPECTRUM_SAMPLE), 0.156125, places=6
        )
        self.assertEqual(compute_lux_proxy(SPECTRUM_SAMPLE), 2000.0)
        high_gain = dict(SPECTRUM_SAMPLE, gain_code=9)
        for field in (
            "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "Clear", "NIR"
        ):
            high_gain[field] *= 4
        self.assertEqual(compute_lux_proxy(high_gain), 2000.0)

    def test_compute_lux_proxy_rejects_incomplete_spectrum(self):
        incomplete = dict(SPECTRUM_SAMPLE)
        del incomplete["F5"]
        with self.assertRaisesRegex(ValueError, "F5"):
            compute_lux_proxy(incomplete)

    def test_compute_lux_proxy_rejects_negative_and_boolean_values(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            compute_lux_proxy(dict(SPECTRUM_SAMPLE, F4=-1))
        with self.assertRaisesRegex(ValueError, "numeric"):
            compute_lux_proxy(dict(SPECTRUM_SAMPLE, F4=True))

    def test_compute_lux_proxy_rejects_invalid_gain_metadata(self):
        with self.assertRaisesRegex(ValueError, "gain_code"):
            compute_lux_proxy(dict(SPECTRUM_SAMPLE, gain_code=11))

    def test_classify_light_honors_threshold_boundaries(self):
        self.assertEqual(classify_light(199.99, 200.0, 500.0), "too_dim")
        self.assertEqual(classify_light(200.0, 200.0, 500.0), "suitable")
        self.assertEqual(classify_light(500.0, 200.0, 500.0), "suitable")
        self.assertEqual(classify_light(500.01, 200.0, 500.0), "too_bright")

    def test_classify_light_rejects_reversed_thresholds(self):
        with self.assertRaisesRegex(ValueError, "dim threshold"):
            classify_light(300.0, 500.0, 200.0)

    def test_build_telemetry_matches_proxy_contract(self):
        telemetry = build_telemetry(
            SPECTRUM_SAMPLE,
            timestamp=1_718_000_000,
            device_id="focuscube-c3-proxy-01",
            source="c3-as7341-proxy",
            lux_scale=0.15,
            dim_threshold=200.0,
            bright_threshold=500.0,
        )
        self.assertEqual(telemetry["device_id"], "focuscube-c3-proxy-01")
        self.assertEqual(telemetry["source"], "c3-as7341-proxy")
        self.assertEqual(telemetry["light"]["lux"], 300.0)
        self.assertEqual(telemetry["light"]["label"], "suitable")
        self.assertEqual(telemetry["light"]["proxy"], 2000.0)
        self.assertEqual(telemetry["light"]["saturated"], False)
        self.assertEqual(
            telemetry["imu"],
            {"valid": False, "face": 0, "mode": "unknown", "activity": 0.0},
        )
        self.assertEqual(
            telemetry["focus"],
            {"valid": False, "state": "idle", "remaining_s": 0, "session_count": 0},
        )
        self.assertEqual(
            telemetry["power"],
            {"valid": False, "battery_pct": 0, "charging": False},
        )

    def test_build_telemetry_can_emit_strict_light_contract(self):
        telemetry = build_telemetry(
            SPECTRUM_SAMPLE,
            timestamp=1,
            device_id="focuscube-c3-proxy-01",
            source="c3-as7341-proxy",
            lux_scale=0.15,
            dim_threshold=200.0,
            bright_threshold=500.0,
            strict_contract=True,
        )
        self.assertEqual(telemetry["light"], {"lux": 300.0, "label": "suitable"})

    def test_build_telemetry_propagates_sensor_saturation(self):
        telemetry = build_telemetry(
            dict(SPECTRUM_SAMPLE, saturated=True),
            timestamp=1,
            device_id="focuscube-c3-proxy-01",
            source="c3-as7341-proxy",
            lux_scale=0.15,
            dim_threshold=200.0,
            bright_threshold=500.0,
        )
        self.assertTrue(telemetry["light"]["saturated"])

    def test_build_telemetry_rejects_non_positive_scale(self):
        with self.assertRaisesRegex(ValueError, "lux scale"):
            build_telemetry(
                SPECTRUM_SAMPLE,
                timestamp=1,
                device_id="focuscube-c3-proxy-01",
                source="c3-as7341-proxy",
                lux_scale=0,
                dim_threshold=200.0,
                bright_threshold=500.0,
            )

    def test_telemetry_endpoint_accepts_base_or_full_url(self):
        expected = "http://192.168.1.20:8000/api/v1/telemetry"
        self.assertEqual(telemetry_endpoint("http://192.168.1.20:8000/"), expected)
        self.assertEqual(telemetry_endpoint(expected), expected)

    def test_cli_defaults_identify_the_c3_proxy(self):
        args = parse_args(["192.168.1.10"])
        self.assertEqual(args.device_id, "focuscube-c3-proxy-01")
        self.assertEqual(args.source, "c3-as7341-proxy")
        self.assertEqual(args.gain_code, 7)
        self.assertEqual(args.lux_scale, 0.15)
        self.assertFalse(args.calibrated)


class _CaptureHandler(BaseHTTPRequestHandler):
    body = None

    def do_POST(self):
        size = int(self.headers["Content-Length"])
        type(self).body = json.loads(self.rfile.read(size))
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, _format, *_args):
        pass


class HttpPostTests(unittest.TestCase):
    def test_post_json_sends_payload_and_returns_status_and_body(self):
        server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            status, response = post_json(
                f"http://127.0.0.1:{server.server_port}/api/v1/telemetry",
                {"device_id": "focuscube-c3-proxy-01"},
            )
        finally:
            thread.join(timeout=2)
            server.server_close()
        self.assertEqual(status, 201)
        self.assertEqual(response, {"ok": True})
        self.assertEqual(_CaptureHandler.body, {"device_id": "focuscube-c3-proxy-01"})

    def test_append_jsonl_writes_replayable_utf8_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/telemetry.jsonl"
            append_jsonl(path, {"text": "光照适宜", "seq": 1})
            append_jsonl(path, {"text": "光线偏暗", "seq": 2})
            with open(path, "r", encoding="utf-8") as handle:
                records = [json.loads(line) for line in handle]
        self.assertEqual(
            records,
            [{"text": "光照适宜", "seq": 1}, {"text": "光线偏暗", "seq": 2}],
        )


class BridgeEndToEndTests(unittest.TestCase):
    def test_run_receives_c3_sample_and_posts_focuscube_telemetry(self):
        c3_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c3_server.bind(("127.0.0.1", 0))
        c3_server.listen(1)
        c3_port = c3_server.getsockname()[1]
        received_command = []

        def serve_c3():
            connection, _address = c3_server.accept()
            with connection:
                received_command.append(connection.recv(64).decode("ascii"))
                connection.sendall(b'{"hello":"C3-AS7341","port":3333}\n')
                connection.sendall((json.dumps(SPECTRUM_SAMPLE) + "\n").encode("utf-8"))
            c3_server.close()

        c3_thread = threading.Thread(target=serve_c3)
        c3_thread.start()
        backend = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        backend_thread = threading.Thread(target=backend.handle_request)
        backend_thread.start()
        args = parse_args(
            [
                "127.0.0.1", "--c3-port", str(c3_port),
                "--backend-url", f"http://127.0.0.1:{backend.server_port}",
                "--interval-ms", "2500", "--limit", "1",
            ]
        )
        output = StringIO()
        try:
            with redirect_stdout(output):
                result = run(args)
        finally:
            c3_thread.join(timeout=2)
            backend_thread.join(timeout=2)
            backend.server_close()

        self.assertEqual(result, 0)
        self.assertEqual(received_command, ["g7\ni2500\n"])
        self.assertEqual(_CaptureHandler.body["device_id"], "focuscube-c3-proxy-01")
        self.assertEqual(_CaptureHandler.body["light"], {"lux": 300.0, "label": "suitable"})
        self.assertFalse(_CaptureHandler.body["imu"]["valid"])
        self.assertFalse(_CaptureHandler.body["focus"]["valid"])
        self.assertFalse(_CaptureHandler.body["power"]["valid"])
        self.assertIn("[POST] HTTP 201", output.getvalue())

    def test_run_keeps_collecting_when_backend_times_out(self):
        c3_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c3_server.bind(("127.0.0.1", 0))
        c3_server.listen(1)
        c3_port = c3_server.getsockname()[1]

        def serve_c3():
            connection, _address = c3_server.accept()
            with connection:
                connection.recv(64)
                connection.sendall((json.dumps(SPECTRUM_SAMPLE) + "\n").encode("utf-8"))
            c3_server.close()

        c3_thread = threading.Thread(target=serve_c3)
        c3_thread.start()
        args = parse_args(
            [
                "127.0.0.1", "--c3-port", str(c3_port),
                "--backend-url", "http://127.0.0.1:8000", "--limit", "1",
            ]
        )
        stdout, stderr = StringIO(), StringIO()
        try:
            with (
                patch("telemetry_bridge.post_json", side_effect=TimeoutError("timed out")),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = run(args)
        finally:
            c3_thread.join(timeout=2)
        self.assertEqual(result, 0)
        self.assertIn("[POST failed] timed out", stderr.getvalue())
        self.assertIn('"device_id":"focuscube-c3-proxy-01"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
