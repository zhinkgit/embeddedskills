# OpenOCD 常见 Board / Interface / Target 配置速查表

OpenOCD 通过 `.cfg` 配置文件组合来描述调试链路。优先使用 `board` 配置（已包含 interface 和 target），否则手动组合 `interface + target`。

## Interface（调试器）

| 调试器类型 | 配置文件 |
|-----------|---------|
| ST-Link V2 | `interface/stlink.cfg` |
| ST-Link V3 | `interface/stlink.cfg` |
| CMSIS-DAP | `interface/cmsis-dap.cfg` |
| DAPLink | `interface/cmsis-dap.cfg` |
| J-Link | `interface/jlink.cfg` |
| FTDI 系列 | `interface/ftdi/minimodule.cfg` 等 |

## Target（目标芯片）

### STMicroelectronics

| 系列 | 配置文件 |
|------|---------|
| STM32F0 | `target/stm32f0x.cfg` |
| STM32F1 | `target/stm32f1x.cfg` |
| STM32F2 | `target/stm32f2x.cfg` |
| STM32F3 | `target/stm32f3x.cfg` |
| STM32F4 | `target/stm32f4x.cfg` |
| STM32F7 | `target/stm32f7x.cfg` |
| STM32G0 | `target/stm32g0x.cfg` |
| STM32G4 | `target/stm32g4x.cfg` |
| STM32H7 | `target/stm32h7x.cfg` |
| STM32L0 | `target/stm32l0x.cfg` |
| STM32L1 | `target/stm32l1x.cfg` |
| STM32L4 | `target/stm32l4x.cfg` |
| STM32U5 | `target/stm32u5x.cfg` |
| STM32WB | `target/stm32wbx.cfg` |
| STM32WL | `target/stm32wlx.cfg` |

### GigaDevice

| 系列 | 配置文件 |
|------|---------|
| GD32F1x3 | `target/stm32f1x.cfg`（兼容） |
| GD32F3x0 | `target/stm32f1x.cfg`（兼容） |
| GD32F4xx | `target/stm32f4x.cfg`（兼容） |
| GD32E103 | `target/stm32f1x.cfg`（兼容） |

> GigaDevice 芯片通常兼容对应 STM32 系列的 target 配置。

### Nordic Semiconductor

| 系列 | 配置文件 |
|------|---------|
| nRF51 | `target/nrf51.cfg` |
| nRF52 | `target/nrf52.cfg` |

### NXP

| 系列 | 配置文件 |
|------|---------|
| LPC1768 | `target/lpc1768.cfg` |
| LPC4088 | `target/lpc4088.cfg` |

### ESP32

| 系列 | 配置文件 |
|------|---------|
| ESP32 | `target/esp32.cfg` |
| ESP32-S2 | `target/esp32s2.cfg` |
| ESP32-S3 | `target/esp32s3.cfg` |
| ESP32-C3 | `target/esp32c3.cfg` |

## Board（开发板，已包含 interface + target）

| 开发板 | 配置文件 |
|--------|---------|
| STM32F4 Discovery | `board/stm32f4discovery.cfg` |
| STM32F429 Discovery | `board/stm32f429disc1.cfg` |
| STM32F746 Discovery | `board/stm32f746g-disco.cfg` |
| STM32 Nucleo-F401RE | `board/st_nucleo_f4.cfg` |
| STM32 Nucleo-L476RG | `board/st_nucleo_l476rg.cfg` |
| nRF52-DK | `board/nordic_nrf52_dk.cfg` |

## 查找完整列表

如果上表未包含目标配置，可通过以下方式查找：

1. 列出 OpenOCD 自带配置：`ls <openocd-scripts-dir>/target/`
2. 在 OpenOCD 官方文档搜索: https://openocd.org/doc-release/html/index.html
3. 运行 `openocd -f interface/stlink.cfg -f target/stm32f4x.cfg -c "init; targets; shutdown"` 验证组合
