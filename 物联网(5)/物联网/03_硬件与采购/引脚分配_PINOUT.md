# ESP32-S3 引脚配置表（原理图确认版）

> 说明：本表只作为 S3 立方体已烧录硬件的引脚参考。当前阶段不建议为了新功能大改 S3 引脚或重做固件；P4 七寸屏展示端不使用本表。

## 硬件对照

| 外设 | 型号 | 通信接口 |
|---|---|---|
| 麦克风 | ICS43434 (I2S) | I2S BCLK=42, WS=43, DIN=44 |
| IMU | BMI270 (六轴) | I2C (与 SCCB 共用) SDA=13, SCL=12 |
| 摄像头 | OV2640 (DVP 8-bit) | DVP + SCCB |
| SD 卡 | — | SDMMC 1-bit: CLK=39, CMD=40, D0=38（D1/D2/D3 未接 ESP32）|
| LED | WS2816 (数字 RGB) | GPIO45 (RMT) |
| 触摸 | — | GPIO1 (T1), GPIO2 (T2) |
| USB | Type-C | DN=GPIO19, DP=GPIO20 |
| 电池 | — | ADC1_CH2 → GPIO14 |
| 电源开关 | — | GPIO47 (BOOT 按键) |
| PWR_HOLD | — | GPIO48 (推挽输出，保持上电) |
| 传感器电源开关 | — | GPIO41 (高=通电，低=断电) |

---

## GPIO 分配（已确认）

| GPIO | 功能 | 备注 |
|---|---|---|
| 0 | 悬空/可用 | |
| 1 | 触摸 T1 | |
| 2 | 触摸 T2 | |
| 3 | 悬空/可用 | |
| 4 | CAM D2 (Y4) | OV2640 |
| 5 | CAM D1 (Y3) | OV2640 |
| 6 | CAM D3 (Y5) | OV2640 |
| 7 | CAM D0 (Y2) | OV2640 |
| 8 | CAM XCLK | OV2640 |
| 9 | CAM D7 (Y9) | OV2640 |
| 10 | CAM HREF | OV2640 |
| 11 | CAM VSYNC | OV2640 |
| 12 | IMU SCL + SCCB SCL | I2C 与 OV2640 控制共用 |
| 13 | IMU SDA + SCCB SDA | I2C 与 OV2640 控制共用 |
| 14 | 电池 ADC | ADC1_CH2 |
| 15 | CAM D4 (Y6) | OV2640 |
| 16 | CAM PCLK | OV2640 |
| 17 | CAM D5 (Y7) | OV2640 |
| 18 | CAM D6 (Y8) | OV2640 |
| 19 | USB DN | Type-C |
| 20 | USB DP | Type-C |
| 21 | IMU INT | BMI270 中断 |
| 38 | SD D0 | SDMMC 1-bit 数据线 |
| 39 | SD CLK | SDMMC 时钟 |
| 40 | SD CMD | SDMMC 命令线 |
| 41 | 传感器电源开关 | 高=通电，低=断电（摄像头/麦克风/LED）|
| 42 | MIC I2S BCLK | ICS43434 |
| 43 | MIC I2S WS | ICS43434 |
| 44 | MIC I2S DIN | ICS43434 |
| 45 | WS2816 LED | RMT 单线协议 |
| 47 | 电源开关机键 | BOOT 按键输入 |
| 48 | PWR_HOLD | 推挽输出，保持上电 |

---

## 总线分配

| 总线 | 设备 | GPIO | 状态 |
|---|---|---|---|
| I2C0 | IMU (BMI270) + OV2640 (SCCB) | SDA=13, SCL=12 | ✅ 共用，无冲突 |
| I2S0 | 麦克风 (ICS43434) | BCLK=42, WS=43, DIN=44 | ✅ 独立 |
| SDMMC | SD 卡（1-bit 模式） | CLK=39, CMD=40, D0=38 | ⚠️ D1/D2/D3 未接 ESP32，但卡侧 D3 必须有 10kΩ 外部上拉，否则卡进 SPI 模式 |
| RMT | WS2816 LED | GPIO45 | ✅ 独立 |
| Touch | 电容触摸 | GPIO1, GPIO2 | ✅ 独立 |
| USB | Type-C | GPIO19, GPIO20 | ✅ 独占 |

---

## 电源架构

```
开机：GPIO47 按键 → bsp_power_init_early() → GPIO48 拉高保持
传感器电源：GPIO41 高=开，低=关（摄像头/麦克风/LED）
  ⚠️ GPIO41 不控制 SD 卡 — SD 卡在主电源域（MCU 3V3），始终上电
SLEEP：GPIO41 拉低，传感器域断电（MCU/IMU/SD 常开）
关机：长按3s → bsp_power_safe_shutdown() → GPIO41低 → GPIO48低 → 全板断电
```

---

## 空闲 GPIO

| GPIO | 状态 |
|---|---|
| GPIO0 | 悬空，可用于其他功能 |
| GPIO3 | 悬空，可用于其他功能 |

---

## 待调试项

| 项目 | 说明 |
|---|---|
| BMI270 I2C 地址 | 默认 0x68，取决于 SDO 引脚接地/接高，可能需要改为 0x69 |
| OV2640 SCCB 地址 | 默认 0x30，摄像头驱动可能需要确认 |
| SD 卡速率 | 20MHz 已验证 6 轮稳定（写 475 KB/s，读 915 KB/s，1-bit 模式）。config.h `SD_MAX_FREQ_KHZ` 可降速。详见 `../05_测试记录/SD卡测试报告.md` |
| SD 卡 1-bit D3 上拉 | 卡侧 D3 必须有 10kΩ 外部上拉到 3V3，否则卡进 SPI 模式导致 mount 失败 |

---

## WiFi 模式

S3 的 WiFi 以已烧录固件实际配置为准。当前文档阶段不建议为了网络模式大改 S3；如果上传不稳定，优先使用 S3 真实 telemetry 样例由后端 replay。
