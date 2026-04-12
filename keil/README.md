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

复制 `config.example.json` 为 `config.json`，根据实际安装路径修改：

```json
{
  "uv4_exe": "C:\\Keil_v5\\UV4\\UV4.exe",
  "default_target": "",
  "log_dir": ".build",
  "operation_mode": 1
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `uv4_exe` | 是 | UV4.exe 完整路径 |
| `default_target` | 否 | 默认 Target 名称，为空时从工程中选择 |
| `log_dir` | 否 | 构建日志输出目录，默认 `.build` |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |
