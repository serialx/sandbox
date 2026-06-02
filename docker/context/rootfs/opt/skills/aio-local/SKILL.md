---
name: aio-local
description: AIO CLI local sandbox management — use this skill when working OUTSIDE the sandbox container on your local machine. This covers creating/managing sandbox containers, installing runtime dependencies, SSO login, opening the web UI, and executing commands against local sandboxes. Trigger when user mentions "create sandbox", "start sandbox", "aio install", "aio login", "manage containers", "local development with sandbox", "debug sandbox", "connect to sandbox", or any task involving sandbox container lifecycle management from the host machine. Also trigger when user asks about AIPAAS, cloud sandbox, or how to set up a local sandbox development environment.
allowed-tools: Bash(aio:*)
---

# AIO CLI 本地沙箱管理

在宿主机（你的笔记本/开发机）上管理 AIO Sandbox 容器的 CLI 工具。

> **VLM 限制**：`screenshot` 生成图片，只有具备视觉理解能力的模型才能解读。如果你不具备 VLM 能力，用 `text`/`markdown`/`snapshot` 替代截图获取页面信息。详见 [browser-guide.md](../shared/browser-guide.md)。

## 安装

```bash
# npx 直接使用
npx --registry=https://bnpm.byted.org @byted/aio-cli --help

# 全局安装
npm install -g @byted/aio-cli --registry=https://bnpm.byted.org

# 或从源码
cd aio-sandbox/aio-cli && npm install && npm run build && npm link
```

## 快速开始

### Docker（默认）

```bash
# 1. 安装运行时依赖（首次）
aio install

# 2. 创建沙箱
aio sandbox create dev

# 3. 等待就绪后使用
aio shell "echo hello"
aio web                    # 打开 Web UI

# 4. 清理
aio sandbox stop dev && aio sandbox rm dev
```


## 运行时安装

`aio install` 自动检测环境并安装缺失依赖：

```bash
aio install              # 全部安装（Docker/Colima）
aio install colima       # 只装 Colima（macOS 无 Docker Desktop 时）
aio install nerdctl      # 只装 nerdctl
aio install image        # 只构建 sandbox:latest 镜像
aio install boxlite      # 安装 BoxLite SDK（npm install -g @boxlite-ai/boxlite）
```

**Docker 检测逻辑：** 有 Docker 直接用 → 没有则装 Colima + nerdctl → 没 Homebrew 报错。全程幂等，重复运行跳过已完成步骤。

**BoxLite 支持平台：** macOS ARM64、Linux（x86_64/ARM64）、Windows WSL2。

## 沙箱生命周期

### 创建

```bash
aio sandbox create dev                                     # 默认端口 8080
aio sandbox create dev --port 18080                        # 指定端口
aio sandbox create dev --cpus 4 --memory 8g --shm-size 8g # 资源限制
aio sandbox create dev -e TZ=Asia/Shanghai                 # 环境变量
aio sandbox create dev -e GITHUB_TOKEN=ghp_xxx             # GitHub Token
aio sandbox create dev -v ~/projects:/home/gem/projects    # 目录挂载

# 完整示例
aio sandbox create dev \
  --port 18080 \
  --cpus 4 --memory 8g \
  -e TZ=Asia/Shanghai \
  -e GITHUB_TOKEN=ghp_xxx \
  -e RUN_HOOK_POST_READY="pip install torch" \
  -v ~/projects:/home/gem/projects
```

### 查看

```bash
aio sandbox list                  # 列表（NAME / STATUS / PORT / IMAGE）
aio sandbox status dev            # 详情 + HTTP 健康检查
aio sandbox logs dev --tail 50    # 日志
aio sandbox logs dev --follow     # 实时跟踪
```

### 停止与删除

```bash
aio sandbox stop dev              # 停止
aio sandbox rm dev                # 删除
aio sandbox rm dev --force        # 强制删除（运行中也可删）
```

## 使用沙箱

沙箱就绪后，通过 `aio shell`/`aio tty`/`aio browser` 等命令操作：

```bash
# 默认连 localhost:8080
aio shell "ls -la"
aio browser navigate https://example.com
aio browser screenshot -o page.png

# 非默认端口时指定 API 地址
aio --api-url http://localhost:18080 shell "python --version"
aio --api-url http://localhost:18080 browser navigate https://example.com
```

### 打开 Web UI

```bash
aio web                    # 打开 http://localhost:8080
aio web --port 18080       # 指定端口
```

Web UI 提供 JupyterLab、VS Code Server、VNC 桌面、文件管理器等。

## SSO 登录

通过字节跳动 SSO 获取 JWT Token，用于后续云端 AIPAAS 鉴权。

```bash
aio login                     # 默认 cn region
aio login --region i18n       # 海外 region
aio login --region eu-ttp     # EU region
```

### 支持的 Region

| Region | 域名 | 说明 |
|--------|------|------|
| `cn` | cloud.bytedance.net | 国内（默认） |
| `test` | cloud.bytedance.net | 内部测试 |
| `i18n` | cloud.tiktok-row.net | 海外 I18N（Maliva/SG） |
| `eu-ttp` | cloud-eu.tiktok-row.net | EU-TTP |
| `i18nbd` | cloud.byteintl.net | non-TT 控制面 |
| `tx` | cloud-ttp-us.bytedance.net | US-TTP |
| `sinf` | cloud.bytedance.net | 小基架 |
| `sinfi18n` | cloud-i18n.sinf.net | 小基架海外 |

