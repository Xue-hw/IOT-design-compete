# 联调说明

## 联调前提

- 当前一次只选择 EYE 或 Cube 中的一个主设备；二者不要求同时在线。
- C3/AS7341 代理链路继续保留，但不能在界面或架构图中被解释成第三个产品节点。
- EYE 测试完成前不下发正式设备绑定。P4 与后端先把当前选择配置为同一个 `device_id`。

## 与成员 A

成员 A 按 `examples/telemetry.json` 调用：

```text
POST http://<成员C局域网IP>:8000/api/v1/telemetry
Content-Type: application/json
```

验收：HTTP 2xx、响应为 JSON、数据已保存、显式传入同一 `device_id` 的 `GET /api/v1/status` 能看到最新状态。

## 与成员 B

P4 读取：

```text
GET /api/v1/status?device_id=<当前选择的设备ID>
GET /api/v1/report/daily?device_id=<当前选择的设备ID>&date=YYYY-MM-DD
GET /api/v1/reminders?device_id=<当前选择的设备ID>&since=0
```

代理联调阶段 `<当前选择的设备ID>` 可继续使用 `focuscube-c3-proxy-01`；EYE/Cube 正式 ID 等统一下发后再改。P4 三个请求共用同一项 `CONFIG_FOCUSCUBE_DEVICE_ID`，不要分别硬编码。

## 与成员 D

Web 看板已放入仓库 `frontend/`，云服务器部署后推荐直接打开：

```text
http://82.156.238.244/focuscube/dashboard/
```

该地址与 API 同源。除状态、日报和提醒外，还使用：

```text
GET /api/v1/timeseries?device_id=<当前选择的设备ID>&date=YYYY-MM-DD&metric=light.lux
GET /api/v1/timeseries?device_id=<当前选择的设备ID>&date=YYYY-MM-DD&metric=imu.activity
GET /api/v1/timeseries?device_id=<当前选择的设备ID>&date=YYYY-MM-DD&metric=power.battery_pct
GET /api/v1/timeseries?device_id=<当前选择的设备ID>&date=YYYY-MM-DD&metric=focus.state
```

## AI Gateway 联调（成员 C）

1. 在 `.env` 填入控制台“查看代码”给出的 Base URL、网关访问密钥和模型标识。
2. 先让成员 A 上传一条 telemetry，或使用 `scripts/replay.py` 回放真实样例。
3. 请求 `GET /api/v1/report/daily?device_id=<当前选择的设备ID>&date=YYYY-MM-DD&refresh=true`。
4. 确认返回 `report_text` 和 `suggestions`，并检查后端日志未显示进入规则兜底。
5. 保存脱敏后的成功记录，再由成员 B、D 通过原有接口显示结果。
