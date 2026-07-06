# FocusCube 任务清单与分工

## 当前方案

FocusCube 当前按“S3 立方体采集端 + P4 七寸屏展示端 + 后端大模型 + Web 看板”推进。

```text
S3 立方体采集结构化感知数据
        ↓
后端接收、存储、聚合
        ↓
云端大模型生成复盘和提醒
        ↓
Web 看板 + P4 七寸屏展示
        ↓
S3/P4 提醒响应
```

## 分工

| 成员 | 方向 | 当前任务 |
|---|---|---|
| A | S3 端 | 保持已烧录 S3 稳定，确认字段，提供真实数据或样例 |
| B | P4 端 | 负责 P4 七寸屏展示，优先展示后端状态、大模型复盘和提醒 |
| C | 大模型 + 后端 | 提供 API、聚合数据、调用大模型、生成提醒 |
| D | 前端 + 材料 | Web 看板、方案书、PPT、视频脚本、提交包 |

## M1：最小闭环

目标：S3 数据进入后端，大模型生成复盘，Web 看板可展示。

- A：提供 S3 telemetry 字段和样例数据。
- C：实现 `POST /api/v1/telemetry`、`GET /api/v1/status`、`GET /api/v1/report/daily`。
- D：Web 看板显示设备状态、指标、复盘。
- B：P4 环境和 7 寸屏基础 Demo 跑通。

验收：

- 后端至少收到一条 S3 真实或准真实 telemetry。
- 大模型至少生成一次中文复盘。
- Web 页面能展示复盘结果。

## M2：P4 七寸屏接入

目标：P4 成为最终视频中可见的展示节点。

- B：P4 通过 HTTP 拉取后端 status/report/reminders。
- B：7 寸屏展示设备状态、大模型复盘或提醒列表。
- C：保证 P4 使用的接口稳定。
- D：把 P4 屏幕展示写入系统架构图和视频脚本。
- A：保持 S3 稳定，不参与 P4 大改。

验收：

- P4 屏幕在视频中清楚可见。
- P4 至少稳定显示一条后端数据或大模型结果。
- P4 不是孤立 Demo，而是接入后端闭环。

## M3：材料和提交

目标：完整方案、视频、PPT、代码说明和测试记录准备好。

- A：S3 演示步骤、字段说明、测试记录。
- B：P4 工程说明、运行照片、屏幕展示视频片段。
- C：API 文档、prompt、示例输入输出、部署说明。
- D：方案书、PPT、演示视频、最终提交包。

## 接口契约

### telemetry 上行

```json
{
  "device_id": "focuscube-s3-01",
  "source": "s3",
  "ts": 1718000000,
  "light": {
    "lux": 235.6,
    "label": "suitable"
  },
  "imu": {
    "face": 2,
    "mode": "focus",
    "activity": 0.32
  },
  "focus": {
    "state": "running",
    "remaining_s": 940,
    "session_count": 3
  },
  "power": {
    "battery_pct": 78,
    "charging": false
  }
}
```

### 后端 API

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/telemetry` | 接收 S3/P4 telemetry |
| GET | `/api/v1/status` | 获取系统和设备最新状态 |
| GET | `/api/v1/report/daily?device_id=&date=` | 获取大模型日报复盘 |
| GET | `/api/v1/reminders?device_id=&since=` | 获取提醒列表 |
| GET | `/api/v1/timeseries?device_id=&date=&metric=` | 获取曲线数据 |
| GET/PUT | `/api/v1/config?device_id=` | 获取或修改配置 |

### 下行提醒

```json
{
  "id": "r-001",
  "type": "too_dim",
  "text": "当前光线偏暗，建议打开台灯。",
  "priority": 2,
  "ttl_s": 120
}
```

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| S3 上传不稳定 | 使用 S3 真实导出数据 replay，视频仍展示 S3 实物 |
| P4 UI 来不及 | 改成大字体三屏轮播：状态、复盘、提醒 |
| 大模型 API 不稳定 | 返回最近一次成功复盘或规则生成复盘 |
| Web 来不及 | 保留一个总览页，不做复杂配置页 |
