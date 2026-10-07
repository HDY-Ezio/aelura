<div align="center">

# 🐆 Aelura · 黑豹跳蛛

### 轻量化全功能网页抓取框架

**零付费 API · 纯技术手段 · 6级智能降级 · 自动代理切换**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Quality](https://img.shields.io/badge/Code_Quality-99%2F100-brightgreen.svg)](https://xinpect.xingwangzhineng.com)

**不花一分钱 API，用纯技术手段解决 99% 的网页抓取场景**

[快速开始](#-快速开始) · [降级链原理](#-6级智能降级链) · [MCP 集成](#-mcp-集成) · [竞品对比](#-竞品对比)

</div>

---

## ✨ 为什么选择 Aelura？

| 痛点 | Aelura 的解法 |
|------|--------------|
| 付费 API 太贵（ScraperAPI $29/月起） | **零付费**，纯 Python + 免费代理 |
| 反爬严格，频繁被封 | **6 级降级链** + 指纹轮换 + 行为模拟 |
| 代理质量不稳定 | **自动代理切换**，免费代理池自动检测 |
| 需要登录态才能抓 | **Cookie 注入**，支持浏览器导出格式 |
| 集成复杂 | **MCP Server**，AI Agent 一行配置即可调用 |

## 🚀 快速开始

### 安装依赖

```bash
pip install curl-cffi scrapling playwright
playwright install chromium
```

### 基础用法

```bash
# 单个页面
python scripts/web_scrape.py --url https://example.com

# 批量抓取
python scripts/web_scrape.py --url-file urls.txt --delay 5

# 结构化提取
python scripts/web_scrape.py --url https://example.com --selector ".product" --fields "title,price"

# 使用登录态 Cookie
python scripts/web_scrape.py --url https://example.com --cookies cookies.json

# 自动发现站点所有页面
python scripts/web_scrape.py --url https://example.com --discover-sitemap

# 刷新代理池
python scripts/web_scrape.py --refresh-proxies
```

### Python 调用

```python
from scripts.web_scrape import smart_fetch

# 自动降级
page, err = smart_fetch("https://example.com")
if page:
    print(page.get_all_text()[:500])

# 浏览器模式（JS 渲染）
page, err = smart_fetch("https://spa-site.com", mode="browser")
```

## 🔗 6 级智能降级链

核心设计理念：**每一层都可能在上一层失败时接管，确保最终拿到数据。**

```
请求 → L1 web_fetch (静态)
        ↓ 失败
       L2 curl_cffi (TLS 指纹伪装)
        ↓ 失败
       L3 Scrapling HTTP (完整 HTTP 伪装)
        ↓ 失败
       L4 会话预热 (先首页再目标)
        ↓ 失败
       L5 Playwright (JS 渲染 + 指纹轮换)
        ↓ 全部失败
       自动代理重试 (L2→L5 全链路代理)
```

| 层级 | 方案 | 适用场景 | 反爬绕过能力 |
|------|------|----------|------------|
| L1 | web_fetch | 简单静态页面 | ★☆☆☆☆ |
| L2 | curl_cffi TLS | Chrome 指纹伪装 | ★★★☆☆ |
| L3 | Scrapling | 自适应解析 | ★★★☆☆ |
| L4 | 会话预热 | Cookie/Session 校验 | ★★★★☆ |
| L5 | Playwright | JS 渲染 + 行为模拟 | ★★★★★ |
| 代理 | 免费代理池 | IP 被封 | ★★★★★ |

## 🧠 指纹轮换引擎

每次请求自动更换浏览器身份，覆盖：

- **User-Agent**：50+ 真实 Chrome/Firefox/Edge/Android 指纹
- **地理一致性**：时区/语言/平台三元素绑定（不会出现"中国IP说日语"的穿帮）
- **屏幕分辨率**：20+ 真实分辨率组合
- **硬件参数**：CPU核数/内存/触屏点数

## 🎭 行为模拟器

模拟真人浏览行为，降低被检测概率：

- **鼠标轨迹**：贝塞尔曲线 + 随机扰动
- **滚动曲线**：不均匀速度 + 偶尔回滚 + 末端减速
- **打字节奏**：不均匀延迟 + 偶尔停顿
- **请求节奏**：随机间隔 5-25s，偶尔快速翻页，偶尔长时间离开

## 🌐 免费代理池

自动从 3 个免费源抓取、检测、轮换：

| 数据源 | 特点 |
|--------|------|
| free-proxy-list.net | 社区维护，更新快 |
| proxyscrape.com | API 直连，量大 |
| geonode.com | 质量较高，有国家标注 |

内置 SQLite 存储 + 存活检测 + 失败计数 + 自动淘汰。

## 🔌 MCP 集成

让 AI Agent（Claude Desktop、Cursor 等）直接调用 Aelura：

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

8 个 MCP Tools：`scrape_page` / `bulk_scrape` / `scrape_with_browser` / `extract_data` / `extract_links` / `discover_sitemap` / `refresh_proxies` / `scrape_with_cookies`

详见 [aelura-mcp/README.md](aelura-mcp/README.md)

## 🛡️ 增强模块

| 模块 | 功能 |
|------|------|
| 域名限速 | 每个域名独立限速 5-30s，符合 robots.txt 建议 |
| 增量抓取 | ETag / Last-Modified 检测，只抓更新的页面 |
| 智能重试 | 503/429/timeout 自动指数退避重试 |
| Sitemap 发现 | 自动发现 sitemap.xml，支持递归发现子 sitemap |
| 本地缓存 | SQLite 缓存 + SHA-256 内容去重 |
| robots.txt | 自动解析并遵守爬取规则 |

## 🍪 Cookie 登录态

支持两种 Cookie 格式：

```json
// 浏览器扩展导出格式（EditThisCookie 等）
[{"name": "session_id", "value": "abc123", "domain": ".example.com", "path": "/"}]

// 简单键值对格式
{"session_id": "abc123", "token": "xyz"}
```

## 📊 竞品对比

| 特性 | Aelura | ScraperAPI | ScrapingBee | Crawlee |
|------|--------|-----------|-------------|---------|
| 费用 | **免费** | $29/月起 | $49/月起 | 免费 |
| 降级链 | **6 级** | 代理池 | JS 渲染 | 自定义 |
| 代理 | **免费自动** | 付费 | 付费 | 需自备 |
| 指纹 | **50+ 轮换** | 有 | 有 | 无 |
| 行为模拟 | **✅** | ❌ | ❌ | ❌ |
| Cookie | **✅** | ✅ | ✅ | ✅ |
| MCP | **✅** | ❌ | ❌ | ❌ |
| 部署 | **纯 Python** | SaaS | SaaS | Node.js |

## 📁 项目结构

```
aelura/
├── scripts/
│   ├── web_scrape.py          # 主入口 + 降级链
│   ├── fingerprint_engine.py  # 指纹轮换引擎
│   ├── behavior_simulator.py  # 行为模拟器
│   ├── free_proxy_pool.py     # 免费代理池
│   └── enhancer.py            # 增强模块
├── aelura-mcp/
│   ├── server.py              # MCP Server
│   ├── README.md              # MCP 配置指南
│   └── requirements.txt
├── requirements.txt           # 核心依赖
├── LICENSE                    # MIT License
└── README.md
```

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
