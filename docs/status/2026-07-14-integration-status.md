# 2026-07-14 联调状态

## 后端接口

联调基础地址：`http://10.129.90.92:8000`

> 该地址是当前局域网动态地址，仅用于现场联调。最终固件应通过集中配置修改，不能散落硬编码。

已实际验证以下接口均返回 HTTP 200 和合法 UTF-8 JSON：

```text
POST /api/v1/telemetry
GET  /api/v1/status
GET  /api/v1/report/daily?device_id=focuscube-s3-01&date=2026-07-14
GET  /api/v1/reminders?device_id=focuscube-s3-01&since=0
GET  /api/v1/timeseries?device_id=focuscube-s3-01&date=2026-07-14&metric=light.lux
```

已验证内容包括设备状态、中文日报、统计指标、建议、低电量提醒、光线偏暗提醒和光照时序数据。

## P4 七寸屏

- 工程原位置：`/Users/buptniaosuan/Desktop/物联网/ESP32-P4-WIFI6-Touch-LCD-7B-main/examples/ESP-IDF/10_lvgl_demo_v9`
- 工程启动说明：原工程内 `P4工程启动说明.md`
- 固定中文和标题乱码已经修复，现场确认无乱码。
- P4 此前已成功访问 `/api/v1/status` 并显示真实 JSON。
- P4 具备请求失败后自动重试能力，后端恢复后无需重启设备。
- P4 工程尚未同步到共享仓库，目标目录为 `firmware/esp32-p4/`。

阶段性屏幕照片：

![P4 七寸屏阶段性联调画面](../assets/p4-display-status-2026-07-14.jpg)

## 当前阻塞

`10.129.90.92:8000` 当前表现为 TCP 端口可连接，但 HTTP 请求持续超时；电脑和 P4 结果一致，因此问题归属后端服务，而非 P4 网络栈。成员 C 正在处理。

## 已知数据问题

- 早期 mock 时序中存在多条相同 `ts`，后续 S3 或 replay 必须使用递增的真实时间戳。
- 日报生成后新增的 `60 lux` 数据没有进入已缓存日报；演示前必须触发重新生成，确认 `min_lux`、`avg_lux` 和 `suitable_light_ratio` 更新。
- 当前没有可用 S3 主固件，S3 工程和真实 telemetry 仍待补齐。

## 下一次验收

- 后端恢复后，电脑和 P4 同时验证 `status/report/reminders`。
- P4 展示真实状态、AI 复盘和提醒，连续刷新且断网不崩溃。
- 成员 B 上传 P4 工程、依赖说明、烧录步骤和完整屏幕照片。
- 成员 C 上传后端工程、模型调用说明、配置模板和失败兜底说明。
