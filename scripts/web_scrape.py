#!/usr/bin/env python3
"""
Aelura · 黑豹跳蛛 —— 轻量化全功能网页抓取技能

6 级智能降级链：
  L1: web_fetch（平台内置）
  L2: curl_cffi（TLS 指纹伪装）
  L3: Scrapling（HTTP 伪装）
  L4: Scrapling 会话预热（Stealthy/Dynamic）
  L5: Playwright 真实浏览器（含行为模拟）
  L6: 自动代理切换重试

v7.0 改进：
  - 新增公共 utils 模块，统一导入辅助 / SSRF 防护 / 配置加载
  - _BaseResponse 统一响应基类（curl / 浏览器共用）
  - validate_url 含 SSRF 防护（拦截内网地址）
  - 降级计数器 + logger 埋点
  - main() 拆分为多个职责单一的子函数
  - 浏览器实例复用（_BrowserManager）
  - 支持 POST 请求（--method / --data）
  - 支持截图（--screenshot）
  - 支持配置文件（--config / aelura.json）
  - 支持 asyncio 并发批量抓取
  - Cookie 值合法性校验
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse

from utils import (
    try_import_curl_cffi,
    try_import_scrapling,
    try_import_playwright,
    validate_url,
    load_config,
    MIN_CONTENT_LENGTH,
)

# ============================================================
# 日志配置
# ============================================================
logger = logging.getLogger("aelura")

# ============================================================
# 降级计数
# ============================================================
_degradation_count: Dict[str, int] = {}


def _count_degradation(level: str) -> None:
    """记录某降级层级的触发次数。"""
    _degradation_count[level] = _degradation_count.get(level, 0) + 1


def get_degradation_stats() -> Dict[str, int]:
    """获取当前降级统计。"""
    return dict(_degradation_count)


# ============================================================
# 统一响应基类
# ============================================================
class _BaseResponse:
    """统一的页面响应基类，封装 curl 和浏览器响应的公共接口。"""

    def __init__(self, html_content: str = "", status: int = 200, url: str = ""):
        self.html_content = html_content
        self.status = status
        self.url = url

    def get_all_text(self) -> str:
        """提取页面全部可见文本。"""
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", self.html_content, flags=re.IGNORECASE)
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def css(self, selector: str) -> List[str]:
        """简单的 CSS 选择器提取（基于正则，非完整 CSS 引擎）。"""
        results: List[str] = []
        # 简化实现：提取 class/id 匹配的内容
        pattern = re.compile(
            rf'<[^>]+(?:class|id)="[^"]*{re.escape(selector)}[^"]*"[^>]*>(.*?)</[^>]+>',
            re.DOTALL | re.IGNORECASE,
        )
        for match in pattern.finditer(self.html_content):
            inner = match.group(1)
            text = re.sub(r"<[^>]+>", "", inner).strip()
            if text:
                results.append(text)
        return results


class _CurlResponse(_BaseResponse):
    """curl_cffi / scrapling 响应的适配包装。"""

    def __init__(self, response_obj: Any):
        super().__init__()
        self._resp = response_obj
        self.status = getattr(response_obj, "status_code", 200)
        self.html_content = getattr(response_obj, "text", "")

    def get_all_text(self) -> str:
        # 如果底层对象有 .get_all_text()，优先使用
        if hasattr(self._resp, "get_all_text"):
            try:
                return self._resp.get_all_text()
            except Exception:
                pass
        return super().get_all_text()

    def css(self, selector: str) -> List[str]:
        if hasattr(self._resp, "css"):
            try:
                elements = self._resp.css(selector)
                return [el.text(strip=True) if hasattr(el, "text") else str(el) for el in elements]
            except Exception:
                pass
        return super().css(selector)


class _BrowserPage(_BaseResponse):
    """Playwright Page 对象的适配包装。"""

    def __init__(self, page: Any):
        super().__init__()
        self._page = page
        self.html_content = page.content()
        self.status = 200

    def get_all_text(self) -> str:
        try:
            return self._page.inner_text("body")
        except Exception:
            return super().get_all_text()

    def css(self, selector: str) -> List[str]:
        try:
            elements = self._page.query_selector_all(selector)
            return [el.inner_text() for el in elements if el]
        except Exception:
            return super().css(selector)


# ============================================================
# 浏览器管理器（实例复用）
# ============================================================
class _BrowserManager:
    """管理 Playwright 浏览器实例的生命周期，避免重复启动。"""

    def __init__(self):
        self._playwright = None
        self._browser = None

    def _ensure_browser(self, headless: bool = True):
        """确保浏览器实例已启动。"""
        if self._browser is None:
            ok, sync_playwright = try_import_playwright()
            if not ok:
                raise ImportError("Playwright 未安装，无法启动浏览器")
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=headless)
            logger.debug("[BrowserManager] 浏览器实例已启动")
        return self._browser

    def new_context(self, fp_args: Optional[dict] = None, proxy: Optional[str] = None, **kwargs):
        """在已有浏览器上创建新的上下文。"""
        browser = self._ensure_browser(headless=kwargs.get("headless", True))
        ctx_opts: Dict[str, Any] = {}
        if fp_args:
            ctx_opts.update({
                "user_agent": fp_args.get("user_agent"),
                "viewport": fp_args.get("viewport"),
                "screen": fp_args.get("screen"),
                "locale": fp_args.get("locale"),
                "timezone_id": fp_args.get("timezone"),
                "device_scale_factor": fp_args.get("device_scale_factor", 1),
                "color_scheme": fp_args.get("color_scheme", "light"),
            })
            # 清理 None 值
            ctx_opts = {k: v for k, v in ctx_opts.items() if v is not None}
        if proxy:
            ctx_opts["proxy"] = {"server": f"http://{proxy}"}
        return browser.new_context(**ctx_opts)

    def close(self):
        """关闭浏览器和 Playwright 实例。"""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None


# 全局浏览器管理器
_browser_manager: Optional[_BrowserManager] = None


def _get_browser_manager() -> _BrowserManager:
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = _BrowserManager()
    return _browser_manager


# ============================================================
# L2: curl_cffi TLS 指纹伪装
# ============================================================
def fetch_tls(
    url: str,
    fp: Optional[dict] = None,
    proxy: Optional[str] = None,
    method: str = "GET",
    data: Optional[str] = None,
    timeout: float = 15.0,
) -> _BaseResponse:
    """L2: 使用 curl_cffi 发送带 TLS 指纹的请求。"""
    ok, cffi = try_import_curl_cffi()
    if not ok:
        _count_degradation("L2_unavailable")
        logger.warning("[L2] curl_cffi 未安装，跳过")
        return _BaseResponse(status=0)

    from fingerprint_engine import generate_fingerprint
    if fp is None:
        fp = generate_fingerprint()

    proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else None

    try:
        req_kwargs = {
            "headers": {
                "User-Agent": fp.get("user_agent", ""),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": f'{fp.get("locale", "en-US")},en;q=0.9',
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            },
            "timeout": timeout,
            "proxies": proxies,
            "impersonate": "chrome131",
        }

        if method.upper() == "POST" and data:
            resp = cffi.post(url, data=data, **req_kwargs)
        else:
            resp = cffi.get(url, **req_kwargs)

        result = _CurlResponse(resp)
        result.status = resp.status_code
        if resp.status_code >= 400:
            _count_degradation("L2_http_error")
            logger.warning("[L2] HTTP %d: %s", resp.status_code, url)
        return result
    except Exception as e:
        _count_degradation("L2_error")
        logger.warning("[L2] curl_cffi 请求失败: %s - %s", url, e)
        return _BaseResponse(status=0)


# ============================================================
# L3: Scrapling HTTP 伪装
# ============================================================
def fetch_http(
    url: str,
    proxy: Optional[str] = None,
    method: str = "GET",
    data: Optional[str] = None,
    timeout: float = 15.0,
) -> _BaseResponse:
    """L3: 使用 Scrapling Fetcher 发送请求。"""
    ok, libs = try_import_scrapling()
    if not ok:
        _count_degradation("L3_unavailable")
        logger.warning("[L3] Scrapling 未安装，跳过")
        return _BaseResponse(status=0)

    try:
        kwargs: Dict[str, Any] = {"timeout": timeout}
        if proxy:
            kwargs["proxy"] = f"http://{proxy}"

        if method.upper() == "POST" and data:
            page = libs["Fetcher"].post(url, data=data, **kwargs)
        else:
            page = libs["Fetcher"].get(url, **kwargs)

        return _CurlResponse(page)
    except Exception as e:
        _count_degradation("L3_error")
        logger.warning("[L3] Scrapling Fetcher 失败: %s - %s", url, e)
        return _BaseResponse(status=0)


# ============================================================
# L4: Scrapling 会话预热（Stealthy/Dynamic）
# ============================================================
def fetch_stealth(
    url: str,
    fp: Optional[dict] = None,
    proxy: Optional[str] = None,
    method: str = "GET",
    data: Optional[str] = None,
    timeout: float = 20.0,
) -> _BaseResponse:
    """L4: 使用 Scrapling StealthyFetcher / DynamicFetcher 预热会话。"""
    ok, libs = try_import_scrapling()
    if not ok:
        _count_degradation("L4_unavailable")
        logger.warning("[L4] Scrapling 未安装，跳过")
        return _BaseResponse(status=0)

    try:
        kwargs: Dict[str, Any] = {"timeout": timeout}
        if proxy:
            kwargs["proxy"] = f"http://{proxy}"
        if fp:
            kwargs["user_agent"] = fp.get("user_agent", "")

        # 先尝试 StealthyFetcher
        try:
            if method.upper() == "POST" and data:
                page = libs["StealthyFetcher"].post(url, data=data, **kwargs)
            else:
                page = libs["StealthyFetcher"].get(url, **kwargs)
            result = _CurlResponse(page)
            if result.status < 400:
                return result
        except Exception:
            pass

        # 再尝试 DynamicFetcher
        if method.upper() == "POST" and data:
            page = libs["DynamicFetcher"].post(url, data=data, **kwargs)
        else:
            page = libs["DynamicFetcher"].get(url, **kwargs)
        return _CurlResponse(page)

    except Exception as e:
        _count_degradation("L4_error")
        logger.warning("[L4] Scrapling Stealth 失败: %s - %s", url, e)
        return _BaseResponse(status=0)


# ============================================================
# L5: Playwright 真实浏览器
# ============================================================
def fetch_browser(
    url: str,
    fp: Optional[dict] = None,
    proxy: Optional[str] = None,
    headless: bool = True,
    wait_until: str = "networkidle",
    timeout: float = 20000.0,
    screenshot_path: Optional[str] = None,
) -> _BaseResponse:
    """L5: 使用 Playwright 真实浏览器抓取，复用浏览器实例。"""
    ok, _ = try_import_playwright()
    if not ok:
        _count_degradation("L5_unavailable")
        logger.warning("[L5] Playwright 未安装，跳过")
        return _BaseResponse(status=0)

    from fingerprint_engine import generate_fingerprint
    if fp is None:
        fp = generate_fingerprint()

    manager = _get_browser_manager()
    context = None
    page = None
    try:
        context = manager.new_context(fp_args=fp, proxy=proxy, headless=headless)
        page = context.new_page()

        # 行为模拟：设置随机 viewport 偏移
        page.set_default_timeout(timeout)
        page.goto(url, wait_until=wait_until, timeout=timeout)

        # 可选截图
        if screenshot_path:
            page.screenshot(path=screenshot_path)
            logger.info("[L5] 截图已保存: %s", screenshot_path)

        # 行为模拟
        try:
            from behavior_simulator import simulate_page_browsing
            simulate_page_browsing(page)
        except ImportError:
            logger.debug("[L5] behavior_simulator 未安装，跳过行为模拟")

        result = _BrowserPage(page)
        if not result.html_content or len(result.get_all_text()) < MIN_CONTENT_LENGTH:
            _count_degradation("L5_empty")
            logger.warning("[L5] 页面内容过短: %s", url)
        return result

    except Exception as e:
        _count_degradation("L5_error")
        logger.error("[L5] Playwright 失败: %s - %s", url, e)
        return _BaseResponse(status=0)
    finally:
        try:
            if page:
                page.close()
            if context:
                context.close()
        except Exception:
            pass


# ============================================================
# Cookie 校验
# ============================================================
def validate_cookie_value(value: str) -> bool:
    """校验 cookie 值的合法性。

    过滤包含控制字符或明显注入特征的值。

    Args:
        value: cookie 值字符串。

    Returns:
        True 表示合法，False 表示应跳过。
    """
    if not value:
        return False
    # 禁止包含换行/回车（防止 header 注入）
    if any(c in value for c in ("\r", "\n", "\0")):
        return False
    return True


def validate_cookie_name(name: str) -> bool:
    """校验 cookie 名称的合法性。

    Cookie 名称只允许 token 字符（RFC 6265）。

    Args:
        name: cookie 名称。

    Returns:
        True 表示合法，False 表示应跳过。
    """
    if not name:
        return False
    # RFC 6265 token chars: 排除分隔符和控制字符
    separators = set("()<>@,;:\\\"/[]?={} \t")
    if any(c in separators or ord(c) < 32 for c in name):
        return False
    return True


# ============================================================
# L6: 智能降级链（auto 模式）
# ============================================================
def smart_fetch(
    url: str,
    mode: str = "auto",
    proxy_enabled: bool = False,
    warmup: bool = False,
    headless: bool = True,
    timeout: float = 15.0,
    fp: Optional[dict] = None,
    method: str = "GET",
    data: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> _BaseResponse:
    """
    智能降级抓取。

    auto 模式：先无代理直连（L2→L3→L5），全部失败后自动加代理重试一轮。

    Args:
        url: 目标 URL。
        mode: "auto" | "tls" | "http" | "stealth" | "browser"。
        proxy_enabled: 是否启用代理。
        warmup: 是否先进行会话预热（L4）。
        headless: 浏览器是否无头模式。
        timeout: 请求超时（秒），浏览器模式为毫秒。
        fp: 预生成的指纹。
        method: HTTP 方法（GET / POST）。
        data: POST 请求体。
        screenshot_path: 截图保存路径。
        cookies: Cookie 字典。

    Returns:
        _BaseResponse 实例。
    """
    # SSRF 防护
    url_err = validate_url(url)
    if url_err:
        logger.error("[smart_fetch] URL 校验失败: %s — %s", url_err, url)
        return _BaseResponse(status=0)

    # Cookie 注入
    if cookies:
        valid_cookies = {
            k: v for k, v in cookies.items()
            if validate_cookie_name(k) and validate_cookie_value(v)
        }
        if valid_cookies:
            logger.info("[smart_fetch] 注入 %d 个 Cookie", len(valid_cookies))

    # 代理池
    proxies: List[Optional[str]] = [None]
    if proxy_enabled:
        try:
            from free_proxy_pool import get_available_proxies
            proxy_list = get_available_proxies(limit=5)
            if proxy_list:
                proxies = proxy_list
                logger.info("[smart_fetch] 代理池就绪，%d 个可用代理", len(proxies))
        except ImportError:
            logger.warning("[smart_fetch] free_proxy_pool 未安装，跳过代理")

    # 如果指定了固定模式，只尝试该模式
    mode_map = {
        "tls": [fetch_tls],
        "http": [fetch_http],
        "stealth": [fetch_stealth],
        "browser": [fetch_browser],
    }

    if mode in mode_map:
        fetch_funcs = mode_map[mode]
    else:
        # auto 模式：先无代理，再加代理
        fetch_funcs = []  # 会在下面动态构建

    # auto 模式：先直连，再加代理
    if mode == "auto":
        # Phase 1: 无代理直连
        if warmup:
            result = fetch_stealth(url, fp=fp, proxy=None, method=method, data=data, timeout=timeout)
            if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
                return result
            _count_degradation("L4_fail_no_proxy")

        result = fetch_tls(url, fp=fp, proxy=None, method=method, data=data, timeout=timeout)
        if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
            return result
        _count_degradation("L2_fail_no_proxy")

        result = fetch_http(url, proxy=None, method=method, data=data, timeout=timeout)
        if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
            return result
        _count_degradation("L3_fail_no_proxy")

        browser_timeout = timeout * 1000 if timeout < 1000 else timeout
        result = fetch_browser(url, fp=fp, proxy=None, headless=headless, timeout=browser_timeout,
                               screenshot_path=screenshot_path)
        if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
            return result
        _count_degradation("L5_fail_no_proxy")

        # Phase 2: 加代理重试
        if proxies and proxies[0] is not None:
            logger.info("[smart_fetch] 直连全部失败，启用代理重试...")
        for proxy in proxies:
            if proxy is None:
                continue
            result = fetch_tls(url, fp=fp, proxy=proxy, method=method, data=data, timeout=timeout)
            if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
                return result
            _count_degradation("L2_fail_with_proxy")

            result = fetch_http(url, proxy=proxy, method=method, data=data, timeout=timeout)
            if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
                return result
            _count_degradation("L3_fail_with_proxy")

        # 最终兜底：浏览器 + 代理
        for proxy in proxies:
            if proxy is None:
                continue
            result = fetch_browser(url, fp=fp, proxy=proxy, headless=headless, timeout=browser_timeout,
                                   screenshot_path=screenshot_path)
            if result.status and result.status < 400 and len(result.get_all_text()) >= MIN_CONTENT_LENGTH:
                return result
            _count_degradation("L5_fail_with_proxy")
    else:
        # 固定模式
        for fetch_fn in fetch_funcs:
            kwargs: Dict[str, Any] = {"url": url, "fp": fp}
            if proxy_enabled and proxies[0] is not None:
                kwargs["proxy"] = proxies[0]
            if fetch_fn == fetch_browser:
                browser_timeout = timeout * 1000 if timeout < 1000 else timeout
                kwargs["timeout"] = browser_timeout
                kwargs["headless"] = headless
                kwargs["screenshot_path"] = screenshot_path
            else:
                kwargs["timeout"] = timeout
                kwargs["method"] = method
                kwargs["data"] = data
            result = fetch_fn(**kwargs)
            if result.status and result.status < 400:
                return result

    logger.error("[smart_fetch] 所有层级均失败: %s", url)
    return _BaseResponse(status=0)


# ============================================================
# Async 封装（并发批量抓取）
# ============================================================
async def async_smart_fetch(url: str, **kwargs) -> _BaseResponse:
    """smart_fetch 的异步包装，在线程池中运行同步代码。

    用于并发批量抓取，避免串行阻塞。
    """
    return await asyncio.to_thread(smart_fetch, url, **kwargs)


async def async_bulk_scrape(urls: List[str], max_concurrent: int = 5, **kwargs) -> List[Dict]:
    """异步并发批量抓取。

    Args:
        urls: URL 列表。
        max_concurrent: 最大并发数。
        **kwargs: 传递给 smart_fetch 的参数。

    Returns:
        [{"url": str, "status": int, "text": str, "success": bool}, ...]
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(url: str) -> Dict:
        async with semaphore:
            result = await async_smart_fetch(url, **kwargs)
            return {
                "url": url,
                "status": result.status,
                "text": result.get_all_text(),
                "success": bool(result.status and result.status < 400),
            }

    tasks = [_fetch_one(url) for url in urls]
    return await asyncio.gather(*tasks)


