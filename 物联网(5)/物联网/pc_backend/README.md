# FocusCube 成员 C 后端

本目录严格对应小组分工中的“成员 C：大模型 + 后端”，不增加新的项目主线。云端大模型统一使用火山引擎边缘大模型网关（AI Gateway）。

## 当前设备口径

- `focuscube-eye-01` 是边缘控制节点，负责 IMU、专注会话、健康状态和派生环境结论。
- `focuscube-c3-01` 是 AS7341 光照节点，也是原始光照数据的唯一云端上传者。
- `focuscube-base-01` 是后端融合后的逻辑基座视图，不是第三块物理设备。
- P4 与 Web 默认读取 `focuscube-base-01`；物理节点状态仍可分别用于诊断。
- 旧版单节点 telemetry 继续兼容，但新的双节点实机应使用 `schema_version: 2`。

## 已实现接口

```text
POST    /api/v1/telemetry
GET     /api/v1/status?installation_id=focuscube-base-01
GET     /api/v1/status?device_id=focuscube-eye-01
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

旧版兼容样例位于 `examples/telemetry.json`；多节点 v2 的 C3/EYE 完整样例及身份、幂等、融合测试位于 `tests/test_multinode.py`。
