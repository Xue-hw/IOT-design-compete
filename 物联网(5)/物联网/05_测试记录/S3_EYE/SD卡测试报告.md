# SD 卡测试报告

> 测试日期：2026-06-28
> 测试工程：`sd_test/`（独立 ESP-IDF v5.5 工程）
> 目标：验证 ESP32-S3 SDMMC 1-bit 模式 SD 卡读写稳定性与性能

## 1. 测试环境

| 项 | 值 |
|---|---|
| 芯片 | ESP32-S3（QFN56, rev v0.2） |
| Flash | 16MB（boya） |
| PSRAM | 8MB Octal（AP, 80MHz） |
| IDF | v5.5（`E:\esp\.espressif\v5.5\esp-idf`） |
| 测试卡 | 卡3：16GB SDHC（联想杂牌，格式化后恢复健康） |
| 卡座 | TF 卡模组（无 LDO，直通 MCU 3V3，SD 模式标注 DAT0/CMD/CLK） |
| 构建 | `python build.py build -DCMAKE_C_FLAGS=-O2`（绕过 GCC `-Og` IRA bug） |
| 烧录 | `python build.py -p COM4 flash monitor` |

## 2. 引脚配置

| 信号 | GPIO | 备注 |
|---|---|---|
| CLK | 39 | SDMMC 时钟 |
| CMD | 40 | SDMMC 命令线 |
| D0  | 38 | SDMMC 1-bit 数据线 |
| D1  | —  | 未接 ESP32（1-bit 模式）|
| D2  | —  | 未接 ESP32（1-bit 模式）|
| D3  | —  | 未接 ESP32，**卡侧必须有 10kΩ 外部上拉到 3V3**，否则卡进 SPI 模式 |

## 3. 电源域

```
SD 卡 ← 主电源域（MCU 3V3），始终上电，不受 GPIO41 控制
GPIO41 ← 只控制传感器域（摄像头/麦克风/LED）
```

⚠️ 旧 `bsp_sd.c:20` 注释「SD 卡电源域和传感器共用 GPIO41」是**错误的**，已纠正。

## 4. 问题与诊断（按时间顺序）

### 问题 1：ACMD41 超时 `0x107`（卡无响应）

**现象**：`sdmmc_init_ocr: send_op_cond (1) returned 0x107`，3 次重试全失败。

**排查过程**：
1. 硬件检查：TF 卡已插、上拉电阻 OK、38/39/40 连通 OK
2. 代码排查：d1/d2/d3 默认 GPIO4/12/13 被保存到 slot_gpio（width=1 时驱动不配置它们，但防御性设 NC）
3. 降速到 1MHz、400kHz — 仍超时（探测阶段固定 400kHz，`max_freq_khz` 不影响 ACMD41）
4. **根因**：卡1（SD/29818MB）在之前的 1MB 写块失败后文件系统损坏，进入脏状态

**修复**：
- 代码：d1/d2/d3 显式设 `GPIO_NUM_NC` + `SDMMC_SLOT_FLAG_INTERNAL_PULLUP`
- 硬件：拔卡用 Windows 格式化（FAT32 + 32KB cluster）

### 问题 2：DMA FRUN 错误 `status 0x400d00`

**现象**：mount OK，但写 blk 0 立即失败：
```
sdmmc_write_sectors_dma: sdmmc_send_cmd returned 0x107, status 0x400d00
```

**解析** `0x400d00`：
- bit 8 RTO = Response Timeout
- bit 10 HTO = Data Hunger Timeout
- bit 11 **FRUN** = FIFO Run Error（DMA 缓冲区问题）
- bit 22 = SDMMC 控制器高位错误

**根因**：`malloc(4KB)` 在 PSRAM 启用时分配到 PSRAM，SDMMC DMA 只能访问内部 RAM。

**修复**：改用 `heap_caps_malloc(size, MALLOC_CAP_DMA | MALLOC_CAP_8BIT)` 强制内部 RAM。

### 问题 3：CMD13 `0x109` 警告

**现象**：CYCLE 2 mount OK 后 `sdmmc_send_cmd_send_status returned 0x109`。

**根因**：与问题 2 同源 — DMA FRUN 导致卡状态异常，CMD13 状态查询失败。

**修复**：DMA buffer 修复后**自动消失**（CYCLE 2-6 无任何警告）。

### 问题 4：20MHz 写块超时（误判）

**现象**：20MHz mount OK，写到大文件某块时 `sdmmc_wait_for_idle timeout`。

**误判**：最初认为是 20MHz 信号完整性问题，降速到 400kHz。

