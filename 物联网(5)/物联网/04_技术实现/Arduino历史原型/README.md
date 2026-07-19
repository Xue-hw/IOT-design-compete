# Arduino 历史原型

本目录保存两份 Arduino UNO 智能家居原型，功能包括光照、PIR、DHT11、RGB 灯、风扇、OLED、舵机，以及增强版中的 MFRC522 RFID。

它们与当前 FocusCube 的 ESP32-S3/P4 主线硬件和 telemetry 协议不同，仅用于保留历史代码：

- `SmartHome_NoCamera/`：基础版，软件 SPI OLED，无 RFID。
- `SmartHome_NoCamera_RFID/`：增强版，硬件 SPI OLED、RFID 和灯光亮度控制。

## Arduino 依赖

- Adafruit NeoPixel
- DHT sensor library
- Servo
- U8g2
- MFRC522（仅增强版）
- SPI（Arduino 内置，仅增强版）

## 安全说明

这两份代码是教学原型，不可直接用于真实门禁：

- 串口开门码固定为 `1234`；
- RFID 增强版当前会接受任意可读取卡片，没有 UID 白名单；
- 串口会输出 RFID UID。

如需继续使用，必须先移除固定口令、增加 UID 白名单，并避免在公开日志中输出完整 UID。
