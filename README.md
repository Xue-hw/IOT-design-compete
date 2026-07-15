# FocusCube - 2026 全国大学生物联网设计竞赛

FocusCube 是面向学习与办公场景的智能光环境和专注管理系统，当前参赛主线为：

```text
ESP32-S3 立方体采集端
        ↓ telemetry
后端服务 + 云端大模型
        ↓ report / reminders / status
Web 看板 + ESP32-P4 七寸屏
        ↓
设备端提醒展示与响应
```

## 当前状态

- 后端已验证 `telemetry`、`status`、`report/daily`、`reminders` 和 `timeseries` 接口。
- AS7341 真实光照已完成偏暗、适宜、过亮三档闭环测试；IMU、专注和电量仍为无效占位数据。
- C3 光照代理统一使用 `focuscube-c3-proxy-01`；未来真实 S3 保留 `focuscube-s3-01`，禁止混用。
- ESP32-P4 七寸屏已显示中文界面并成功读取过真实 `status` JSON。
- P4 工程暂未同步到本仓库，成员 B 上传后放入 `firmware/esp32-p4/`。
- S3 比赛固件仍待提供，上传后放入 `firmware/esp32-s3/`。
- Arduino 智能家居原型已归档到 `firmware/arduino/`，不作为 FocusCube 当前主固件。

详细联调记录见 [`docs/status/2026-07-14-integration-status.md`](docs/status/2026-07-14-integration-status.md)。

## 仓库导航

| 路径 | 内容 | 状态 |
|---|---|---|
| [`docs/`](docs/) | 文档索引、联调状态、图片素材 | 持续更新 |
| [`firmware/esp32-c3/`](firmware/esp32-c3/) | AS7341 光照采集固件与电脑桥接程序 | 已上传、测试通过 |
| [`firmware/esp32-s3/`](firmware/esp32-s3/) | FocusCube S3 采集端固件 | 待上传 |
| [`firmware/esp32-p4/`](firmware/esp32-p4/) | P4 七寸屏展示端固件 | 待成员 B 上传 |
| [`firmware/arduino/`](firmware/arduino/) | Arduino 历史原型 | 已归档 |
| [`backend/`](backend/) | 后端与大模型服务 | 待成员 C 上传 |
| [`frontend/`](frontend/) | Web 看板 | 待成员 D 上传 |
| [`物联网(5)/物联网/`](<物联网(5)/物联网/>) | 早期完整资料包 | 已保留 |

## 核心接口

联调环境基础地址曾使用 `http://10.129.90.92:8000`。这是动态局域网地址，不应写死在最终固件中。

```text
POST /api/v1/telemetry
GET  /api/v1/status
GET  /api/v1/report/daily?device_id=&date=
GET  /api/v1/reminders?device_id=&since=
GET  /api/v1/timeseries?device_id=&date=&metric=
GET/PUT /api/v1/config?device_id=
```

接口字段以 [`物联网(5)/物联网/04_技术实现/局域网接口说明.md`](<物联网(5)/物联网/04_技术实现/局域网接口说明.md>) 为准。

## 提交截止日期

- 完整设计方案和实物演示视频：2026-07-27
- 分赛区网评结果：2026-08-10 前
- 分赛区决赛：2026-08-20 前

## 协作约定

- 不提交 Wi-Fi 密码、API Key、访问令牌、个人隐私或本机专用配置。
- 每个工程必须带独立启动说明、依赖版本和烧录/运行命令。
- `build/`、生成的 `sdkconfig`、IDE 临时文件和本地密钥不进入仓库。
- 提交前先验证工程可构建，并保留硬件照片、串口日志或接口响应作为证据。