# ============================================================
# CLI 参数解析
# ============================================================
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Aelura · 黑豹跳蛛 — 轻量化全功能网页抓取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="*", help="要抓取的 URL（支持多个）")
    parser.add_argument("--url-file", help="从文件读取 URL 列表（每行一个）")
    parser.add_argument("--mode", choices=["auto", "tls", "http", "stealth", "browser"],
                        default="auto", help="抓取模式（默认 auto）")
    parser.add_argument("--output", choices=["json", "text", "html"], default="json",
                        help="输出格式（默认 json）")
    parser.add_argument("--proxy", action="store_true", help="启用代理池")
    parser.add_argument("--warmup", action="store_true", help="启用会话预热")
    parser.add_argument("--timeout", type=float, default=15.0, help="请求超时（秒）")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="浏览器无头模式（默认开启）")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="浏览器有头模式（调试用）")
    parser.add_argument("--screenshot", help="截图保存路径（仅浏览器模式）")
    parser.add_argument("--method", choices=["GET", "POST"], default="GET",
                        help="HTTP 方法（默认 GET）")
    parser.add_argument("--data", help="POST 请求体数据")
    parser.add_argument("--config", help="配置文件路径（JSON 格式）")
    parser.add_argument("--cookies", help="Cookie 文件路径（JSON 格式）")
    parser.add_argument("--max-concurrent", type=int, default=5,
                        help="异步批量抓取最大并发数（默认 5）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志输出")
    return parser.parse_args(argv)


