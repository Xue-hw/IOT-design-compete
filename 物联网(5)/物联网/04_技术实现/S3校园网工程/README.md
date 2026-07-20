# FocusCube S3 北邮校园网工程

这是一个独立的 ESP-IDF 验证工程，用于让 ESP32-S3 连接开放网络
`BUPT-portal`，自动获取门户重定向和 Cookie，然后提交校园网账号密码。

该流程参考 `Lynnette177/BUPT-NETWORK-ESP32` 的思路，但直接复用了本项目
P4 端已验证的 ESP-IDF 实现，不包含 OLED、OTA 和循环修改 MAC 的逻辑。

## 安全说明

- `sdkconfig` 和 `sdkconfig.defaults.local` 已被 Git 忽略，不要提交账号密码。
- 北邮门户当前使用明文 HTTP，只应在可信的北邮校园网环境中使用。
- 如需恢复连接前的完整固件，使用
  [`05_测试记录/S3_EYE/固件备份`](../../05_测试记录/S3_EYE/固件备份/README.md)
  中对应 MAC 地址的 8 MB 镜像。

## 编译和烧录

```bash
source /Users/buptniaosuan/.espressif/v6.0.2/esp-idf/export.sh
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py -p /dev/cu.usbmodem11201 flash monitor
```

在 `menuconfig -> FocusCube S3 Campus Network Configuration` 中配置本机账号密码。
