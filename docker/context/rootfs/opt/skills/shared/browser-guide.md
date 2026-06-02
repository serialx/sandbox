# Browser Automation Guide

AIO Sandbox 内置 Chromium 浏览器，通过 Playwright SDK 提供完整的浏览器自动化能力。无论你在容器内还是容器外使用 `aio browser`，API 完全一致。

## 截图与视觉能力

`screenshot` 命令生成的是图片，只有具备视觉理解（VLM）能力的模型才能解读截图内容。如果你不具备 VLM 能力（无法理解图片），**不要使用 `screenshot` 来获取页面信息**，改用以下文本替代方案：

| 需求 | VLM 模型 | 非 VLM 模型 |
|------|----------|-------------|
| 了解页面内容 | `screenshot` | `text` 或 `markdown` |
| 定位交互元素 | `screenshot` | `snapshot`（无障碍树） |
| 调试页面状态 | `screenshot` | `snapshot` + `evaluate "document.title"` |
| 留存证据 | `screenshot -o file.png` | `screenshot -o file.png`（保存给用户查看，但自己不读取） |

简单判断：如果你能读取并理解图片文件的内容，你就具备 VLM 能力，可以使用截图；否则请始终使用文本方案。

## 核心命令速查

```bash
# 导航
aio browser navigate <url>
aio browser navigate <url> --wait-until networkidle

# 截图
aio browser screenshot                    # 当前视口
aio browser screenshot --full -o page.png # 全页截图

# 交互
aio browser click "<selector>"            # 点击元素
aio browser fill "<text>" -s "<selector>" # 填写输入框
aio browser type "<text>" --delay 50      # 逐字输入
aio browser press Enter                   # 按键
aio browser hotkey Control a              # 组合键
aio browser scroll --dir down --amt 500   # 滚动

# 内容提取
aio browser text                          # 页面纯文本
aio browser markdown                      # 页面 Markdown
aio browser html                          # 页面 HTML
aio browser evaluate "document.title"     # 执行 JS

# 等待
aio browser wait load                     # 等页面加载
aio browser wait network_idle             # 等网络空闲
aio browser wait selector --selector ".done" --timeout 10

# 无障碍树
aio browser snapshot                      # 获取交互元素树
```

## 使用场景与最佳实践

### 场景 1：网页抓取

从网页提取结构化数据。优先用 `markdown` 或 `text`，避免解析原始 HTML。

```bash
aio browser navigate https://example.com/pricing
aio browser wait load
aio browser markdown          # 结构化内容，最适合 AI 解析
aio browser screenshot -o pricing.png   # 留证据
```

**最佳实践：**
- 用 `--wait-until networkidle` 等动态内容加载完
- 先 `text` 或 `markdown` 获取内容，比 `html` 更干净
- 截图留档，方便后续验证

### 场景 2：表单填写与提交

自动化登录、搜索、提交等表单操作。

```bash
aio browser navigate https://app.com/login
aio browser fill "admin" -s "#username"
aio browser fill "password123" -s "#password"
aio browser click "button[type=submit]"
aio browser wait url --url "**/dashboard"
aio browser screenshot -o logged-in.png
```

**最佳实践：**
- 每步之间用 `wait` 确保页面状态就绪
- 提交后用 `wait url` 或 `wait selector` 确认跳转
- 用 CSS selector 而非 XPath，更稳定

### 场景 3：SPA 应用交互

单页应用需要等待动态渲染。

```bash
aio browser navigate https://spa-app.com
aio browser wait network_idle
aio browser click ".nav-item:nth-child(3)"
aio browser wait selector --selector ".content-loaded"
aio browser text
```

**最佳实践：**
- SPA 导航不会触发页面加载事件，用 `wait selector` 或 `wait network_idle`
- 用 `snapshot` 获取可交互元素列表，比猜 selector 更可靠
- 复杂交互先 `screenshot` 看当前状态

### 场景 4：截图文档生成

批量截图用于文档或报告。

```bash
# 多页截图
for url in "https://app.com/page1" "https://app.com/page2"; do
  aio browser navigate "$url"
  aio browser wait load
  aio browser screenshot -o "$(echo $url | sed 's/[^a-zA-Z0-9]/_/g').png"
done

# 全页长截图
aio browser navigate https://app.com/long-page
aio browser screenshot --full -o full-page.png
```

### 场景 5：JavaScript 执行与数据提取

当 `text`/`markdown` 不够用时，直接执行 JS。

```bash
# 提取表格数据为 JSON
aio browser evaluate "JSON.stringify([...document.querySelectorAll('table tr')].map(r => [...r.cells].map(c => c.textContent)))"

# 获取所有链接
aio browser evaluate "[...document.querySelectorAll('a')].map(a => ({text: a.textContent, href: a.href}))"

# 修改页面状态
aio browser evaluate "document.querySelector('.modal-close').click()"
```

### 场景 6：多标签页工作流

需要在多个页面间切换时。

```bash
aio browser navigate https://app.com/dashboard
aio browser tab-new https://docs.example.com
aio browser tabs              # 查看所有标签页
aio browser tab-close 1       # 关闭第二个标签页
```

## 调试技巧

### 元素找不到？

```bash
# 1. 先截图看当前页面状态
aio browser screenshot -o debug.png

# 2. 用 snapshot 获取可交互元素列表
aio browser snapshot

# 3. 用 evaluate 验证 selector
aio browser evaluate "document.querySelector('#my-element')?.textContent"
```

### 页面没加载完？

```bash
# 方法 1: 等网络空闲
aio browser wait network_idle

# 方法 2: 等特定元素出现
aio browser wait selector --selector ".loaded" --timeout 15

# 方法 3: 等 URL 变化
aio browser wait url --url "**/success"
```

### 浏览器状态异常？

```bash
# 软重启（清除页面状态，保留浏览器进程）
aio browser restart --mode soft

# 硬重启（重启浏览器进程）
aio browser restart
```

## 完整命令参考

### navigate
```
aio browser navigate <url> [--wait-until load|domcontentloaded|networkidle|commit] [--timeout <s>]
```

### screenshot
```
aio browser screenshot [-o <file>] [--full] [--format png|jpeg]
```

### click
```
aio browser click "<selector>" [--index <n>] [--button left|right|middle]
```

### fill
```
aio browser fill "<text>" -s "<selector>" [--index <n>]
```

### type
```
aio browser type "<text>" [--delay <ms>]
```

### press / hotkey
```
aio browser press <key>
aio browser hotkey <key1> <key2> [<key3>...]
```

### scroll
```
aio browser scroll [--dir up|down] [--amt <pixels>]
```

### wait
```
aio browser wait load
aio browser wait network_idle
aio browser wait selector --selector "<css>" [--state visible|hidden] [--timeout <s>]
aio browser wait url --url "<pattern>"
aio browser wait timeout --timeout <s>
aio browser wait function --expression "<js>"
```

### Content extraction
```
aio browser text
aio browser html [--outer]
aio browser markdown
aio browser evaluate "<js>"
aio browser console [--clear]
aio browser snapshot
```

### Tabs
```
aio browser tabs
aio browser tab-new [url]
aio browser tab-close <index>
```

### Lifecycle
```
aio browser restart [--mode soft|hard]
```