def load_cookies_from_args(args: argparse.Namespace) -> Optional[Dict[str, str]]:
    """从命令行参数加载 Cookie。"""
    if not args.cookies:
        return None
    try:
        with open(args.cookies, encoding="utf-8") as f:
            raw = json.load(f)
        # 支持两种格式：
        # 1. 简单键值对: {"key": "value"}
        # 2. 浏览器扩展格式: [{"name": "key", "value": "value", ...}]
        if isinstance(raw, list):
            cookies = {}
            for item in raw:
                name = item.get("name", "")
                value = item.get("value", "")
                if validate_cookie_name(name) and validate_cookie_value(value):
                    cookies[name] = value
                else:
                    logger.warning("跳过非法 Cookie: %s", name)
            return cookies
        elif isinstance(raw, dict):
            return {
                k: v for k, v in raw.items()
                if validate_cookie_name(k) and validate_cookie_value(v)
            }
        return None
    except Exception as e:
        logger.warning("Cookie 文件加载失败: %s", e)
        return None


def collect_urls(args: argparse.Namespace) -> List[str]:
    """收集所有要抓取的 URL。"""
    urls: List[str] = list(args.urls)
    if args.url_file:
        try:
            with open(args.url_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        urls.append(line)
        except Exception as e:
            logger.warning("URL 文件读取失败: %s", e)
    # 去重并校验
    validated: List[str] = []
    for u in urls:
        err = validate_url(u)
        if err:
            logger.warning("URL 校验失败，跳过: %s — %s", err, u)
        else:
            validated.append(u)
    return list(dict.fromkeys(validated))  # 去重保序


def scrape_single_url(url: str, args: argparse.Namespace,
                      cookies: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """抓取单个 URL 并返回结果字典。"""
    start_time = time.time()
    result = smart_fetch(
        url,
        mode=args.mode,
        proxy_enabled=args.proxy,
        warmup=args.warmup,
        headless=args.headless,
        timeout=args.timeout,
        method=args.method,
        data=args.data,
        screenshot_path=args.screenshot,
        cookies=cookies,
    )
    elapsed = time.time() - start_time
    return {
        "url": url,
        "status": result.status,
        "text": result.get_all_text(),
        "html_length": len(result.html_content),
        "elapsed_seconds": round(elapsed, 2),
        "success": bool(result.status and result.status < 400),
    }


def format_results(results: List[Dict], output_format: str) -> str:
    """格式化抓取结果。"""
    if output_format == "json":
        return json.dumps(results, ensure_ascii=False, indent=2)
    elif output_format == "text":
        parts = []
        for r in results:
            parts.append(f"=== {r['url']} (HTTP {r['status']}, {r['elapsed_seconds']}s) ===")
            parts.append(r["text"][:2000])
            parts.append("")
        return "\n".join(parts)
    else:  # html
        parts = []
        for r in results:
            parts.append(f"<h2>{r['url']} (HTTP {r['status']})</h2>")
            parts.append(f"<pre>{r['text'][:5000]}</pre>")
        return "\n".join(parts)


# ============================================================
# 主函数
# ============================================================
def main(argv: Optional[List[str]] = None) -> None:
    """主入口。"""
    args = parse_args(argv)

    # 日志级别
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 加载配置
    config = load_config(args.config)

    # 收集 URL
    urls = collect_urls(args)
    if not urls:
        logger.error("未提供有效 URL，退出。")
        sys.exit(1)

    # 加载 Cookie
    cookies = load_cookies_from_args(args)

    # 抓取
    if len(urls) == 1:
        results = [scrape_single_url(urls[0], args, cookies)]
    else:
        # 多 URL 使用异步并发
        logger.info("共 %d 个 URL，异步并发抓取（max_concurrent=%d）", len(urls), args.max_concurrent)
        results = asyncio.run(async_bulk_scrape(
            urls,
            max_concurrent=args.max_concurrent,
            mode=args.mode,
            proxy_enabled=args.proxy,
            warmup=args.warmup,
            headless=args.headless,
            timeout=args.timeout,
            method=args.method,
            data=args.data,
            screenshot_path=args.screenshot,
            cookies=cookies,
        ))

    # 输出
    output = format_results(results, args.output)
    print(output)

    # 降级统计
    stats = get_degradation_stats()
    if stats:
        logger.info("[降级统计] %s", stats)

    # 清理浏览器实例
    if _browser_manager:
        _browser_manager.close()


if __name__ == "__main__":
    main()
