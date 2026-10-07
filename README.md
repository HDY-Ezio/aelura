<div align="center">

# 🐆 Aelura · 黑豹跳蛛

### 轻量级反爬网页抓取框架

**零付费 API · 6 级智能降级 · 自动代理切换 · MCP 集成**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Size](https://img.shields.io/badge/Code-2.4K%20LOC-blue)](scripts/)

**不花一分钱 API，用纯技术手段解决 99% 的网页抓取场景**

[安装](#-安装) · [快速开始](#-快速开始) · [API 文档](#-api) · [架构](#-架构) · [竞品对比](#-竞品对比)

</div>

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **零付费** | 不依赖任何付费 API，纯 Python + 免费代理源 |
| **6 级智能降级** | 静态→TLS伪装→HTTP伪装→会话预热→浏览器→代理重试，自动降级 |
| **连接池** | curl_cffi Session 复用 TCP+TLS，同域名请求节省 2-3 次 RTT |
| **指纹轮换** | 50+ 真实浏览器指纹 + 预生成指纹池 O(1) 轮转 |
| **行为模拟** | 贝塞尔鼠标轨迹 + 不均匀滚动 + 打字节奏 |
| **免费代理池** | 3 个免费源 + 并发检测 + 自动淘汰 |
| **Cookie 注入** | 支持浏览器导出格式，保持登录态 |
| **SQLite 缓存** | WAL 模式 + gzip 压缩存储，体积减少 70-80% |
| **MCP Server** | AI Agent（Claude/Cursor/Coze）一行配置接入 |
| **错误短路** | DNS/SSL 不可恢复错误直接跳过后续层级，不浪费时间 |

## 📦 安装

### 方式一：从源码安装

```bash
git clone https://github.com/HDY-Ezio/aelura.git
cd aelura
pip install -r requirements.txt
```

### 方式二：安装依赖（按需）

```bash
# 核心依赖（HTTP 抓取）
pip install scrapling

# TLS 指纹伪装（推荐）
pip install curl-cffi

# 浏览器模式
pip install playwright
playwright install chromium

# HTML 解析加速（可选，快 5-10x）
pip install selectolax

# MCP Server（AI Agent 集成）
pip install mcp
```

> **最小安装**：仅需 `pip install scrapling` 即可运行 L3 HTTP 抓取。其他依赖按需安装，缺少时自动降级。

## 🚀 快速开始

### CLI 使用

```bash
# 单页面抓取（自动降级）
python scripts/web_scrape.py --url https://example.com

# 指定模式
python scripts/web_scrape.py --url https://spa-site.com --mode browser

# 批量抓取
python scripts/web_scrape.py --url-file urls.txt --delay 5

# 带 Cookie 登录态
python scripts/web_scrape.py --url https://example.com --cookies cookies.json

# 结构化提取
python scripts/web_scrape.py --url https://example.com --selector ".product" --fields "title,price"

# 刷新代理池（并发检测）
python scripts/web_scrape.py --refresh-proxies
```

### Python API

```python
from scripts.web_scrape import smart_fetch, fetch_tls, fetch_browser

# 1. 自动降级（推荐）
page, error = smart_fetch("https://example.com")
if page:
    print(page.get_all_text()[:500])  # 提取文本
    print(page.css(".title"))          # CSS 选择器

# 2. 指定模式
page, error = smart_fetch("https://spa-site.com", mode="browser")
page, error = smart_fetch("https://api-site.com", mode="curl")  # TLS 指纹

# 3. 直接调用底层
resp = fetch_tls("https://example.com")
if resp.status == 200:
    print(resp.get_all_text())

# 4. 批量抓取
from scripts.web_scrape import async_bulk_scrape
import asyncio

results = asyncio.run(async_bulk_scrape(
    urls=["https://example.com/1", "https://example.com/2"],
    max_concurrent=5,
))
for r in results:
    print(f"{r['url']}: {r['status']} ({r['elapsed_seconds']}s)")

# 5. 带 Cookie
page, error = smart_fetch(
    "https://members.example.com",
    cookies='[{"name":"session","value":"abc123","domain":".example.com"}]',
)

# 6. 使用代理
page, error = smart_fetch("https://example.com", proxy="http://proxy:8080")
```

## 📖 API 文档

### `smart_fetch(url, mode, ...)` → `(_BaseResponse, str)`

主入口函数。根据模式选择抓取策略，失败时自动降级。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `url` | `str` | — | 目标 URL |
| `mode` | `str` | `"auto"` | 模式：`auto`/`curl`/`http`/`browser`/`stealth`/`web_fetch` |
| `proxy` | `str` | `None` | 代理地址（`host:port` 或完整 URL） |
| `headless` | `bool` | `True` | 浏览器无头模式 |
| `timeout` | `float` | `15.0` | 超时时间（秒） |
| `cookies` | `str` | `""` | Cookie JSON 字符串 |
| `method` | `str` | `"GET"` | HTTP 方法 |
| `data` | `str` | `None` | POST 请求体 |
| `warmup` | `bool` | `False` | 启用会话预热（先访问首页） |
| `screenshot_path` | `str` | `""` | 浏览器截图保存路径 |

**返回**：`(page, error)` 元组。成功时 `page` 为 `_BaseResponse` 对象，`error` 为空字符串；失败时 `page` 为 `None`。

### `_BaseResponse`

统一响应基类。

| 属性/方法 | 类型 | 说明 |
|-----------|------|------|
| `.html_content` | `str` | 原始 HTML |
| `.status` | `int` | HTTP 状态码 |
| `.url` | `str` | 最终 URL（重定向后） |
| `.get_all_text()` | `str` | 提取全部可见文本 |
| `.css(selector)` | `List[str]` | CSS 选择器提取 |

### `async_bulk_scrape(urls, ...)` → `List[Dict]`

异步批量抓取。

```python
results = await async_bulk_scrape(
    urls=["https://example.com/1", "https://example.com/2"],
    max_concurrent=5,
    mode="auto",
)
# 返回: [{"url": ..., "status": 200, "text": ..., "elapsed_seconds": 1.2, "success": True}, ...]
```

### 底层函数

| 函数 | 说明 |
|------|------|
| `fetch_tls(url, fp, proxy, ...)` | L2：curl_cffi TLS 指纹模拟（Session 连接池复用） |
| `fetch_http(url, fp, proxy, ...)` | L3：Scrapling HTTP 请求 |
| `fetch_stealth(url, fp, proxy, ...)` | L4：会话预热（先首页再目标） |
| `fetch_browser(url, fp, proxy, ...)` | L5：Playwright 浏览器（指纹注入 + 行为模拟） |

## 🔗 6 级智能降级链

核心设计理念：**每一层都可能在上一层失败时接管，确保最终拿到数据。**

```
请求 → L1 web_fetch (静态页面)
        ↓ 失败
       L2 curl_cffi (TLS 指纹伪装 + Session 连接池)
        ↓ 失败
       L3 Scrapling HTTP (完整 HTTP 伪装)
        ↓ 失败
       L4 会话预热 (先首页再目标，绕过 Session 校验)
        ↓ 失败
       L5 Playwright (JS 渲染 + 指纹轮换 + 行为模拟)
        ↓ 全部失败
       自动代理重试 (L2→L5 全链路代理切换)
```

**错误短路优化**：如果 DNS 解析失败、SSL 错误或 SSRF 拦截，直接跳过后续层级（因为网络层问题不会因切换层级而解决）。

| 层级 | 方案 | 适用场景 | 反爬能力 |
|------|------|----------|----------|
| L1 | web_fetch | 简单静态页面 | ★☆☆☆☆ |
| L2 | curl_cffi TLS | Chrome TLS 指纹 + 连接池复用 | ★★★☆☆ |
| L3 | Scrapling | 自适应 HTTP 伪装 | ★★★☆☆ |
| L4 | 会话预热 | Cookie/Session 校验 | ★★★★☆ |
| L5 | Playwright | JS 渲染 + 行为模拟 | ★★★★★ |
| 代理 | 免费代理池 | IP 被封 | ★★★★★ |

## 🧠 指纹引擎

- **50+ UA**：覆盖 Chrome / Firefox / Edge / Android 真机
- **地理一致性**：时区 / 语言 / 平台三元素绑定
- **指纹池**：预生成 50 套指纹，`FingerprintPool` O(1) 轮转，避免每次请求 SHA-256 去重
- **真实性**：`hardware_concurrency` 只用 2 的幂 `[2,4,8,16]`，`color_depth` 固定 24

## 🎭 行为模拟器

模拟真人浏览行为，降低被检测概率：

- **鼠标轨迹**：贝塞尔曲线 + 随机扰动
- **滚动曲线**：不均匀速度 + 偶尔回滚 + 末端减速
- **打字节奏**：不均匀延迟 + 偶尔停顿
- **请求节奏**：随机间隔 5-25s

## 🌐 免费代理池

自动从 3 个免费源抓取、**并发检测**（5 worker）、自动淘汰：

| 数据源 | 特点 |
|--------|------|
| free-proxy-list.net | 社区维护，更新快 |
| proxyscrape.com | API 直连，量大 |
| geonode.com | 质量较高，有国家标注 |

500 个代理并发检测从 ~100s 降至 ~20s。

## 🛡️ 增强模块

| 模块 | 功能 |
|------|------|
| 域名限速 | 每个域名独立限速 5-30s，遵守 robots.txt |
| 增量抓取 | ETag / Last-Modified 检测，只抓更新的页面 |
| 智能重试 | 503/429/timeout 自动指数退避 |
| Sitemap 发现 | 自动发现 sitemap.xml，支持递归子 sitemap |
| 本地缓存 | SQLite WAL + gzip 压缩，体积减少 70-80% |
| robots.txt | 自动解析并遵守爬取规则 |

## 🔌 MCP Server

让 AI Agent（Claude Desktop、Cursor、Coze）直接调用 Aelura：

```json
{
  "mcpServers": {
    "aelura": {
      "command": "python",
      "args": ["/path/to/aelura-mcp/server.py"]
    }
  }
}
```

8 个 MCP Tools：

| Tool | 说明 |
|------|------|
| `scrape_page` | 智能抓取（自动降级） |
| `bulk_scrape` | 批量抓取 |
| `scrape_with_browser` | 浏览器渲染 |
| `extract_data` | 结构化数据提取 |
| `extract_links` | 链接发现 |
| `discover_sitemap` | Sitemap 发现 |
| `refresh_proxies` | 刷新代理池 |
| `scrape_with_cookies` | 带登录态抓取 |

详见 [aelura-mcp/README.md](aelura-mcp/README.md)

## 🏗️ 架构

```
aelura/
├── scripts/
│   ├── web_scrape.py          # 主入口：6级降级链 + Session连接池 + 上下文池
│   ├── fingerprint_engine.py  # 指纹引擎：50+ UA + 指纹池 + SHA-256 去重
│   ├── behavior_simulator.py  # 行为模拟：贝塞尔鼠标 + 滚动 + 打字
│   ├── free_proxy_pool.py     # 代理池：3源抓取 + 并发检测 + 自动淘汰
│   ├── enhancer.py            # 增强模块：限速/缓存/重试/Sitemap/robots
│   └── utils.py               # 公共工具：SSRF防护/配置/SQLite/资源管理
├── aelura-mcp/
│   └── server.py              # MCP Server：8 tools
└── requirements.txt
```

**关键设计决策**：

- **Session 连接池**：`_SessionManager` 按域名缓存 curl_cffi Session，复用 TCP+TLS
- **WAL 模式 SQLite**：读写并发不互斥，批量场景性能提升 3-5x
- **错误短路**：DNS/SSL/SSRF 错误直接跳过后续降级层级
- **gzip 缓存**：压缩存储 HTML，兼容旧数据自动解压
- **指纹池**：预生成 + 轮转，O(1) 取用
- **上下文池**：3 个 Playwright context 轮转复用
- **atexit 清理**：`register_cleanup()` 自动关闭浏览器和数据库连接

## 📊 竞品对比

| 特性 | Aelura | ScraperAPI | ScrapingBee | Crawlee | Scrapling |
|------|--------|-----------|-------------|---------|-----------|
| 费用 | **免费** | $29/月起 | $49/月起 | 免费 | 免费 |
| 降级链 | **6 级** | 代理池 | JS 渲染 | 自定义 | 单级 |
| 代理 | **免费自动** | 付费 | 付费 | 需自备 | 需自备 |
| 指纹池 | **50+ 预生成** | 有 | 有 | 无 | 有 |
| 行为模拟 | **✅** | ❌ | ❌ | ❌ | ❌ |
| Session 池 | **✅** | ❌ | ❌ | ❌ | ❌ |
| Cookie | **✅** | ✅ | ✅ | ✅ | ✅ |
| MCP | **✅** | ❌ | ❌ | ❌ | ❌ |
| 部署 | **纯 Python** | SaaS | SaaS | Node.js | Python |

## 📈 性能优化 (v8.0)

| 优化项 | 效果 |
|--------|------|
| SQLite WAL 模式 | 批量写入 3-5x |
| Session 连接池 | 同域名节省 2-3 次 RTT |
| 降级链错误短路 | 无效降级节省 50-80% 时间 |
| 代理池并发检测 | 500 代理 100s → 20s |
| selectolax HTML 解析 | 大页面 5-10x（可选） |
| 指纹池 O(1) 轮转 | 消除批量场景指纹计算开销 |
| gzip 缓存压缩 | 磁盘占用 -70-80% |

## 🤝 参与贡献

1. Fork 本项目
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 发起 Pull Request

## 📄 License

MIT License - 详见 [LICENSE](LICENSE)

---

<div align="center">

**Aelura** — 像黑豹跳蛛一样精准、敏捷、零成本地捕获目标

by [煋旺智能](https://github.com/xingwangzhineng)

</div>
