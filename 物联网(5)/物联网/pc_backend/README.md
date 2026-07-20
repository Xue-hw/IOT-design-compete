# FocusCube 成员 C 后端

本目录严格对应小组分工中的“成员 C：大模型 + 后端”，不增加新的项目主线。云端大模型统一使用火山引擎边缘大模型网关（AI Gateway）。

## 当前设备口径

- 产品主设备只有 `ESP32-S3-EYE` 和 `ESP32-S3 Cube`。EYE 用于功能测试，Cube 用于最终演示且是 EYE 的功能子集；当前不要求二者同时在线。
- AS7341 虽连接在 ESP32-C3 上，但 C3 只是 EYE/Cube 共用的光照采集与传输子系统，不作为第三个产品节点展示。
- `focuscube-c3-proxy-01` 与 `c3-as7341-proxy` 暂时保留用于追踪光照物理来源。状态响应会把前者标记为 `product_node: false`、`device_role: sensor_proxy`。
- EYE 屏幕当前未启用，Cube 没有屏幕。现阶段的正式展示端是 P4 七寸屏。
- EYE 测试结束前不提前建立正式绑定；通过 `FOCUSCUBE_ACTIVE_DEVICE_ID` 和 P4 的 `CONFIG_FOCUSCUBE_DEVICE_ID` 选择同一个当前数据源即可。

## 已实现接口

```text
POST    /api/v1/telemetry
GET     /api/v1/status
GET     /api/v1/report/daily?device_id=&date=
GET     /api/v1/reminders?device_id=&since=
GET     /api/v1/timeseries?device_id=&date=&metric=
GET/PUT /api/v1/config?device_id=
```

AI Gateway 不替换以上接口。S3、P4、Web 仍只访问成员 C 后端；只有后端在生成日报时调用 AI Gateway。

## 启动

```bash
python -m pip install -r requirements.txt
cp .env.example .env   # Windows 可手工复制
python run.py
```

默认监听 `0.0.0.0:8000`。其他成员应使用成员 C 电脑的局域网 IPv4，例如：

```text
http://192.168.31.100:8000
```

不能让成员 B、D 使用 `localhost` 访问成员 C 的电脑。

## D 端看板

本仓库已包含 `../frontend`。后端启动后可直接打开：

```text
http://localhost:8000/dashboard/
```

当前云服务器入口为：

```text
http://82.156.238.244/focuscube/dashboard/
```

页面与 API 同源，不需要写死成员电脑的旧 IP，也不会影响 P4 使用的原有状态字段。

## AI Gateway 参数

在 `.env` 中填写控制台“查看代码”给出的实际值：

```text
FOCUSCUBE_LLM_PROVIDER=volcengine_ai_gateway
FOCUSCUBE_LLM_BASE_URL=TODO_控制台实际地址
FOCUSCUBE_LLM_API_KEY=TODO_网关访问密钥
FOCUSCUBE_LLM_MODEL=TODO_实际模型标识
```

不要把真实密钥放入 ZIP、Git 仓库、方案文档或演示截图。未填写或云端接口失败时，系统会使用规则复盘保证演示不中断；规则兜底不能替代赛事要求的真实 AI Gateway 调用。

## 接口验收

```bash
python scripts/verify_acceptance.py
pytest -q
```

成员 A 的原始 JSON 可直接发送，示例位于 `examples/telemetry.json`。该文件仍是兼容性样例，不代表 EYE/Cube 的正式 ID 已确定。
