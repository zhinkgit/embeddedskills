# keil

Claude Code skill，驱动 Keil MDK 进行工程扫描、Target 枚举、编译构建，并返回可交给 `jlink/openocd` 的产物路径。`flash` 保留为兼容入口。

## 功能

- 扫描目录下的 .uvprojx / .uvmpw 工程文件
- 枚举工程中的 Target
- 增量编译 / 全量重建 / 清理
- 返回 `flash_file` / `debug_file` 等产物路径，便于继续交给 `jlink/openocd`
- 通过 Keil 下载固件到目标板（兼容入口）
- 解析构建日志，输出结构化错误/警告信息

## 环境要求

- [Keil MDK](https://www.keil.com/mdk5/) — 提供 UV4.exe
- Python 3.x（仅标准库，无额外依赖）

## 配置

### 环境级配置（skill/config.json）

复制 `config.example.json` 为 `config.json`，根据实际安装路径修改：

```json
{
  "uv4_exe": "C:\\Keil_v5\\UV4\\UV4.exe",
  "operation_mode": 1
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `uv4_exe` | 是 | UV4.exe 完整路径 |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |

### 工程级配置（workspace/.embeddedskills/config.json）

工程级共享配置保存在工作区的 `.embeddedskills/config.json` 中：

```json
{
  "keil": {
    "project": "",
    "target": "",
    "log_dir": ".embeddedskills/build"
  }
}
```

| 字段 | 说明 |
|------|------|
| `project` | 默认工程路径（相对 workspace） |
| `target` | 默认 Target 名称 |
| `log_dir` | 构建日志输出目录，默认 `.embeddedskills/build` |

### 参数解析优先级

参数解析顺序（从高到低）：
1. CLI 显式参数
2. 环境级配置（skill/config.json）
3. 工程级配置（.embeddedskills/config.json）
4. state.json（上次构建记录）
5. 搜索/询问
