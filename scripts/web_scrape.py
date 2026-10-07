#!/usr/bin/env python3
"""
Aelura · 黑豹跳蛛 — Web Scraping Tool v6
轻量化全功能网页抓取框架。

6级智能降级链（含自动代理重试）：
  L1: web_fetch          ← 静态页面（内置工具）
  L2: curl_cffi TLS      ← Chrome TLS指纹伪装
  L3: Scrapling HTTP     ← 完整HTTP伪装 + 自适应解析
  L4: 会话预热           ← 先首页再目标
  L5: Playwright浏览器   ← JS渲染 + 指纹轮换 + 行为模拟
  自动代理: 任一层级失败后可自动切换免费代理重试

增强模块：域名限速 / 增量抓取 / 智能重试 / Sitemap发现 / 本地缓存 / robots.txt合规

用法：
  python web_scrape.py --url URL [--mode auto] [--output json]
  python web_scrape.py --url URL --selector ".product" --fields "title,price"
  python web_scrape.py --url-file urls.txt --delay 5 --warmup
  python web_scrape.py --url URL --discover-sitemap
  python web_scrape.py --url URL --incremental
  python web_scrape.py --url URL --respect-robots
  python web_scrape.py --refresh-proxies
  python web_scrape.py --clear-cache
  python web_scrape.py --url URL --cookies cookies.json
"""

import argparse
import hashlib
import json
import logging
import random
import re
import sys
import time
from typing import Optional
from urllib.parse import urlparse

# 日志配置
logger = logging.getLogger("aelura")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# 导入核心模块
from fingerprint_engine import FingerprintEngine
from behavior_simulator import human_pause, simulate_page_browsing
from free_proxy_pool import (
    init_db as init_proxy_db,
    get_random_proxy,
    refresh_proxy_pool,
)
from enhancer import (
    ScrapingEnhancer,
    discover_sitemap,
    smart_retry,
    check_page_changed,
)

# 内容有效性的最低字符长度
MIN_CONTENT_LENGTH = 100


# ============================================================
# 公共响应包装
# ============================================================
class _BaseResponse:
    """统一的页面响应基类，为不同抓取层级提供一致接口。"""

    status: int = 200
    html_content: str = ""

    def get_all_text(self) -> str:
        """提取页面纯文本内容。"""
        text = re.sub(r'<[^>]+>', ' ', self.html_content)
        return re.sub(r'\s+', ' ', text).strip()

    def css(self, selector):
        """使用 CSS 选择器从 HTML 中提取元素列表。

        Args:
            selector: CSS 选择器字符串，如 ".product", "#title"。

        Returns:
            匹配的元素列表；Scrapling 不可用时返回空列表。
        """
        try:
            from scrapling.parser import Selector
            return Selector(self.html_content).css(selector)
        except (ImportError, Exception) as e:
            logger.debug("CSS 选择器解析失败: %s", e)
            return []

    def __str__(self):
        return self.html_content


class _CurlResponse(_BaseResponse):
    """curl_cffi 响应包装。"""

    def __init__(self, resp):
        self._resp = resp
        self.status = resp.status_code
        self.html_content = resp.text
        self._etag = resp.headers.get("ETag")
        self._last_modified = resp.headers.get("Last-Modified")


class _BrowserPage(_BaseResponse):
    """Playwright 页面包装。"""

    def __init__(self, html: str):
        self.html_content = html
        self.status = 200


# ============================================================
# 延迟导入
# ============================================================
def try_import_curl_cffi():
    """尝试导入 curl_cffi 库。

    Returns:
        tuple: (True, cffi_requests) 成功时；(False, None) 失败时。
    """
    try:
        from curl_cffi import requests as cffi_requests
        return True, cffi_requests
    except ImportError:
        return False, None


def try_import_scrapling():
    """尝试导入 Scrapling 库。

    Returns:
        tuple: (True, fetchers_dict) 成功时；(False, None) 失败时。
    """
    try:
        from scrapling.fetchers import Fetcher, StealthyFetcher, DynamicFetcher
        return True, {
            "Fetcher": Fetcher,
            "StealthyFetcher": StealthyFetcher,
            "DynamicFetcher": DynamicFetcher,
        }
    except ImportError:
        return False, None


def try_import_playwright():
    """尝试导入 Playwright 库。

    Returns:
        tuple: (True, sync_playwright) 成功时；(False, None) 失败时。
    """
    try:
        from playwright.sync_api import sync_playwright
        return True, sync_playwright
    except ImportError:
        return False, None


