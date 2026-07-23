# FocusCube API 文档

基础地址：`http://<成员C电脑局域网IP>:8000`

这些路径是 FocusCube 小组内部约定的 HTTP 接口，不是乐鑫规定的固定路径；它们用于实现赛事要求的设备感知数据上行、云端大模型处理和结果展示。

设备身份约定：`focuscube-eye-01` 负责 IMU、专注会话和派生结果，`focuscube-c3-01` 负责 AS7341 原始光照，`focuscube-base-01` 是后端融合逻辑视图。新固件使用 `schema_version: 2`；不含该字段的旧请求继续走兼容校验。

## 1. POST `/api/v1/telemetry`

请求头：`Content-Type: application/json`

请求体必须保持小组约定结构：

```json
{
  "device_id": "focuscube-s3-01",
  "source": "s3",
  "ts": 1718000000,
  "light": {"lux": 235.6, "label": "suitable"},
  "imu": {"face": 2, "mode": "focus", "activity": 0.32},
  "focus": {"state": "running", "remaining_s": 940, "session_count": 3},
  "power": {"battery_pct": 78, "charging": false}
}
```

字段说明：

- `ts`：Unix 秒级时间戳。示例值用于联调；真实 S3 上传时应使用设备当前时间。
- `light.lux`：非负照度值；`label` 沿用 S3 固件当前输出。
- `imu.face`、`imu.mode`、`imu.activity`：沿用成员 A 固件定义，成员 A 需向 B/C/D 提供映射说明。
- `focus.remaining_s`：当前专注周期剩余秒数。
- `focus.session_count`：当天已经完成的专注周期数；后端用它和当前剩余时间估算专注分钟数。
- `power.battery_pct`：0-100；`charging` 为布尔值。

成功：HTTP `201`，返回合法 JSON，并写入 SQLite。

## 2. GET `/api/v1/status`

融合视图使用 `/api/v1/status?installation_id=focuscube-base-01`。物理节点诊断使用 `device_id=focuscube-eye-01` 或 `device_id=focuscube-c3-01`。两个参数不能同时传入；不传参数时默认返回逻辑基座。P4 和 Web 可每 2-5 秒轮询一次。

融合响应包含 `availability`、`telemetry` 和 `members`，并在每个有效数据块中保留 `source_device_id`、`quality`、`ts`、`stale`。`devices[0]` 提供旧版 P4/Web 可读取的兼容融合结构。

## 3. GET `/api/v1/report/daily`

参数：

- `device_id`：必填。
- `date`：可选，`YYYY-MM-DD`，默认当天。
- `refresh`：可选，`true` 时重新生成。

后端把光照、IMU 活动/模式、专注计时和电量聚合成结构化摘要，再由成员 C 后端通过火山引擎边缘大模型网关（AI Gateway）调用云端模型生成中文复盘和建议，满足小组的传感器融合与大模型处理主线。

## 4. GET `/api/v1/reminders`

参数：`device_id` 必填，`since` 为秒级时间戳，默认 0。

返回过暗、过亮、低电量、专注周期结束等实时提醒；AI Gateway 云端模型成功生成的建议也会以 `ai_suggestion` 类型进入同一提醒列表，供 S3、P4、Web 展示或响应。

## 5. GET `/api/v1/timeseries`

参数：`device_id`、`date`、`metric`。`metric` 可取：

```text
lux / light.lux / activity / imu.activity / battery_pct / power.battery_pct
remaining_s / focus.remaining_s / session_count / focus.session_count / focus.state
edge.environment.score
```

融合视图中 `focus.state` 返回 `segments`，每段包含 `start_ts`、`end_ts`、`value` 和来源；其他指标返回带 `source_device_id` 的 `points`。

## 6. GET/PUT `/api/v1/config`

按 `device_id` 获取或修改：适宜照度范围、低电量阈值、在线超时、单次专注分钟数。

## AI Gateway 调用位置

AI Gateway 是后端的外部云服务，不替换本页任何局域网接口。S3/P4/Web 只调用 `/api/v1/...`；成员 C 后端使用网关访问密钥调用 AI Gateway，并把结果转换为既有的 `report_text`、`suggestions` 和 `ai_suggestion` 提醒格式。
