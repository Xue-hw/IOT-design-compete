# ESP32-S3 采集端

当前没有可用的 FocusCube S3 固件。本目录暂存工程入口，收到固件后至少应补齐：

现阶段由 `c3-as7341-proxy` 提供真实 AS7341 光照数据，设备 ID 固定为 `focuscube-c3-proxy-01`。代理中的 IMU、专注和电量字段必须显式使用 `valid: false`，不得作为真实 S3 数据展示。未来真实 S3 使用独立的 `focuscube-s3-01`。

- 完整 ESP-IDF 或 Arduino 工程；
- 开发框架和版本；
- 硬件引脚与板卡版本；
- Wi-Fi 与后端地址配置方式；
- 构建、烧录和串口监视命令；
- telemetry 字段映射；
- 真实数据样例和稳定性测试记录。

禁止上传真实 Wi-Fi 密码和 API Key。