**实际根因**：DMA buf 在 PSRAM（问题 2），与频率无关。DMA 修复后 20MHz 6 轮全稳定。

### 问题 5：GPIO41 放电循环（错误假设）

**现象**：仿 IDF `examples/peripherals/sdio/host/main/app_main.c` `slave_power_on()` 实现 GPIO41 放电→上电时序。

**根因**：错误假设 SD 卡在 GPIO41 传感器电源域。实际 SD 卡在主电源域（MCU 3V3），始终上电，放电循环无意义。

**修复**：移除放电循环，改为 boot 后单纯 `vTaskDelay(1000ms)` 等电源稳定。

### 问题 6：卡1/卡2 损坏（OV5640 装错）

**现象**：
- 卡1（SD/29818MB）：之前 PASS，1MB 写块失败后再也 mount 不了，Windows 识别慢、读取慢
- 卡2（SDABC/29843MB）：mount OK 但写 blk 0 始终失败
- 两张卡 Windows 格式化后建文件夹失败或极慢

**根因链**：
1. 上一个板子 OV5640 装错 FPC 插座 → 引脚错位 → 3V3 对地短路
2. ESP32 模组发热 → 3V3 电源纹波/跌落
3. SD 卡在擦写过程中 VCC 跌落到 <2.7V → 闪存坏块累积 + FAT 表损坏
4. 杂牌代工闪存本身寿命短，加速损坏

⚠️ **SD 卡不走 I²C**（走 SDMMC 协议，独立总线）。损坏是通过**电源域耦合**和**热扩散**，不是 I²C。

### 问题 7：4KB 循环 fwrite 1MB blk 32 失败

**现象**：4KB 块循环 fwrite 1MB，写到 blk 32（128KB 边界）超时。

**根因**：per-block fwrite 命令开销累积 + DMA buf 问题双重作用。

**修复**：改用一次性整文件 fwrite（`fwrite(buf, 1, size, f)`），让 FatFS/diskio 自行分块管理 DMA。1MB 一次性写 PASS。

### 问题 8：双 buffer 4MB OOM

**现象**：`alloc 4096KB failed (free=4500284)` — PSRAM 剩余 4.3MB，但双 buffer 需 8MB。

**根因**：双 buffer 方案（buf_wr + buf_rd）需 2× 文件大小内存。

**修复**：改单 buffer 方案 — 写时填充 buf→fwrite，读时 fread 到同一 buf→重新生成预期值比对。4MB 只需 4MB 内存。

### 问题 9：8MB 单 buffer 仍 OOM

**现象**：`alloc 8192KB failed (free=8694592)` — PSRAM 剩余 8.3MB，但 malloc(8MB) 失败。

**根因**：PSRAM 8MB 中系统占用部分 + 堆碎片化，单次连续 malloc(8MB) 无法满足。

**结论**：4MB 是当前 PSRAM 配置下的应用层 buffer 上限。SD 卡本身 4MB 读写完全稳定。

### 问题 10：文件系统脏状态导致 mount 失败

**现象**：大文件写失败后，即使拔插 SD 卡，再次 mount 仍 ACMD41 超时。

**根因**：USB 复位不会断电 SD 卡（SD 在主 3V3 始终上电），卡内部状态保持脏状态。拔插也不一定复位。

**修复**：Windows 格式化（FAT32 + 32KB cluster）后完全恢复。

## 5. 频率扫描结果

从高到低降速扫描，首次成功即停止。文件大小固定 1MB，单 buffer 方案。

| 频率 | 协商速度 | 写速度 | 读速度 | 校验 | 结果 |
|---|---|---|---|---|---|
| **20MHz** | **20.00 MHz** | **475 KB/s** | **915 KB/s** | ✅ OK | **6 轮全 PASS** |

20MHz 首次即成功，未降速。6 轮（CYCLE 1-6）全 PASS，无任何错误/警告。

## 6. 文件大小上限测试

400kHz + 单 buffer 方案，顺序倍增，首次失败即停止。

| 大小 | 写 | 读 | 校验 | 结果 |
|---|---|---|---|---|
| 128KB | 40 KB/s | 45 KB/s | ✅ | PASS |
| 256KB | 42 KB/s | 45 KB/s | ✅ | PASS |
| 512KB | 43 KB/s | 45 KB/s | ✅ | PASS |
| 1MB | 43 KB/s | 45 KB/s | ✅ | PASS |
| 2MB | 43 KB/s | 45 KB/s | ✅ | PASS |
| **4MB** | **43 KB/s** | **45 KB/s** | ✅ | **PASS** |
| 8MB | — | — | — | OOM（PSRAM malloc 上限） |

