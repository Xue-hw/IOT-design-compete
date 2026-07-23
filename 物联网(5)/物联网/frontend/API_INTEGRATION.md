# D 端 API 联调约定

## 地址解析

推荐从后端同源地址打开：

```text
http://82.156.238.244/focuscube/dashboard/
```

此时前端会从当前页面路径识别 `/focuscube` 前缀，并将其作为 API 基础路径。若前端单独运行在 `127.0.0.1:5173`，默认访问 `http://82.156.238.244/focuscube`，也可以通过 `?api=http://其他IP:8000` 临时覆盖。

## 1. 状态接口

```http
GET /api/v1/status?installation_id=focuscube-base-01
```

当前后端返回逻辑基座、物理成员、数据来源和可用性：

```json
{
  "ok": true,
  "installation_id": "focuscube-base-01",
  "view_id": "focuscube-base-01",
  "ready": true,
  "availability": {
    "light": {"state": "fresh", "quality": "measured"},
    "imu": {"state": "fresh", "quality": "measured"}
  },
  "telemetry": {
    "light": {
      "valid": true,
      "source_device_id": "focuscube-c3-01",
      "lux": 360.72,
      "label": "suitable"
    },
    "imu": {
      "valid": true,
      "source_device_id": "focuscube-eye-01",
      "face": "+X",
      "activity": 0.18
    }
  },
  "members": [
    {
      "device_id": "focuscube-eye-01",
      "role": "edge_controller",
      "online": true,
      "health": {"c3_connected": true}
    }
  ]
}
```

`devices[0]` 仍提供兼容融合结构，供 P4 和已有调用方使用。子系统 `valid:false` 或质量为 `partial/missing/invalid` 时，前端不展示占位值。

## 2. 日报接口

```http
GET /api/v1/report/daily?device_id=focuscube-base-01&date=2026-07-23
```

D 端直接展示 `report_text`、`metrics` 和 `suggestions`，不在浏览器中重新计算日报。

## 3. 提醒接口

```http
GET /api/v1/reminders?device_id=focuscube-base-01&since=0
```

D 端只展示后端实际返回的提醒对象。

## 4. 时序接口

```http
GET /api/v1/timeseries?device_id=focuscube-base-01&date=2026-07-23&metric=light.lux
```

前端使用：

```text
light.lux
imu.activity
edge.environment.score
focus.state
```

前三项返回 `points`；`focus.state` 返回 `segments`。无有效数据时返回空数组，前端保持等待态。

## 5. CORS

同源 `/dashboard/` 不需要跨域配置。单独运行前端时，后端默认 `FOCUSCUBE_CORS_ORIGINS=*`，也可改为只允许实际前端 origin。
