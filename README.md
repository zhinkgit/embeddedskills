<div align="center">

简体中文 | [English](./README.en.md)

# ⚡ embeddedskills

### 让 AI 直接操控你的编译器、调试器和通信总线

[![MIT License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/status-active-success?style=flat-square)]()
<br>
<img src="https://img.shields.io/badge/Claude_Code-black?style=flat-square&logo=anthropic&logoColor=white">
<img src="https://img.shields.io/badge/OpenAI_Codex_CLI-412991?style=flat-square&logo=openai&logoColor=white">
<img src="https://img.shields.io/badge/Cursor-000?style=flat-square&logo=cursor&logoColor=white">
<img src="https://img.shields.io/badge/Kiro-232F3E?style=flat-square&logo=amazon&logoColor=white">

**不再手动编译、不再手动烧录、不再手动调试。**<br>
AI 自主完成从写代码到验证功能的全部步骤。

如果觉得项目对你有帮助，请点一个免费的 ⭐

</div>

---

## 为什么需要它

AI 改完代码 → 你手动编译 → 你手动烧录 → 你把报错复制给 AI → 循环往复……

**embeddedskills 把这个循环交给 AI 自己跑：**

```
写代码  →  编译  →  烧录  →  调试  →  发现问题  →  自动修正
  ↑_______________________________|
```

| | 传统 AI 辅助 | AI + embeddedskills |
|---|---|---|
| 代码编写 | AI | AI |
| 编译构建 | 你 | AI 调用 Keil / GCC |
| 烧录下载 | 你 | AI 调用 J-Link / OpenOCD |
| 调试验证 | 你 | AI 断点 / 寄存器 / 内存 |
| 通信调试 | 你 | AI 串口 / CAN / 网络 |
| 错误修正 | 你复制报错给 AI | AI 自主读取并修正 |

---

## Skill 一览

| 分类 | Skill | 能做什么 |
|---|---|---|
| 构建 | **keil** | 扫描工程、枚举 Target、编译 / 重建 / 清理 / 烧录 |
| 构建 | **gcc** | CMake 工程配置、编译、大小分析 |
| 调试 | **jlink** | 烧录、读写内存/寄存器、RTT/SWO、GDB 调试 |
| 调试 | **openocd** | 烧录、擦除、GDB/Telnet、Semihosting/ITM |
| 通信 | **serial** | 扫描串口、实时监控、发送数据、Hex 查看 |
| 通信 | **can** | CAN/CAN-FD 监控、发帧、DBC 解码、统计 |
| 通信 | **net** | 抓包分析、连通性测试、端口扫描、流量统计 |
| 编排 | **workflow** | 自动识别工程 → 选择工具链 → 串联全流程 |

> `Keil / GCC` 与 `J-Link / OpenOCD` 可自由正交组合。

---

## 安装

```bash
# 一键安装全部 skill（推荐）
npx skills add https://github.com/zhinkgit/embeddedskills -g -y

# 只安装需要的 skill
npx skills add https://github.com/zhinkgit/embeddedskills --skill jlink -g -y

# 管理
npx skills ls -g        # 查看已安装
npx skills update -g    # 更新
npx skills remove -g    # 移除
```

或者直接 clone：

```bash
git clone https://github.com/zhinkgit/embeddedskills ~/.claude/skills/embeddedskills
```

**[→ 完整安装与使用手册](docs/getting-started.md)**

---

## 工作原理

每个 Skill 是一组 Python 脚本，封装底层工具的命令行交互，通过 `SKILL.md` 以自然语言暴露给 AI。

**统一 JSON 输出格式**，AI 直接解析并决策下一步：

```json
{
  "status": "ok | error",
  "summary": "简短摘要",
  "details": {},
  "artifacts": {},
  "next_actions": []
}
```

**三层配置**，按需覆盖：

| 层级 | 位置 | 内容 |
|---|---|---|
| 环境级 | `skill/config.json` | 工具路径、本机硬件参数 |
| 工程级 | `workspace/.embeddedskills/config.json` | 目标芯片、接口、日志目录 |
| 运行状态 | `workspace/.embeddedskills/state.json` | 最近一次构建 / 烧录 / 调试记录 |

---

## 外部依赖

| Skill | 依赖 |
|---|---|
| keil | Keil MDK (UV4.exe) |
| gcc | CMake · Ninja/Make · ARM GNU Toolchain |
| jlink | SEGGER J-Link Software · arm-none-eabi-gdb |
| openocd | OpenOCD · 调试器驱动 (ST-Link / CMSIS-DAP / DAPLink) |
| serial | pyserial · USB 转串口驱动 |
| can | python-can · cantools · pyserial · USB-CAN 驱动 |
| net | Wireshark (tshark) · Npcap |

---

## 完成进度

| Skill | 状态 |
|---|---|
| keil | ✅ 已完成测试 |
| gcc | ✅ 已完成测试 |
| jlink | ✅ 已完成测试 |
| openocd | ✅ 已完成测试 |
| serial | ✅ 已完成测试 |
| net | ✅ 已完成测试 |
| workflow | ✅ 已完成测试 |
| can | 🔧 待测试 |

---

## Star History

<a href="https://www.star-history.com/?repos=zhinkgit%2Fembeddedskills&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/image?repos=zhinkgit/embeddedskills&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/image?repos=zhinkgit/embeddedskills&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/image?repos=zhinkgit/embeddedskills&type=date&legend=top-left" />
 </picture>
</a>

欢迎提 Issue 和 PR。感谢 [Linux.do](https://linux.do/) 社区支持。
