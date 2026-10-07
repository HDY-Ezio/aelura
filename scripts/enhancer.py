#!/usr/bin/env python3
"""
Aelura 增强模块
提供域名限速、增量抓取（ETag/Last-Modified）、智能重试、Sitemap 递归发现、
内容缓存去重、robots.txt 合规等增强功能。

v2.0: SQLite 连接复用、SSRF 防护集成、类型注解统一。
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List, Tuple, Any
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

from utils import validate_url as _validate_url, get_db

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
CACHE_DB_PATH: str = "./.aelura_cache.db"
CACHE_TTL_HOURS: int = 24
MAX_SITEMAP_DEPTH: int = 3
SITEMAP_RETRY_ATTEMPTS: int = 3
RATE_LIMIT_MIN_INTERVAL: float = 5.0   # 域名限速最小间隔（秒）
RATE_LIMIT_MAX_INTERVAL: float = 30.0  # 域名限速最大间隔（秒）
SITEMAP_DELAY: float = 1.0             # sitemap 抓取间隔（秒）


# ============================================================
# 域名限速器
# ============================================================
class DomainRateLimiter:
    """每个域名独立的请求限速器。"""

    def __init__(self):
        self._last_request_time: Dict[str, float] = {}

    def wait(self, url: str) -> None:
        """根据域名等待合适的时间间隔。"""
        domain = urlparse(url).netloc
        now = time.time()
        if domain in self._last_request_time:
            elapsed = now - self._last_request_time[domain]
            required = random.uniform(RATE_LIMIT_MIN_INTERVAL, RATE_LIMIT_MAX_INTERVAL)
            if elapsed < required:
                wait_time = required - elapsed
                logger.debug("[RateLimiter] 域名 %s 限速等待 %.1fs", domain, wait_time)
                time.sleep(wait_time)
        self._last_request_time[domain] = time.time()


# ============================================================
# 增量抓取（ETag / Last-Modified）
# ============================================================
def check_page_changed(url: str, headers: Optional[Dict] = None) -> Tuple[bool, Optional[Dict]]:
    """通过条件请求检查页面是否有更新。

    Args:
        url: 目标 URL。
        headers: 已保存的 ETag / Last-Modified 头。

    Returns:
        (是否已更新, 新的 etag/last_modified 字典)
    """
    # SSRF 校验
    err = _validate_url(url)
    if err:
        logger.warning("[Enhancer] URL 校验失败: %s", err)
        return True, None

    try:
        import requests
        conditional_headers = {}
        if headers:
            if "ETag" in headers:
                conditional_headers["If-None-Match"] = headers["ETag"]
            if "Last-Modified" in headers:
                conditional_headers["If-Modified-Since"] = headers["Last-Modified"]

        resp = requests.head(url, headers=conditional_headers, timeout=10, allow_redirects=True)
        new_headers = {
            "ETag": resp.headers.get("ETag"),
            "Last-Modified": resp.headers.get("Last-Modified"),
        }
        if resp.status_code == 304:
            logger.debug("[Enhancer] %s 未更新 (304)", url)
            return False, new_headers
        return True, new_headers
    except Exception as e:
        logger.warning("[Enhancer] 条件请求失败 %s: %s", url, e)
        return True, None


# ============================================================
# 智能重试
# ============================================================
def smart_retry(func, max_retries: int = 3, retryable_errors: Optional[List[str]] = None) -> Any:
    """带指数退避的智能重试装饰器。

    Args:
        func: 要执行的无参 callable。
        max_retries: 最大重试次数。
        retryable_errors: 可重试的错误关键词列表。

    Returns:
        func() 的返回值，或重试耗尽后的最后一次异常。
    """
    if retryable_errors is None:
        retryable_errors = ["timeout", "503", "429", "502", "504", "connection", "reset"]

    last_exception = None
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_exception = e
            error_str = str(e).lower()
            can_retry = any(keyword in error_str for keyword in retryable_errors)
            if not can_retry or attempt >= max_retries - 1:
                raise
            wait_time = min(2 ** attempt + random.uniform(0, 1), 60)
            logger.warning(
                "[Enhancer] 请求失败(第%d/%d次)，%s。%.1f秒后重试...",
                attempt + 1, max_retries, e, wait_time,
            )
            time.sleep(wait_time)

    if last_exception:
        raise last_exception


# ============================================================
# Sitemap 递归发现
# ============================================================
def discover_sitemap(domain: str, depth: int = 0) -> List[str]:
    """递归发现网站的 sitemap 及其中包含的所有 URL。

    Args:
        domain: 网站域名，如 "https://example.com"。
        depth: 当前递归深度（最大 MAX_SITEMAP_DEPTH）。

    Returns:
        发现的 URL 列表。
    """
    # SSRF 校验
    err = _validate_url(domain)
    if err:
        logger.warning("[Enhancer] sitemap 域名校验失败: %s", err)
        return []

    if depth > MAX_SITEMAP_DEPTH:
        logger.warning("[Enhancer] sitemap 递归深度超限 (%d > %d)", depth, MAX_SITEMAP_DEPTH)
        return []

    urls: List[str] = []
    sitemap_urls_to_check = []

    # 1. 检查 robots.txt
    robots_url = f"{domain.rstrip('/')}/robots.txt"
    try:
        import requests
        resp = requests.get(robots_url, timeout=10)
        if resp.status_code == 200:
            for line in resp.text.split("\n"):
                if line.lower().startswith("sitemap:"):
                    sitemap_url = line.split(":", 1)[1].strip()
                    sitemap_urls_to_check.append(sitemap_url)
    except Exception as e:
        logger.debug("[Enhancer] robots.txt 读取失败: %s", e)

    # 2. 常见的 sitemap 路径
    common_paths = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap/sitemap.xml"]
    for path in common_paths:
        sitemap_urls_to_check.append(f"{domain.rstrip('/')}{path}")

    # 去重
    sitemap_urls_to_check = list(set(sitemap_urls_to_check))

    # 3. 解析每个 sitemap
    import requests
    for sitemap_url in sitemap_urls_to_check:
        try:
            time.sleep(SITEMAP_DELAY)
            resp = requests.get(sitemap_url, timeout=10)
            if resp.status_code == 200:
                content = resp.text
                # 提取 <loc> 标签
                locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", content)
                for loc in locs:
                    if loc.endswith(".xml"):
                        # 嵌套 sitemap，递归解析
                        if depth < MAX_SITEMAP_DEPTH:
                            sub_urls = discover_sitemap(loc, depth + 1)
                            urls.extend(sub_urls)
                    else:
                        urls.append(loc)
        except Exception as e:
            logger.debug("[Enhancer] sitemap 解析失败 %s: %s", sitemap_url, e)

    urls = list(set(urls))
    logger.info("[Enhancer] sitemap 发现 %d 个 URL（域名: %s, 深度: %d）", len(urls), domain, depth)
    return urls


# ============================================================
# 内容缓存 & SHA-256 去重
# ============================================================
class PageCache:
    """基于 SQLite + SHA-256 的页面内容缓存，支持去重和 TTL 过期。"""

    def __init__(self, db_path: str = CACHE_DB_PATH, ttl_hours: int = CACHE_TTL_HOURS):
        self._db_path = db_path
        self._ttl_hours = ttl_hours
        self._init_db()

    def _init_db(self) -> None:
        """初始化缓存数据库表。"""
        conn = get_db(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS page_cache (
                url TEXT PRIMARY KEY,
                content_hash TEXT,
                content TEXT,
                headers TEXT,
                cached_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    def get(self, url: str) -> Optional[Dict]:
        """从缓存获取页面内容。

        Returns:
            缓存命中返回 {"content": str, "headers": str}，未命中或过期返回 None。
        """
        conn = get_db(self._db_path)
        cursor = conn.execute(
            "SELECT content, headers, cached_at FROM page_cache WHERE url=?",
            (url,),
        )
        row = cursor.fetchone()
        if row:
            content, headers, cached_at = row
            try:
                cache_time = datetime.strptime(cached_at, "%Y-%m-%d %H:%M:%S")
                if datetime.now() - cache_time < timedelta(hours=self._ttl_hours):
                    return {"content": content, "headers": headers}
            except ValueError:
                pass
            # 过期，清理
            conn.execute("DELETE FROM page_cache WHERE url=?", (url,))
            conn.commit()
        return None

    def set(self, url: str, content: str, headers: Optional[str] = None) -> None:
        """保存页面内容到缓存。"""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        conn = get_db(self._db_path)
        # 去重检查：相同哈希不重复存储
        cursor = conn.execute(
            "SELECT content_hash FROM page_cache WHERE url=?",
            (url,),
        )
        existing = cursor.fetchone()
        if existing and existing[0] == content_hash:
            logger.debug("[Cache] 内容未变，跳过更新: %s", url)
            return

        conn.execute(
            "INSERT OR REPLACE INTO page_cache (url, content_hash, content, headers) "
            "VALUES (?, ?, ?, ?)",
            (url, content_hash, content, headers),
        )
        conn.commit()

    def is_duplicate(self, url: str, content: str) -> bool:
        """检查内容是否与缓存中的内容重复。"""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn = get_db(self._db_path)
        cursor = conn.execute(
            "SELECT content_hash FROM page_cache WHERE content_hash=?",
            (content_hash,),
        )
        return cursor.fetchone() is not None

    def clear_expired(self) -> int:
        """清理过期缓存，返回清理数量。"""
        conn = get_db(self._db_path)
        cutoff = (datetime.now() - timedelta(hours=self._ttl_hours)).strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute("DELETE FROM page_cache WHERE cached_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount


# ============================================================
# robots.txt 合规
# ============================================================
class RobotsChecker:
    """检查 URL 是否符合 robots.txt 规则。"""

    def __init__(self, user_agent: str = "*"):
        self._user_agent = user_agent
        self._parsers: Dict[str, RobotFileParser] = {}
        self._crawl_delays: Dict[str, float] = {}

    def _get_parser(self, url: str) -> Optional[RobotFileParser]:
        domain = urlparse(url).scheme + "://" + urlparse(url).netloc
        if domain not in self._parsers:
            rp = RobotFileParser()
            robots_url = f"{domain}/robots.txt"
            try:
                rp.set_url(robots_url)
                rp.read()
                self._parsers[domain] = rp
                # 尝试解析 Crawl-delay
                self._extract_crawl_delay(robots_url, domain)
            except Exception as e:
                logger.debug("[Robots] 读取 robots.txt 失败 %s: %s", robots_url, e)
                self._parsers[domain] = None
        return self._parsers.get(domain)

    def _extract_crawl_delay(self, robots_url: str, domain: str) -> None:
        """提取 robots.txt 中的 Crawl-delay 值。"""
        try:
            import requests
            resp = requests.get(robots_url, timeout=5)
            if resp.status_code == 200:
                for line in resp.text.split("\n"):
                    line = line.strip().lower()
                    if line.startswith("crawl-delay:"):
                        delay_str = line.split(":", 1)[1].strip()
                        try:
                            self._crawl_delays[domain] = float(delay_str)
                            logger.info("[Robots] %s Crawl-delay: %.1fs", domain, self._crawl_delays[domain])
                        except ValueError:
                            pass
        except Exception:
            pass

    def can_fetch(self, url: str) -> bool:
        """检查给定 URL 是否允许抓取。"""
        parser = self._get_parser(url)
        if parser is None:
            return True  # 无法获取 robots.txt 时默认允许
        return parser.can_fetch(self._user_agent, url)

    def get_crawl_delay(self, url: str) -> Optional[float]:
        """获取指定域名的 Crawl-delay。"""
        domain = urlparse(url).scheme + "://" + urlparse(url).netloc
        return self._crawl_delays.get(domain)


# ============================================================
# 统一管理器
# ============================================================
class ScrapingEnhancer:
    """增强功能统一管理器。

    整合域名限速、增量抓取、智能重试、Sitemap 发现、内容缓存和 robots.txt 合规。
    """

    def __init__(self, respect_robots: bool = False, use_cache: bool = True):
        self.rate_limiter = DomainRateLimiter()
        self.robots = RobotsChecker() if respect_robots else None
        self.cache = PageCache() if use_cache else None

    def pre_fetch_check(self, url: str) -> Tuple[bool, Optional[Dict]]:
        """抓取前的预处理检查。

        Returns:
            (是否应该继续抓取, 已有的缓存数据)
        """
        # robots.txt 检查
        if self.robots and not self.robots.can_fetch(url):
            logger.info("[Enhancer] robots.txt 禁止抓取: %s", url)
            return False, None

        # 域名限速
        self.rate_limiter.wait(url)

        # 缓存检查
        if self.cache:
            cached = self.cache.get(url)
            if cached:
                logger.debug("[Enhancer] 缓存命中: %s", url)
                return False, cached

        return True, None

    def post_fetch(self, url: str, content: str, headers: Optional[Dict] = None) -> bool:
        """抓取后的后处理（缓存存储等）。

        Returns:
            内容是否为新增（非重复）。
        """
        if self.cache:
            is_dup = self.cache.is_duplicate(url, content)
            self.cache.set(url, content, str(headers) if headers else None)
            return not is_dup
        return True
