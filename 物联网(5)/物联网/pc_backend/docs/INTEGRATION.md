# 联调说明

## 联调前提

- EYE 与 C3 是两个物理节点，共用 `installation_id=focuscube-base-01`。
- C3 唯一上传 AS7341 原始光照；EYE 上传 IMU、专注会话和派生环境结果。
- P4 与 Web 默认读取逻辑基座 `focuscube-base-01`。

## 与成员 A

成员 A 按 `tests/test_multinode.py` 中的 C3/EYE v2 样例调用：

```text
POST http://<成员C局域网IP>:8000/api/v1/telemetry
Content-Type: application/json
```

验收：两节点请求均为 HTTP 2xx，融合状态能看到正确来源，重复 `message_id` 幂等，冲突消息被拒绝。

## 与成员 B

P4 读取：

```text
GET /api/v1/status?installation_id=focuscube-base-01
GET /api/v1/report/daily?device_id=focuscube-base-01&date=YYYY-MM-DD
GET /api/v1/reminders?device_id=focuscube-base-01&since=0
```

P4 三个请求统一使用逻辑基座 ID；物理节点 ID 只用于诊断。

## 与成员 D

Web 看板已放入仓库 `frontend/`，云服务器部署后推荐直接打开：

```text
http://82.156.238.244/focuscube/dashboard/
```

该地址与 API 同源。除状态、日报和提醒外，还使用：

```text
GET /api/v1/timeseries?device_id=focuscube-base-01&date=YYYY-MM-DD&metric=light.lux
GET /api/v1/timeseries?device_id=focuscube-base-01&date=YYYY-MM-DD&metric=imu.activity
GET /api/v1/timeseries?device_id=focuscube-base-01&date=YYYY-MM-DD&metric=edge.environment.score
GET /api/v1/timeseries?device_id=focuscube-base-01&date=YYYY-MM-DD&metric=focus.state
```

## AI Gateway 联调（成员 C）

1. 在 `.env` 填入控制台“查看代码”给出的 Base URL、网关访问密钥和模型标识。
2. 先让成员 A 上传一条 telemetry，或使用 `scripts/replay.py` 回放真实样例。
3. 请求 `GET /api/v1/report/daily?device_id=<当前选择的设备ID>&date=YYYY-MM-DD&refresh=true`。
4. 确认返回 `report_text` 和 `suggestions`，并检查后端日志未显示进入规则兜底。
5. 保存脱敏后的成功记录，再由成员 B、D 通过原有接口显示结果。
