# ESP32-C3 临时光照采集端

当前 C3 连接 AS7341，负责在 S3 成品到位前提供真实光照数据。C3 本身只输出光谱采样 JSON；电脑上的 `telemetry_bridge.py` 完成估算照度换算、协议组装和 HTTP 上传。

固定身份：

```text
device_id=focuscube-c3-proxy-01
source=c3-as7341-proxy
```

工程见 [`as7341-wifi-tcp/`](as7341-wifi-tcp/)。
