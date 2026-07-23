# FocusCube D 端真实接口动态 Web 看板

本目录是成员 D 负责的 Web 看板与总集成展示层，已与成员 C 的 `pc_backend` 合并到同一 GitHub 项目中。浏览器端不保存大模型 API Key，不直接调用云端模型，也不修改 S3/P4 固件。

## 在线入口

当前生产环境已经由后端同源托管：

```text
http://82.156.238.244/focuscube/dashboard/
```

## 本地与后端同源运行

先启动后端：

```bash
cd pc_backend
python -m pip install -r requirements.txt
python run.py
```

然后打开：

```text
http://127.0.0.1:8000/dashboard/
```

通过 `/dashboard/` 打开时，前端自动使用当前后端同源地址，请求：

```text
GET /api/v1/status
GET /api/v1/report/daily?device_id=&date=
GET /api/v1/reminders?device_id=&since=
GET /api/v1/timeseries?device_id=&date=&metric=
```

同源运行不需要额外处理跨域，也不依赖成员电脑的固定局域网 IP。

## 单独运行前端

Windows 双击 `run.bat`，或执行：

```bash
python serve.py --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173
```

单独运行时默认连接 `http://82.156.238.244/focuscube`。临时联调其他后端可使用 URL 参数：

```text
http://127.0.0.1:5173/?api=http://其他IP:8000
```

状态请求实际带有 `installation_id=focuscube-base-01`，看板同时展示逻辑基座和 EYE/C3 物理节点诊断信息。

## 状态兼容规则

- 优先读取多节点响应中的 `telemetry`、`availability` 和 `members`。
- 同时兼容逻辑设备 `devices[0].telemetry`，供 P4/旧调用方共用。
- `valid:false`、`partial`、`missing`、`invalid` 不会被当成真实测量值展示。
- `stale` 数据保留来源提示，但不会伪装成新鲜数据。
- 后续恢复为有效数据时自动恢复实际字段展示。
- 状态接口每 2 秒刷新；日报、提醒和时序每 6 秒刷新。
- 接口失败时显示连接告警，不生成本地伪造遥测。

## 自动化测试

```bash
python tests/smoke_test.py
```

测试覆盖融合状态、EYE/C3 成员诊断、C3 光照、EYE 活动度、派生环境趋势和融合日报。
