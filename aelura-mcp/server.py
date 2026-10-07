#!/usr/bin/env python3
"""
Aelura MCP Server - 基于 FastMCP 的 MCP 服务端实现
让 AI Agent（Claude Desktop、Cursor 等）直接调用 Aelura 的网页抓取能力。

v2.0 改进：
  - 直接 import web_scrape 模块函数，不再使用 subprocess 调用
  - 增强错误处理与参数校验
  - 支持 POST 请求、截图等新功能

支持 stdio 和 HTTP/SSE 两种传输模式。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

# 将 scripts 目录加入 sys.path，以便直接 import
SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# 安全导入核心模块（缺失依赖时优雅降级，不阻塞 MCP Server 启动）
try:
    from web_scrape import (
        smart_fetch,
        async_bulk_scrape,
        fetch_browser,
        get_degradation_stats,
        _BrowserManager,
    )
    _import_error = None
except ImportError as e:
    _import_error = str(e)
    smart_fetch = None
    async_bulk_scrape = None
    fetch_browser = None
    get_degradation_stats = None
    _BrowserManager = None

try:
    from enhancer import discover_sitemap as _discover_sitemap, ScrapingEnhancer
except ImportError:
    _discover_sitemap = None
    ScrapingEnhancer = None

try:
    from free_proxy_pool import refresh_proxy_pool, get_available_proxies
except ImportError:
    refresh_proxy_pool = None
    get_available_proxies = None

# 日志配置
logger = logging.getLogger("aelura.mcp")

# 初始化 MCP Server
mcp = FastMCP(
    "aelura",
    instructions=(
        "Aelura (黑豹跳蛛) 是一个零付费网页抓取框架。"
        "支持6级智能降级、自动代理切换、指纹轮换和行为模拟。"
    ),
)


# ============================================================
# 辅助函数
# ============================================================
def _check_imports() -> Optional[str]:
    """检查核心模块是否导入成功。返回 None 表示正常，否则返回错误信息。"""
    if _import_error:
        return f"核心模块导入失败: {_import_error}"
    if smart_fetch is None:
        return "web_scrape 模块未加载，请检查依赖是否安装"
    return None


def _format_result(result) -> dict:
    """将 _BaseResponse 格式化为 MCP 返回字典。"""
    if not result or result.status == 0:
        return {"error": "抓取失败，所有降级层级均未成功", "status": 0}

    text = result.get_all_text()
    return {
        "status": result.status,
        "url": result.url or "",
        "text": text,
        "html_length": len(result.html_content),
        "text_length": len(text),
        "success": bool(result.status < 400),
    }


def _safe_json_dumps(obj: object) -> str:
    """安全的 JSON 序列化，处理异常。"""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError) as e:
        return json.dumps({"error": f"JSON 序列化失败: {e}"}, ensure_ascii=False)


# ============================================================
# MCP Tools
# ============================================================
@mcp.tool()
def scrape_page(url: str, mode: str = "auto", output: str = "text",
                proxy: bool = False, warmup: bool = False,
                timeout: float = 15.0) -> str:
    """抓取单个网页内容。

    Args:
        url: 目标网页 URL
        mode: 抓取模式 (auto/tls/http/stealth/browser)，默认 auto 自动降级
        output: 输出格式 (text/json)，默认 text
        proxy: 是否启用代理池，默认 false
        warmup: 是否启用会话预热，默认 false
        timeout: 请求超时秒数，默认 15
    """
    try:
        result = smart_fetch(
            url, mode=mode, proxy_enabled=proxy,
            warmup=warmup, timeout=timeout,
        )
        data = _format_result(result)
        if output == "text" and data.get("success"):
            return data.get("text", "")
        return _safe_json_dumps(data)
    except Exception as e:
        logger.error("[MCP] scrape_page 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "url": url})


@mcp.tool()
def bulk_scrape(urls: str, mode: str = "auto",
                max_concurrent: int = 5) -> str:
    """批量抓取多个网页（异步并发）。

    Args:
        urls: 多个 URL，用换行符或逗号分隔
        mode: 抓取模式 (auto/tls/http/stealth/browser)
        max_concurrent: 最大并发数，默认 5
    """
    url_list = [u.strip() for u in urls.replace(",", "\n").strip().split("\n") if u.strip()]
    if not url_list:
        return _safe_json_dumps({"error": "未提供有效 URL"})
    if len(url_list) > 50:
        return _safe_json_dumps({"error": f"URL 数量过多 ({len(url_list)})，单次最多 50 个"})

    try:
        results = asyncio.run(async_bulk_scrape(
            url_list, max_concurrent=max_concurrent, mode=mode,
        ))
        return _safe_json_dumps(results)
    except Exception as e:
        logger.error("[MCP] bulk_scrape 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e)})


@mcp.tool()
def scrape_with_browser(url: str, headless: bool = True,
                        screenshot: bool = False,
                        timeout: float = 30.0) -> str:
    """使用浏览器模式抓取（支持 JS 渲染、行为模拟）。

    Args:
        url: 目标网页 URL
        headless: 是否无头模式，默认 true
        screenshot: 是否截图，默认 false
        timeout: 超时秒数，默认 30
    """
    try:
        screenshot_path = None
        if screenshot:
            import tempfile
            screenshot_path = str(Path(tempfile.gettempdir()) / f"aelura_screenshot_{hash(url)}.png")

        result = fetch_browser(
            url, headless=headless,
            timeout=timeout * 1000,  # 转为毫秒
            screenshot_path=screenshot_path,
        )
        data = _format_result(result)
        if screenshot_path and screenshot:
            data["screenshot_path"] = screenshot_path
        return _safe_json_dumps(data)
    except Exception as e:
        logger.error("[MCP] scrape_with_browser 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "url": url})


@mcp.tool()
def extract_data(url: str, selector: str, fields: str = "") -> str:
    """从网页中提取结构化数据（使用 CSS 选择器）。

    Args:
        url: 目标网页 URL
        selector: CSS 选择器，如 ".product-list li"
        fields: 提取字段（逗号分隔），如 "title,price,link"
    """
    try:
        result = smart_fetch(url, mode="auto")
        if result.status == 0:
            return _safe_json_dumps({"error": "页面抓取失败", "url": url})

        matches = result.css(selector)
        data = {
            "url": url,
            "selector": selector,
            "matches": matches,
            "match_count": len(matches),
        }

        if fields:
            # 按字段过滤（简化实现）
            field_list = [f.strip() for f in fields.split(",")]
            data["requested_fields"] = field_list

        return _safe_json_dumps(data)
    except Exception as e:
        logger.error("[MCP] extract_data 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "url": url})


@mcp.tool()
def extract_links(url: str, same_domain: bool = True) -> str:
    """提取网页中的所有链接。

    Args:
        url: 目标网页 URL
        same_domain: 是否只保留同域名链接，默认 true
    """
    try:
        result = smart_fetch(url, mode="auto")
        if result.status == 0:
            return _safe_json_dumps({"error": "页面抓取失败", "url": url})

        import re
        from urllib.parse import urlparse, urljoin

        links = re.findall(r'href=["\']([^"\']+)["\']', result.html_content)
        parsed_base = urlparse(url)
        base_domain = parsed_base.netloc

        # 转为绝对 URL 并去重
        absolute_links: list = []
        seen: set = set()
        for link in links:
            abs_link = urljoin(url, link)
            if abs_link in seen:
                continue
            seen.add(abs_link)
            if same_domain:
                parsed = urlparse(abs_link)
                if parsed.netloc != base_domain:
                    continue
            absolute_links.append(abs_link)

        return _safe_json_dumps({
            "url": url,
            "links": absolute_links,
            "total": len(absolute_links),
        })
    except Exception as e:
        logger.error("[MCP] extract_links 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "url": url})


@mcp.tool()
def discover_sitemap(domain: str, max_depth: int = 3) -> str:
    """发现网站的所有页面 URL（通过 sitemap.xml 和 robots.txt）。

    Args:
        domain: 目标网站根 URL，如 https://example.com
        max_depth: 最大递归深度，默认 3
    """
    try:
        urls = _discover_sitemap(domain, depth=0)
        return _safe_json_dumps({
            "domain": domain,
            "urls": urls[:500],  # 限制返回数量
            "total": len(urls),
        })
    except Exception as e:
        logger.error("[MCP] discover_sitemap 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "domain": domain})


@mcp.tool()
def refresh_proxies() -> str:
    """刷新免费代理池（抓取新代理并检测存活）。"""
    try:
        alive = refresh_proxy_pool()
        return _safe_json_dumps({
            "status": "ok",
            "alive_proxies": alive,
            "message": f"代理池已刷新，存活代理: {alive}",
        })
    except Exception as e:
        logger.error("[MCP] refresh_proxies 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e)})


@mcp.tool()
def scrape_with_post(url: str, data: str,
                     content_type: str = "application/x-www-form-urlencoded",
                     mode: str = "auto") -> str:
    """使用 POST 方法抓取网页或 API。

    Args:
        url: 目标 URL
        data: POST 请求体数据
        content_type: Content-Type，默认 application/x-www-form-urlencoded
        mode: 抓取模式
    """
    try:
        result = smart_fetch(
            url, mode=mode, method="POST", data=data,
        )
        data_dict = _format_result(result)
        return _safe_json_dumps(data_dict)
    except Exception as e:
        logger.error("[MCP] scrape_with_post 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "url": url})


@mcp.tool()
def scrape_with_cookies(url: str, cookies_json: str,
                        output: str = "text") -> str:
    """使用登录态 Cookie 抓取网页。

    Args:
        url: 目标网页 URL
        cookies_json: Cookie 的 JSON 字符串（键值对格式或浏览器导出格式）
        output: 输出格式 (text/json)
    """
    try:
        cookies_data = json.loads(cookies_json)
        # 兼容浏览器导出格式
        if isinstance(cookies_data, list):
            cookies = {}
            for item in cookies_data:
                name = item.get("name", "")
                value = item.get("value", "")
                if name and value:
                    cookies[name] = value
        elif isinstance(cookies_data, dict):
            cookies = cookies_data
        else:
            return _safe_json_dumps({"error": "Cookie 格式不正确"})

        result = smart_fetch(url, cookies=cookies)
        data = _format_result(result)
        if output == "text" and data.get("success"):
            return data.get("text", "")
        return _safe_json_dumps(data)
    except json.JSONDecodeError:
        return _safe_json_dumps({"error": "Cookie JSON 格式无效"})
    except Exception as e:
        logger.error("[MCP] scrape_with_cookies 异常: %s", e, exc_info=True)
        return _safe_json_dumps({"error": str(e), "url": url})


@mcp.tool()
def get_degradation_stats_tool() -> str:
    """获取当前会话的降级统计数据。"""
    try:
        stats = get_degradation_stats()
        return _safe_json_dumps({
            "degradation_stats": stats,
            "total_events": sum(stats.values()) if stats else 0,
        })
    except Exception as e:
        return _safe_json_dumps({"error": str(e)})


# ============================================================
# 入口
# ============================================================
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
        logger.info("启动 HTTP/SSE 模式 %s:%d", host, port)
        mcp.run(transport="sse")
    else:
        logger.info("启动 stdio 模式")
        mcp.run()