# 全局指纹引擎
fp_engine = FingerprintEngine()

# 降级计数器，用于监控降级发生频率
_degradation_count = {"direct_to_proxy": 0, "all_failed": 0}


# ============================================================
# URL 校验
# ============================================================
_VALID_SCHEMES = {"http", "https"}


def validate_url(url: str) -> Optional[str]:
    """校验 URL 格式合法性。

    Returns:
        None 表示合法；否则返回错误描述。
    """
    if not url or not isinstance(url, str):
        return "URL 不能为空"
    parsed = urlparse(url)
    if parsed.scheme not in _VALID_SCHEMES:
        return f"不支持的协议: {parsed.scheme}，仅允许 http/https"
    if not parsed.netloc:
        return "URL 缺少有效域名"
    return None


# ============================================================
# Cookie/Session 管理
# ============================================================
def load_cookies(cookie_path: str):
    """
    从 JSON 文件加载 Cookie。支持两种格式：

    格式1（浏览器扩展导出，如 EditThisCookie）：
    [
        {"name": "session_id", "value": "abc123", "domain": ".example.com", "path": "/"},
        {"name": "token", "value": "xyz", "domain": ".example.com", "path": "/"}
    ]

    格式2（简单键值对）：
    {"session_id": "abc123", "token": "xyz"}

    Returns:
        tuple: (simple_cookies: dict, full_cookies: list[dict])
               simple_cookies 用于 HTTP 请求，full_cookies 供 Playwright 使用。
    """
    from pathlib import Path

    path = Path(cookie_path)
    if not path.exists():
        logger.warning("Cookie 文件不存在: %s", cookie_path)
        return None, None

    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("Cookie 文件解析失败: %s", e)
        return None, None

    # 格式2：简单键值对
    if isinstance(data, dict):
        simple_cookies = data
        full_cookies = [
            {"name": k, "value": v, "domain": "", "path": "/"}
            for k, v in data.items()
        ]
        return simple_cookies, full_cookies

    # 格式1：浏览器扩展格式
    if isinstance(data, list):
        simple_cookies = {
            c["name"]: c["value"]
            for c in data
            if "name" in c and "value" in c
        }
        full_cookies = []
        for c in data:
            if "name" in c and "value" in c:
                fc = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                }
                full_cookies.append(fc)
        return simple_cookies, full_cookies

    return None, None


# ============================================================
# L2: curl_cffi TLS impersonate
# ============================================================
def fetch_tls(url: str, proxy: str = None, cookies: dict = None):
    """使用 curl_cffi 进行 TLS 指纹伪装请求。"""
    ok, cffi = try_import_curl_cffi()
    if not ok:
        return None, "curl_cffi 未安装"

    try:
        kwargs = fp_engine.generate_curl_cffi_kwargs()
        if proxy:
            kwargs["proxies"] = {
                "https": f"https://{proxy}",
                "http": f"http://{proxy}",
            }
        if cookies:
            kwargs["cookies"] = cookies
        resp = cffi.get(url, timeout=15, **kwargs)
        if (resp.status_code < 400
                and resp.text
                and len(resp.text.strip()) > MIN_CONTENT_LENGTH):
            return _CurlResponse(resp), None
        return None, f"HTTP {resp.status_code}"
    except Exception as e:
        return None, str(e)


# ============================================================
# L3: Scrapling Fetcher
# ============================================================
def fetch_http(url: str, cookies: dict = None):
    """使用 Scrapling Fetcher 进行 HTTP 请求（含自适应解析能力）。"""
    ok, libs = try_import_scrapling()
    if not ok:
        return None, "Scrapling 未安装"

    try:
        kwargs = {}
        if cookies:
            kwargs["cookies"] = cookies
        page = libs["Fetcher"].get(url, **kwargs)
        if page and getattr(page, 'status', 0) and page.status < 400:
            text = (
                page.get_all_text()
                if hasattr(page, 'get_all_text')
                else page.html_content or ""
            )
            if text and len(text.strip()) > MIN_CONTENT_LENGTH:
                return page, None
        return None, f"HTTP {getattr(page, 'status', '?')}"
    except Exception as e:
        return None, str(e)


