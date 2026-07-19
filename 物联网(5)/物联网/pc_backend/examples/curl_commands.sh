#!/usr/bin/env sh
BASE_URL="${1:-http://127.0.0.1:8000}"

curl -i -X POST "$BASE_URL/api/v1/telemetry" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/telemetry.json

echo
curl -s "$BASE_URL/api/v1/status?device_id=focuscube-s3-01"; echo
curl -s "$BASE_URL/api/v1/reminders?device_id=focuscube-s3-01&since=0"; echo
curl -s "$BASE_URL/api/v1/report/daily?device_id=focuscube-s3-01&date=2024-06-10"; echo
