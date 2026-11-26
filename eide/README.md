# eide

驱动 EIDE (Embedded IDE) 进行工程扫描、构建配置枚举、编译构建，并返回可交给 `jlink/openocd` 的产物路径。

## 功能

- 扫描目录下的 EIDE 工程（含 `.eide/eide.yml` 的目录）
- 枚举工程中的构建配置 (ConfigName)
- 增量编译 / 全量重建 / 清理
- 返回 `elf_file` / `hex_file` 等产物路径，便于继续交给 `jlink/openocd`
- ELF 大小分析（text/data/bss 和内存使用）
- 解析构建日志，输出结构化错误/警告信息

## 环境要求

- [VS Code](https://code.visualstudio.com/) — 提供 `code` CLI
- [EIDE 扩展](https://marketplace.visualstudio.com/items?itemName=cl.eide) — 提供 `unify_builder`
- ARM CC (AC5/AC6) 或 arm-none-eabi-gcc — 工具链
- Python 3.x — 运行脚本（需要 PyYAML）
- PyYAML — `pip install pyyaml`

## 配置

### 环境级配置（skill/config.json）

复制 `config.example.json` 为 `config.json`，根据实际安装路径修改：

```json
{
  "builder_dir": "C:\\Users\\<user>\\.vscode\\extensions\\cl.eide-3.27.0\\res\\tools\\win32\\unify_builder",
  "builder_exe": "unify_builder.exe",
  "code_exe": "code",
  "toolchain_prefix": "arm-none-eabi-",
  "operation_mode": 1
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `builder_dir` | 是 | EIDE unify_builder 所在目录 |
| `builder_exe` | 否 | builder 可执行文件名，默认 `unify_builder.exe` |
| `code_exe` | 否 | VS Code CLI 路径，默认从 PATH 查找 |
| `toolchain_prefix` | 否 | size 分析用的工具链前缀，默认 `arm-none-eabi-` |
| `operation_mode` | 否 | `1` 直接执行 / `2` 输出风险摘要 / `3` 执行前确认 |

### 工程级配置（workspace/.embeddedskills/config.json）

工程级共享配置保存在工作区的 `.embeddedskills/config.json` 中：

```json
{
  "eide": {
    "project": "",
    "config": "",
    "log_dir": ".embeddedskills/build"
  }
}
```

| 字段 | 说明 |
|------|------|
| `project` | 默认工程根目录（相对 workspace，包含 `.eide/eide.yml`） |
| `config` | 默认构建配置名称 |
| `log_dir` | 构建日志输出目录，默认 `.embeddedskills/build` |

### 参数解析优先级

参数解析顺序（从高到低）：
1. CLI 显式参数
2. 工程级配置（.embeddedskills/config.json）
3. .embeddedskills/state/eide.json（上次构建记录）
4. 搜索/询问

工具路径依次读取 CLI、本机配置和 PATH。