# ============================================================
# L4: Session warmup
# ============================================================
def fetch_with_warmup(url: str, cookies: dict = None):
    """先访问首页预热会话，再访问目标页面。"""
    ok, libs = try_import_scrapling()
    if not ok:
        return None, "Scrapling 未安装"

    try:
        parsed = urlparse(url)
        homepage = f"{parsed.scheme}://{parsed.netloc}"
        Fetcher = libs["Fetcher"]
        warmup_kwargs = {}
        if cookies:
            warmup_kwargs["cookies"] = cookies
        Fetcher.get(homepage, **warmup_kwargs)
        human_pause(2, 4)
        page = Fetcher.get(url, **warmup_kwargs)
        if page and getattr(page, 'status', 0) and page.status < 400:
            text = (
                page.get_all_text()
                if hasattr(page, 'get_all_text')
                else page.html_content or ""
            )
            if text and len(text.strip()) > MIN_CONTENT_LENGTH:
                return page, None
        return None, "warmup failed"
    except Exception as e:
        return None, str(e)


# ============================================================
# L5: Playwright 浏览器 + 指纹轮换 + 行为模拟
# ============================================================
def fetch_browser(url: str, proxy: str = None, full_cookies: list = None):
    """使用 Playwright 浏览器抓取，含指纹轮换和行为模拟。"""
    ok, pw = try_import_playwright()
    if not ok:
        return None, "Playwright 未安装"

    fp_args = fp_engine.generate_playwright_args()

    try:
        with pw() as p:
            launch_opts = {"headless": True}
            if proxy:
                launch_opts["proxy"] = {"server": f"http://{proxy}"}

            browser = p.chromium.launch(**launch_opts)
            context_opts = {
                "user_agent": fp_args["user_agent"],
                "viewport": fp_args["viewport"],
                "locale": fp_args["locale"],
                "timezone_id": fp_args["timezone"],
                "screen": fp_args["screen"],
                "device_scale_factor": fp_args.get("device_scale_factor", 1),
                "color_scheme": fp_args.get("color_scheme", "light"),
            }
            context = browser.new_context(**context_opts)
            if full_cookies:
                context.add_cookies(full_cookies)

            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            simulate_page_browsing(page)

            html = page.content()
            browser.close()

            if html and len(html.strip()) > MIN_CONTENT_LENGTH:
                return _BrowserPage(html), None
            return None, "empty page"
    except Exception as e:
        return None, str(e)


# ============================================================
# 智能降级 + 智能重试
# ============================================================
def smart_fetch(
    url: str,
    mode: str = "auto",
    warmup: bool = False,
    use_proxy: bool = False,
    cookies: dict = None,
    full_cookies: list = None,
):
    """智能降级抓取。

    auto 模式下自动执行两轮尝试：
      第1轮：直连（不使用代理），依次尝试 L2→L3→L5
      第2轮：切换免费代理，依次尝试 L2→L3→L5
    任一层级成功即返回，全部失败则返回错误信息。
    """
    # URL 校验
    err = validate_url(url)
    if err:
        return None, err

    def build_levels(proxy=None):
        tag = "+代理" if proxy else ""
        levels = [
            (f"L2: TLS{tag}", lambda: fetch_tls(url, proxy, cookies=cookies)),
            (f"L3: HTTP{tag}", lambda: fetch_http(url, cookies=cookies)),
        ]
        if warmup:
            levels.insert(
                1,
                (f"L4: 预热{tag}", lambda: fetch_with_warmup(url, cookies=cookies)),
            )
        levels.append(
            (f"L5: 浏览器{tag}", lambda: fetch_browser(url, proxy, full_cookies=full_cookies))
        )
        return levels

    # 指定了固定模式，直接执行
    if mode == "tls":
        proxy = get_random_proxy(init_proxy_db()) if use_proxy else None
        return fetch_tls(url, proxy, cookies=cookies)
    elif mode == "http":
        return fetch_http(url, cookies=cookies)
    elif mode == "browser":
        proxy = get_random_proxy(init_proxy_db()) if use_proxy else None
        return fetch_browser(url, proxy, full_cookies=full_cookies)

    # auto 模式：先无代理直连，失败后自动加代理重试
    for round_name, proxy in [
        ("无代理直连", None),
        ("免费代理重试", get_random_proxy(init_proxy_db())),
    ]:
        if proxy:
            _degradation_count["direct_to_proxy"] += 1
            logger.warning(
                "[DEGRADATION] 直连全部失败，降级切换代理模式 %s (累计降级: %d)",
                proxy, _degradation_count["direct_to_proxy"]
            )
        levels = build_levels(proxy)

        for name, fn in levels:
            logger.info("尝试 %s ...", name)
            page, err = smart_retry(fn, max_retries=2)
            if page and err is None:
                logger.info("%s 成功 ✓", name)
                return page, None
            logger.warning("%s 跳过: %s", name, err)
            human_pause(1, 2)

    _degradation_count["all_failed"] += 1
    logger.error(
        "[DEGRADATION] 所有降级级别（含代理）均失败，url=%s (累计全失败: %d)",
        url, _degradation_count["all_failed"]
    )
    return None, "所有降级级别（含代理）均失败"