## 7. 性能数据汇总

### 400kHz vs 20MHz 对比

| 频率 | 写速度 | 读速度 | 写提升 | 读提升 |
|---|---|---|---|---|
| 400kHz | 43 KB/s | 45 KB/s | 1× | 1× |
| **20MHz** | **475 KB/s** | **915 KB/s** | **11×** | **20×** |

### 读写不对称分析

- 写 475 KB/s vs 读 915 KB/s — 写慢 ~2× 因为 SD 卡闪存擦写延迟 + FatFS 元数据开销
- 1-bit 模式理论上限 20MHz = 2.5 MB/s，实际读 915 KB/s（~37% 效率，杂牌卡 + FatFS 开销）

### 达到 2 MB/s 写速度的条件

当前 1-bit + 杂牌卡 = 475 KB/s 写。要达到 2 MB/s 写需要：
1. **4-bit 模式**（D1/D2/D3 接线）→ ~4× 提升 → ~1.9 MB/s
2. **正品 SanDisk Ultra/Samsung EVO 卡** → ~2-3× 杂牌写速度
3. 两者结合 → ~4-6 MB/s 写

## 8. DMA Buffer 要求

⚠️ **SDMMC DMA 只能访问内部 RAM，不能访问 PSRAM。**

| 分配方式 | 结果 |
|---|---|
| `malloc(4KB)`（PSRAM 启用） | ❌ FRUN 错误（buf 分到 PSRAM） |
| `heap_caps_malloc(4KB, MALLOC_CAP_DMA)` | ✅ 内部 RAM，稳定 |
| `malloc(1MB)`（应用层 buf，FatFS 内部中转） | ✅ 可用 PSRAM（FatFS/diskio 内部用内部 RAM 做 DMA） |

应用层大 buffer（如 1MB 一次性 fwrite）可用普通 `malloc`（分到 PSRAM），因为 FatFS/diskio 内部会用自己的内部 RAM buffer 做 DMA 中转。只有直接传给 SDMMC 驱动的 buffer 才必须在内部 RAM。

## 9. 回写修复清单

### `firmware/main/config.h`
- 节标题 `SDMMC 4-bit` → `SDMMC 1-bit`
- D1/D2/D3 注释补充外部上拉要求
- 新增 `SD_MAX_FREQ_KHZ` 可配置宏（默认 20MHz）

### `firmware/main/bsp/bsp_sd.c`
- 纠正电源注释（SD 在主电源域，不在 GPIO41）
- `host.max_freq_khz` 改用 `SD_MAX_FREQ_KHZ` 宏
- d1/d2/d3 显式设 `GPIO_NUM_NC`
- 新增 `SDMMC_SLOT_FLAG_INTERNAL_PULLUP`
- 顶部注释补充测试结论 + 遇到的问题与修复
- 删除未使用的 `unmount_cfg` 变量
- 日志 `5 retries` → `3 retries`

### `../03_硬件与采购/引脚分配_PINOUT.md`
- SD 卡接口描述补充「1-bit」+ D1/D2/D3 未接说明
- GPIO 38/39/40 备注 `SDMMC` → `SDMMC 1-bit 数据线/时钟/命令线`
- 总线分配表 SDMMC 行补充 D3 外部上拉要求
- 电源架构补充「GPIO41 不控制 SD 卡」
- 待调试项 SD 卡速率行更新为实测结论

### `AGENTS.md`
- Notable Gotchas 新增：SD 主电源域、D3 上拉要求、sd_test 项目说明
- SD 卡速率数据更新

## 10. 测试工具

`sd_test/` 工程保留为诊断工具，支持：
- 频率扫描（20MHz→400kHz 降速，首次成功即停止）
- 多文件大小测试（128KB→8MB，单 buffer 方案）
- LED 状态指示（绿=PASS / 红=FAIL / 蓝=运行中）

构建烧录：
```powershell
cd sd_test
python build.py build -DCMAKE_C_FLAGS=-O2
python build.py -p COM4 flash monitor
```

## 11. 待办

- [ ] 买正品 SanDisk Ultra 16GB/32GB SDHC 卡重测上限（杂牌卡写速度受限）
- [ ] 正式板子（非测试 TF 模组）SD 卡座走线质量验证
- [ ] 4-bit 模式测试（需硬件支持 D1/D2/D3 接线）
- [ ] `bsp_sd_recover_wavs()` 实现（WAV RIFF 头修复）
