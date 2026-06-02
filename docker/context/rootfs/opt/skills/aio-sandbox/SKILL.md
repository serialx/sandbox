---
name: aio
description: AIO Sandbox CLI — use this skill whenever a task requires interacting with the sandbox environment from INSIDE the container. This includes running shell commands, reading/writing files, searching code, browser automation, GUI desktop control, or querying sandbox metadata. Trigger on phrases like "run a command", "read/write a file", "search code", "take a screenshot", "navigate to URL", "click a button", "list files", "install packages", or any task requiring sandbox interaction. Also trigger when the user asks about how to use the aio CLI or its API. This skill covers the data plane — executing operations within a running sandbox.
allowed-tools: Bash(aio:*)
---

# AIO Sandbox CLI（容器内）

在 AIO Sandbox 容器内执行操作的 CLI 工具。容器内 `aio` 已预装在 `/usr/local/bin/aio`，直连本地 API，无需鉴权。

> **VLM 限制**：`screenshot` 生成图片，只有具备视觉理解能力的模型才能解读。如果你不具备 VLM 能力，用 `text`/`markdown`/`snapshot` 替代截图获取页面信息。详见 [browser-guide.md](../shared/browser-guide.md)。

## 命令总览

```bash
aio shell <command>       # Shell 执行（pipe 模式，分离 stdout/stderr）
aio tty <command>         # 交互终端（tmux 模式，合并输出）
aio file <subcommand>     # 文件操作（读写、搜索、替换、上传下载）
aio browser <subcommand>  # 浏览器自动化（Playwright SDK）
aio gui <subcommand>      # 桌面 GUI 控制（鼠标键盘）
aio sandbox info          # 沙箱环境信息
aio sandbox packages      # 已安装包列表
aio skills <subcommand>   # Skills 管理
aio mcp <subcommand>      # MCP 协议
```

## Shell 执行

两种模式，选择依据：需要分离 stdout/stderr 或 stdin 写入 → `shell`；交互式或长时间运行 → `tty`。

### aio shell（pipe 模式）

```bash
# 执行命令（简写）
aio shell "ls -la"
aio shell "pip install requests"

# 带选项
aio shell exec "python train.py" --timeout 300
aio shell exec "make build" --hard-timeout 60     # 60s 后强制 kill
aio shell exec "pytest" --dir /workspace/project

# 异步执行
aio shell exec "python train.py" --async           # → 返回 session_id
aio shell output <session-id> --wait               # 轮询输出
aio shell write <session-id> "yes\n"               # 写入 stdin
aio shell kill <session-id>                        # SIGTERM
aio shell kill <session-id> --signal SIGKILL       # 强制终止

# 会话管理
aio shell sessions
aio shell session-create --id dev --dir /workspace
aio shell session-close <id>
```

### aio tty（tmux 模式）

```bash
aio tty "npm test"
aio tty exec "make build" --id my-session
aio tty view <session-id>          # 查看输出
aio tty kill <session-id>          # 终止
aio tty sessions                   # 列出会话
aio tty session-create --id dev
aio tty session-delete <id>
```

> `aio bash` 是 `aio tty` 的隐藏别名，旧脚本可继续使用。

## 文件操作

```bash
# 读写
aio file read /workspace/main.py
aio file read /workspace/main.py --start 10 --end 30
aio file write /workspace/output.txt --content "Hello"
aio file write /workspace/data.txt --stdin < local.csv
aio file write /workspace/log.txt --content "line" --append

# 搜索替换
aio file replace /workspace/config.py --old "DEBUG=True" --new "DEBUG=False"
aio file search /workspace/app.py --regex "def\s+\w+"

# 跨文件搜索（grep，基于 ripgrep）
aio file grep "import torch" /workspace --include "*.py"
aio file grep "TODO|FIXME" /workspace -i -A 3 --max-results 50

# 文件查找
aio file find /workspace --glob "**/*.py"
aio file glob "**/*.ts" /workspace --metadata --sort modified

# 目录列表
aio file list /workspace --recursive --depth 3

# 上传下载
aio file upload ./local.txt --to /workspace/remote.txt
aio file download /workspace/result.csv -o ./result.csv
```