# ============================================================
# 数据提取 & 输出
# ============================================================
def extract_data(page, selector: str = None, fields: list = None):
    """根据 CSS 选择器和字段列表从页面提取结构化数据。"""
    if not selector:
        return (
            page.get_all_text()
            if hasattr(page, 'get_all_text')
            else page.html_content or ""
        )

    elements = page.css(selector)
    if not elements:
        return []

    results = []
    for el in elements:
        item = {}
        if fields:
            for f in fields:
                f = f.strip()
                val = (
                    el.css(f).get()
                    or el.css(f'{f}::text').get()
                    or el.css(f'{f}::attr(href)').get()
                    or ""
                )
                item[f] = val.strip()
        else:
            text = (
                el.get_all_text()
                if hasattr(el, 'get_all_text')
                else (el.text or "")
            ).strip()
            item["text"] = text
        results.append(item)
    return results


def to_markdown(page) -> str:
    """将页面内容转换为 Markdown 格式。"""
    if hasattr(page, 'markdown'):
        try:
            md = page.markdown
            if md and len(md.strip()) > MIN_CONTENT_LENGTH:
                return md
        except Exception:
            pass
    text = (
        page.get_all_text()
        if hasattr(page, 'get_all_text')
        else page.html_content or ""
    )
    return '\n\n'.join(
        line.strip() for line in text.split('\n') if line.strip()
    )


# ============================================================
# 输出格式化
# ============================================================
def format_results(all_results: list, output_format: str) -> str:
    """根据指定格式输出抓取结果。"""
    if output_format == "json":
        return json.dumps(all_results, ensure_ascii=False, indent=2)
    elif output_format == "markdown":
        parts = []
        for r in all_results:
            if "markdown" in r:
                parts.append(f"# {r['url']}\n\n{r['markdown']}\n\n---")
        return '\n'.join(parts)
    elif output_format == "text":
        parts = []
        for r in all_results:
            content = r.get("content", r.get("error", ""))
            parts.append(f"--- {r.get('url', '')} ---\n{content}")
        return '\n'.join(parts)
    elif output_format == "csv":
        import csv
        import io
        out = io.StringIO()
        writer = csv.writer(out)
        for r in all_results:
            if "data" in r:
                for item in r["data"]:
                    writer.writerow([r["url"]] + list(item.values()))
        return out.getvalue()
    return ""


# ============================================================
# 主函数
# ============================================================
def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Aelura · 黑豹跳蛛 — Web Scraping Tool v6"
    )
    parser.add_argument("--url", help="目标URL")
    parser.add_argument("--url-file", help="URL列表文件")
    parser.add_argument(
        "--mode",
        choices=["auto", "tls", "http", "browser"],
        default="auto",
    )
    parser.add_argument("--selector", help="CSS选择器")
    parser.add_argument("--fields", help="提取字段（逗号分隔）")
    parser.add_argument(
        "--output",
        choices=["text", "json", "csv", "markdown"],
        default="json",
    )
    parser.add_argument("--warmup", action="store_true", help="会话预热")
    parser.add_argument("--proxy", action="store_true", help="启用免费代理")
    parser.add_argument(
        "--refresh-proxies", action="store_true", help="刷新代理池"
    )
    parser.add_argument(
        "--discover-sitemap", action="store_true", help="自动发现站点所有页面"
    )
    parser.add_argument(
        "--incremental", action="store_true", help="增量抓取（跳过未更新页面）"
    )
    parser.add_argument(
        "--respect-robots", action="store_true", help="遵守robots.txt"
    )
    parser.add_argument("--clear-cache", action="store_true", help="清理过期缓存")
    parser.add_argument(
        "--delay", type=float, default=0, help="请求间隔（秒）"
    )
    parser.add_argument(
        "--cookies",
        help="Cookie JSON文件路径（支持浏览器导出格式或简单键值对格式）",
    )
    return parser.parse_args()


