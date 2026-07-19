param([string]$BaseUrl = "http://127.0.0.1:8000")

curl.exe -i -X POST "$BaseUrl/api/v1/telemetry" `
  -H "Content-Type: application/json" `
  --data-binary "@examples/telemetry.json"

curl.exe "$BaseUrl/api/v1/status?device_id=focuscube-s3-01"
curl.exe "$BaseUrl/api/v1/reminders?device_id=focuscube-s3-01&since=0"
curl.exe "$BaseUrl/api/v1/report/daily?device_id=focuscube-s3-01&date=2024-06-10"