## 浏览器自动化

容器内置 Chromium，通过 Playwright SDK 驱动。

```bash
# 导航
aio browser navigate https://example.com
aio browser navigate https://app.com --wait-until networkidle

# 截图
aio browser screenshot -o page.png
aio browser screenshot --full                # 全页

# 交互
aio browser click "button.submit"
aio browser fill "search text" -s "#search"
aio browser type "slow input" --delay 50
aio browser press Enter
aio browser hotkey Control a

# 内容提取
aio browser text                             # 纯文本
aio browser markdown                         # Markdown
aio browser evaluate "document.title"        # 执行 JS
aio browser snapshot                         # 无障碍树

# 等待
aio browser wait load
aio browser wait selector --selector ".loaded" --timeout 10
aio browser wait network_idle
```

浏览器使用的完整场景和最佳实践详见 → [../shared/browser-guide.md](../shared/browser-guide.md)

## GUI 桌面控制

容器内置 VNC 桌面环境，可用 pyautogui 级别的鼠标键盘控制。

```bash
aio gui screenshot -o desktop.png
aio gui tap 500 300                  # 点击坐标
aio gui double-click 500 300
aio gui type "Hello"                 # 输入文字
aio gui press Enter
aio gui hotkey ctrl c
aio gui scroll --dx 0 --dy -300
aio gui wait 2                       # 等待 2 秒
aio gui info                         # 屏幕信息
```

## 沙箱信息

```bash
aio sandbox info                     # 环境信息（版本、目录、运行时）
aio sandbox packages --python        # Python 包列表
aio sandbox packages --nodejs        # Node.js 包列表
```

## Skills & MCP

```bash
# Skills
aio skills list
aio skills load my-skill
aio skills register --path /workspace/skills

# MCP
aio mcp list
aio mcp tools browser
aio mcp call browser screenshot
```

## 常用工作流

### 写脚本并运行

```bash
aio file write /workspace/script.py --content "import pandas as pd; print(pd.__version__)"
aio shell "python /workspace/script.py"
```

### 搜索代码 → 阅读 → 修改

```bash
aio file grep "def process_data" /workspace --include "*.py"
aio file read /workspace/src/utils.py --start 40 --end 60
aio file replace /workspace/src/utils.py --old "def process_data(x):" --new "def process_data(x, validate=True):"
```

### 网页抓取

```bash
aio browser navigate https://example.com/data
aio browser wait load
aio browser markdown > content.md
aio browser screenshot -o evidence.png
```

### 安装依赖并测试

```bash
aio shell "pip install -r requirements.txt" --timeout 120
aio shell "cd /workspace && python -m pytest tests/ -v"
```

### 长时间任务（异步）

```bash
aio shell exec "python train.py" --async
# → session_id: abc123
aio shell output abc123 --wait               # 等待输出
```

## 全局选项

| 选项 | 说明 |
|------|------|
| `--api-url <url>` | 覆盖 API 地址（容器内通常不需要） |
| `--output json\|table\|text` | 输出格式（TTY 默认文本，管道默认 JSON） |
| `-v, --verbose` | 显示请求详情 |
| `-q, --quiet` | 静默模式 |

## 深入参考

| 参考文档 | 何时查阅 |
|----------|---------|
| [../shared/browser-guide.md](../shared/browser-guide.md) | 浏览器使用场景、最佳实践、调试技巧 |
| [references/shell.md](references/shell.md) | Shell/TTY 完整参考、会话管理、异步执行 |
| [references/file.md](references/file.md) | 文件操作、grep/glob/search 完整用法 |
| [references/browser.md](references/browser.md) | 浏览器命令完整参数 |
| [references/gui.md](references/gui.md) | GUI 操作完整参数 |
| [references/configuration.md](references/configuration.md) | 配置文件、环境变量、输出模式 |
