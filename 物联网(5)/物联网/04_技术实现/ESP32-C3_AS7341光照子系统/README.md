# ESP32-C3 + AS7341 光照子系统

AS7341 物理连接在 ESP32-C3 上，为当前选中的 EYE 或 Cube 主设备提供真实光照数据。C3 保留物理来源标识，但不作为第三个产品节点展示。

## 数据链路

这里的代理是电脑上运行的 `telemetry_bridge.py`，不是另一块硬件。完整数据流为：

```text
AS7341 -> ESP32-C3 -> TCP 3333 -> telemetry_bridge.py -> 后端 telemetry 接口
```

C3 固件输出原始光谱通道；桥接程序将它换算成当前未标定的估算照度，生成 FocusCube telemetry JSON，再上传给后端。

## 配置与运行

1. 将 `secrets.example.h` 复制为 `secrets.h`，只在本机填写 Wi-Fi 名称与密码。`secrets.h` 已被仓库根目录的 `.gitignore` 排除。
2. 用 Arduino IDE 打开并烧录 `C3_AS7341_WiFiTCP.ino`。
3. 从串口监视器读取 C3 的局域网 IP。
4. 在本目录运行：

```powershell
python telemetry_bridge.py <C3_IP> --backend-url http://<BACKEND_IP>:8000
```

后端地址是环境参数，变化时通过命令行更新，禁止写死进代码。局域网实测地址不作为永久配置提交。

默认上传身份已经固定为：

```text
device_id=focuscube-c3-proxy-01
source=c3-as7341-proxy
```

真实光照保留 `light.lux` 和 `light.label`。尚未接入的三组数据固定为：

- `imu.valid=false`，`mode=unknown`；
- `focus.valid=false`，`state=idle`；
- `power.valid=false`，`battery_pct=0`。

已与后端统一的分类阈值为：

- `lux < 200`：`too_dim`；
- `200 <= lux <= 500`：`suitable`；
- `lux > 500`：`too_bright`。

完整请求样例见 [`examples/telemetry-as7341-proxy.json`](examples/telemetry-as7341-proxy.json)。

## 验证

```powershell
python -m unittest -v
```

测试覆盖照度换算、阈值边界、默认设备身份、无效占位字段、本地 HTTP POST 以及后端超时不中断采集。

注意：`light.lux` 目前是基于 AS7341 通道加权和比例系数得到的估算值，未使用标准照度计标定，材料中不得表述为计量级照度。