### Token 优先级

1. `--token <jwt>` 命令行参数
2. `AIO_JWT_TOKEN` 环境变量
3. `~/.aio/credentials.json`（过期自动失效）

### 前置依赖

`aio login` 依赖 `@byted/uni-sso`。非字节内网环境可直接设置 `AIO_JWT_TOKEN` 环境变量。

## AIPAAS 是什么？

**AIPAAS** 是字节跳动的云端沙箱控制面服务，提供远端沙箱的创建、管理和访问能力。

### 本地 vs BoxLite vs 云端

| 维度 | 本地 Docker | BoxLite 微虚拟机 | 云端 AIPAAS |
|------|------------|-----------------|------------|
| **运行环境** | Docker/Colima 容器 | 硬件虚拟化 VM | 字节云端集群 |
| **创建方式** | `aio sandbox create` | `aio sandbox create` | SDK `client.create_session()` |
| **切换方式** | `aio use --local` | `aio use --boxlite` | `aio use <psm>` |
| **鉴权** | 无需 | 无需 | JWT Token |
| **适用场景** | 本地开发调试 | 轻量本地隔离 | 生产/CI/CD |
| **镜像** | `sandbox:latest`（本地构建） | 字节内部镜像（自动拉取） | 云端管理 |
| **数据面 API** | `http://localhost:<port>` | `http://localhost:<port>` | `https://port-8080-<sid>.<domain>` |
| **数据面命令** | 完全一致 | 完全一致 | 完全一致 |

### AIPAAS SDK 使用

```python
# Python SDK
from bytedance.bytedai.sandbox import AsyncSandboxClient, SandboxRegion

async with AsyncSandboxClient(psm="your.psm", region=SandboxRegion.CN) as client:
    async with await client.create_session(ttl=300) as session:
        result = await session.aio.shell.exec_command(command="ls -la")
        print(result.data.output)

        await session.aio.browser.navigate(url="https://example.com")
        await session.aio.browser.screenshot()
```

```javascript
// JavaScript SDK
import { SandboxClient } from "@byted/sandbox-sdk";

const client = new SandboxClient({ psm: "your.psm", region: "cn" });
const session = await client.createSession({ ttl: 300 });
const result = await session.aio.shell.execCommand({ command: "ls -la" });
```

无论本地还是云端，数据面 API 完全一致——你在本地用 `aio shell`、`aio browser` 调试的命令，云端 SDK 里也是一样的调用。

## 调试工作流

### 典型开发流程

```bash
# 1. 安装运行时
aio install

# 2. 创建带调试配置的沙箱
aio sandbox create debug \
  --port 18080 \
  -e LOG_TOOL_TRACE=true \
  -v ~/my-project:/home/gem/project

# 3. 打开 Web UI 查看状态
aio web --port 18080

# 4. 执行命令调试
aio --api-url http://localhost:18080 shell "cd /home/gem/project && python main.py"

# 5. 查看浏览器状态
aio --api-url http://localhost:18080 browser screenshot -o debug.png

# 6. 看容器日志排查问题
aio sandbox logs debug --tail 100

# 7. 清理
aio sandbox stop debug && aio sandbox rm debug
```

### 常见问题排查

| 问题 | 排查方法 |
|------|---------|
| 容器启动失败 | `aio sandbox logs <name> --tail 50` |
| 端口冲突 | 换一个端口 `--port 18081` |
| 健康检查不通过 | `aio sandbox status <name>` 查看详情 |
| 浏览器操作失败 | `aio browser restart` 重启浏览器 |
| 命令超时 | 加 `--timeout 300` 或 `--hard-timeout 120` |
| 找不到文件 | `aio shell "ls -la /home/gem/"` 确认路径 |

### 多沙箱并行

为前后端或不同项目创建独立沙箱：

```bash
aio sandbox create frontend --port 18080 -v ~/fe:/home/gem/project
aio sandbox create backend --port 18081 -v ~/be:/home/gem/project

aio --api-url http://localhost:18080 shell "npm run dev"
aio --api-url http://localhost:18081 shell "python manage.py runserver"
```

## 常用环境变量

创建沙箱时通过 `-e` 传入：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TZ` | `Asia/Singapore` | 时区 |
| `WORKSPACE` | `/home/gem` | 工作目录 |
| `GITHUB_TOKEN` | | GitHub Token |
| `HOMEPAGE` | | 浏览器首页 |
| `RUN_HOOK_INIT` | | 初始化脚本 |
| `RUN_HOOK_POST_READY` | | 就绪后脚本 |
| `BROWSER_EXTRA_ARGS` | | Chromium 额外参数 |
| `MAX_SHELL_SESSIONS` | `10` | 最大 Shell 会话数 |
| `AIO_SKILLS_PATH` | | 自动注册 Skills 路径 |
| `LOG_TOOL_TRACE` | `false` | 工具调用追踪日志 |

## 浏览器自动化

本地沙箱和容器内使用完全一致的浏览器 API：

```bash
aio browser navigate https://example.com
aio browser screenshot --full -o page.png
aio browser click "#submit"
aio browser text
aio browser snapshot
```

完整使用场景和最佳实践 → [../shared/browser-guide.md](../shared/browser-guide.md)

## 全局选项

| 选项 | 说明 |
|------|------|
| `--api-url <url>` | 指定沙箱 API 地址 |
| `--output json\|table\|text` | 输出格式 |
| `-v, --verbose` | 显示请求详情 |
| `-q, --quiet` | 静默模式 |
