# D 端 API 联调约定

## 地址解析

推荐从后端同源地址打开：

```text
http://192.168.1.165:8000/dashboard/
```

此时前端自动使用当前 origin 作为 API 基础地址。若前端单独运行在 `127.0.0.1:5173`，默认访问 `http://192.168.1.165:8000`，也可以通过 `?api=http://其他IP:8000` 临时覆盖。

## 1. 状态接口

```http
GET /api/v1/status
```

当前后端同时返回既有顶层结构和 D 端兼容结构：

```json
{
  "ok": true,
  "devices": [
    {
      "device_id": "focuscube-s3-01",
      "source": "s3",
      "online": true,
      "light": {"lux": 360.72, "label": "suitable"},
      "imu": {"valid": false, "face": 0, "activity": 0},
      "focus": {"valid": false, "state": "idle"},
      "power": {"valid": false, "battery_pct": 0},
      "telemetry": {
        "valid": true,
        "light": {"lux": 360.72, "label": "suitable"},
        "imu": {"valid": false, "face": 0, "activity": 0},
        "focus": {"valid": false, "state": "idle"},
        "power": {"valid": false, "battery_pct": 0}
      }
    }
  ]
}
```

顶层字段继续供 P4 和已有调用方使用；`telemetry` 供 D 看板直接读取。子系统 `valid:false` 时，前端不展示其中的占位 0 值。

## 2. 日报接口

```http
GET /api/v1/report/daily?device_id=focuscube-s3-01&date=2026-07-19
```

D 端直接展示 `report_text`、`metrics` 和 `suggestions`，不在浏览器中重新计算日报。

## 3. 提醒接口

```http
GET /api/v1/reminders?device_id=focuscube-s3-01&since=0
```

D 端只展示后端实际返回的提醒对象。

## 4. 时序接口

```http
GET /api/v1/timeseries?device_id=focuscube-s3-01&date=2026-07-19&metric=light.lux
```

前端使用：

```text
light.lux
imu.activity
power.battery_pct
focus.state
```

前三项返回 `points`；`focus.state` 返回 `segments`。无有效数据时返回空数组，前端保持等待态。

## 5. CORS

同源 `/dashboard/` 不需要跨域配置。单独运行前端时，后端默认 `FOCUSCUBE_CORS_ORIGINS=*`，也可改为只允许实际前端 origin。
