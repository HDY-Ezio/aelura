# Aelura MCP Server

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 服务端实现，让 AI Agent 直接调用 Aelura 的网页抓取能力。

## 功能

| Tool | 说明 |
|------|------|
| `scrape_page` | 抓取单个网页内容 |
| `bulk_scrape` | 批量抓取多个网页 |
| `scrape_with_browser` | 浏览器模式抓取（JS 渲染） |
| `extract_data` | CSS 选择器结构化提取 |
| `extract_links` | 提取页面所有链接 |
| `discover_sitemap` | 发现网站所有页面 |
| `refresh_proxies` | 刷新免费代理池 |
| `scrape_with_cookies` | 使用登录态 Cookie 抓取 |

## 安装

```bash
pip install fastmcp
```

## 使用

### stdio 模式（推荐，用于 Claude Desktop / Cursor）

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

### HTTP/SSE 模式

```bash
python server.py --http --host 0.0.0.0 --port 8000
```

客户端配置：
```json
{
  "mcpServers": {
    "aelura": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

## Claude Desktop 配置

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）：

```json
{
  "mcpServers": {
    "aelura": {
      "command": "python",
      "args": ["/absolute/path/to/aelura-mcp/server.py"]
    }
  }
}
```

重启 Claude Desktop 后，即可在对话中直接使用网页抓取能力。