def load_cookies_from_args(args):
    """根据命令行参数加载 Cookie。"""
    if not args.cookies:
        return None, None
    simple_cookies, full_cookies = load_cookies(args.cookies)
    if simple_cookies:
        logger.info("已加载 %d 个Cookie", len(simple_cookies))
    else:
        logger.warning("Cookie加载失败，将以无登录态模式运行")
    return simple_cookies, full_cookies


def collect_urls(args) -> list:
    """收集待抓取的 URL 列表。"""
    urls = []
    if args.url:
        urls = [args.url]
        if args.discover_sitemap:
            logger.info("正在发现 %s 的所有页面...", args.url)
            discovered = discover_sitemap(args.url)
            if discovered:
                urls = discovered
                logger.info("发现 %d 个页面", len(urls))
            else:
                logger.info("未发现 sitemap，仅抓取指定URL")

    if args.url_file:
        with open(args.url_file) as f:
            urls = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
    return urls


def scrape_single_url(url: str, args, enhancer, simple_cookies, full_cookies):
    """抓取单个 URL 并返回结果字典。"""
    # 增强：抓取前检查
    check = enhancer.pre_fetch_check(url)
    if not check["allowed"]:
        logger.info("[SKIP] %s", check["reason"])
        return {"url": url, "error": check["reason"]}

    if check.get("cached") and not args.incremental:
        logger.info("[CACHE] 使用缓存")
        return {
            "url": url,
            "cached": True,
            "content": check["cache"]["content"][:10000],
        }

    # 增量抓取：检查页面是否更新
    if args.incremental:
        cached = check.get("cache")
        change_info = check_page_changed(
            url,
            cached.get("etag") if cached else None,
            cached.get("last_modified") if cached else None,
        )
        if not change_info["changed"]:
            logger.info("[INCREMENTAL] 页面未更新，跳过")
            return {"url": url, "status": "unchanged"}

    # 抓取
    page, err = smart_fetch(
        url, args.mode, args.warmup, args.proxy,
        cookies=simple_cookies, full_cookies=full_cookies,
    )

    if page is None:
        return {"url": url, "error": err}

    # 提取内容
    content = (
        page.get_all_text()
        if hasattr(page, 'get_all_text')
        else page.html_content or ""
    )

    # 增强：抓取后缓存
    enhancer.post_fetch(url, content)

    # 去重
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # 输出格式
    if args.output == "markdown":
        return {"url": url, "markdown": to_markdown(page), "_hash": content_hash}
    elif args.selector:
        fields = args.fields.split(",") if args.fields else None
        return {
            "url": url,
            "data": extract_data(page, args.selector, fields),
            "_hash": content_hash,
        }
    else:
        return {"url": url, "content": content[:10000], "_hash": content_hash}


def main():
    """主入口函数。"""
    args = parse_args()

    # 加载Cookie
    simple_cookies, full_cookies = load_cookies_from_args(args)

    # 刷新代理池
    if args.refresh_proxies:
        refresh_proxy_pool(init_proxy_db())
        return

    # 清理缓存
    if args.clear_cache:
        from enhancer import PageCache
        count = PageCache().clear_expired()
        logger.info("已清理 %d 条过期缓存", count)
        return

    if not args.url and not args.url_file:
        print("错误：必须提供 --url 或 --url-file", file=sys.stderr)
        sys.exit(1)

    # 收集 URL
    urls = collect_urls(args)

    # 初始化增强模块
    enhancer = ScrapingEnhancer()
    all_results = []
    seen_hashes = set()

    for i, url in enumerate(urls):
        logger.info("[%d/%d]: %s", i + 1, len(urls), url)

        result = scrape_single_url(
            url, args, enhancer, simple_cookies, full_cookies
        )

        # 去重检查
        content_hash = result.pop("_hash", None)
        if content_hash and content_hash in seen_hashes:
            result["status"] = "duplicate"
        if content_hash:
            seen_hashes.add(content_hash)

        all_results.append(result)

        # 请求间隔
        if args.delay > 0:
            time.sleep(args.delay)
        elif len(urls) > 1:
            human_pause(2, 5)

    # 输出结果
    output = format_results(all_results, args.output)
    if output:
        print(output)


if __name__ == "__main__":
    main()
