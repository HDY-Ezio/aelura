#!/usr/bin/env python3
"""
Aelura MCP Server - 基于 FastMCP 的 MCP 服务端实现
让 AI Agent（Claude Desktop、Cursor 等）直接调用 Aelura 的网页抓取能力。

支持 stdio 和 HTTP/SSE 两种传输模式。
"""

import asyncio
import json
import subprocess
import sys
import os
from pathlib import Path

from fastmcp import FastMCP

# 初始化 MCP Server
mcp = FastMCP(
    "aelura",
    instructions=(
        "Aelura (黑豹跳蛛) 是一个零付费网页抓取框架。"
        "支持6级智能降级、自动代理切换、指纹轮换和行为模拟。"
    ),
)

# 脚本路径
SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
WEB_SCRAPE_PY = SCRIPT_DIR / "web_scrape.py"


def _run_scrape(args: list, timeout: int = 120) -> dict:
    """调用 web_scrape.py 并返回结果"""
    cmd = [sys.executable, str(WEB_SCRAPE_PY)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(SCRIPT_DIR),
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "抓取失败"}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"content": result.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": f"请求超时 ({timeout}s)"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def scrape_page(url: str, mode: str = "auto", output: str = "text") -> str:
    """抓取单个网页内容。

    Args:
        url: 目标网页 URL
        mode: 抓取模式 (auto/tls/http/browser)，默认 auto 自动降级
        output: 输出格式 (text/json/markdown)
    """
    args = ["--url", url, "--mode", mode, "--output", output]
    result = _run_scrape(args)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def bulk_scrape(urls: str, mode: str = "auto", delay: float = 2.0) -> str:
    """批量抓取多个网页。

    Args:
        urls: 多个 URL，用换行符分隔
        mode: 抓取模式 (auto/tls/http/browser)
        delay: 请求间隔秒数
    """
    import tempfile
    url_list = [u.strip() for u in urls.strip().split("\n") if u.strip()]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as f:
        f.write("\n".join(url_list))
        tmp_path = f.name

    try:
        args = [
            "--url-file", tmp_path,
            "--mode", mode,
            "--delay", str(delay),
            "--output", "json",
        ]
        result = _run_scrape(args, timeout=300)
        return json.dumps(result, ensure_ascii=False, indent=2)
    finally:
        os.unlink(tmp_path)


@mcp.tool()
def scrape_with_browser(url: str, output: str = "text") -> str:
    """使用浏览器模式抓取（支持 JS 渲染）。

    Args:
        url: 目标网页 URL
        output: 输出格式 (text/json/markdown)
    """
    args = ["--url", url, "--mode", "browser", "--output", output]
    result = _run_scrape(args, timeout=60)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def extract_data(url: str, selector: str, fields: str = "") -> str:
    """从网页中提取结构化数据。

    Args:
        url: 目标网页 URL
        selector: CSS 选择器，如 ".product-list li"
        fields: 提取字段（逗号分隔），如 "title,price,link"
    """
    args = ["--url", url, "--selector", selector, "--output", "json"]
    if fields:
        args += ["--fields", fields]
    result = _run_scrape(args)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def extract_links(url: str, base_only: bool = True) -> str:
    """提取网页中的所有链接。

    Args:
        url: 目标网页 URL
        base_only: 是否只保留同域名下的链接
    """
    args = [
        "--url", url,
        "--selector", "a[href]",
        "--fields", "href",
        "--output", "json",
    ]
    result = _run_scrape(args)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def discover_sitemap(url: str) -> str:
    """发现网站的所有页面 URL（通过 sitemap.xml）。

    Args:
        url: 目标网站根 URL
    """
    args = ["--url", url, "--discover-sitemap", "--output", "json"]
    result = _run_scrape(args)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def refresh_proxies() -> str:
    """刷新免费代理池（抓取新代理并检测存活）。"""
    args = ["--refresh-proxies"]
    result = _run_scrape(args)
    return "代理池已刷新"


@mcp.tool()
def scrape_with_cookies(url: str, cookies_json: str, output: str = "text") -> str:
    """使用登录态 Cookie 抓取网页。

    Args:
        url: 目标网页 URL
        cookies_json: Cookie 的 JSON 字符串（键值对格式或浏览器导出格式）
        output: 输出格式 (text/json/markdown)
    """
    import tempfile
    cookies_data = json.loads(cookies_json)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(cookies_data, f)
        tmp_path = f.name

    try:
        args = [
            "--url", url,
            "--cookies", tmp_path,
            "--output", output,
        ]
        result = _run_scrape(args)
        return json.dumps(result, ensure_ascii=False, indent=2)
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    # 默认 stdio 模式；传 --http 则启动 HTTP/SSE 服务
    if "--http" in sys.argv:
        host = "0.0.0.0"
        port = 8000
        for i, arg in enumerate(sys.argv):
            if arg == "--host" and i + 1 < len(sys.argv):
                host = sys.argv[i + 1]
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        mcp.run(transport="sse")
    else:
        mcp.run()
