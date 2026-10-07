#!/usr/bin/env python3
"""
爬虫增强模块 - 域名限速/增量抓取/智能重试/Sitemap/缓存/robots.txt
纯Python，零外部API
"""

import hashlib
import json
import logging
import random
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

# ============================================================
# 1. 域名级限速
# ============================================================
class DomainRateLimiter:
    """每个域名独立限速"""
    
    def __init__(self, min_interval=5.0, max_interval=30.0):
        self.min_interval = min_interval
        self.max_interval = max_interval
        self._last_request = {}  # {domain: timestamp}
    
    def wait_if_needed(self, url):
        """如果需要等待则等待"""
        domain = urlparse(url).netloc
        now = time.time()
        last = self._last_request.get(domain, 0)
        elapsed = now - last
        
        # 基础间隔 + 随机抖动
        interval = random.uniform(self.min_interval, self.max_interval)
        
        if elapsed < interval:
            wait = interval - elapsed
            time.sleep(wait)
        
        self._last_request[domain] = time.time()
    
    def stats(self):
        """返回各域名最后请求时间统计"""
        return {
            d: f"{time.time() - t:.1f}s ago"
            for d, t in self._last_request.items()
        }


# ============================================================
# 2. 增量抓取（ETag / Last-Modified）
# ============================================================
def check_page_changed(url, cached_etag=None, cached_last_modified=None) -> dict:
    """
    检查页面是否有更新
    返回: {"changed": bool, "etag": str|None, "last_modified": str|None}
    """
    ok, cffi = False, None
    try:
        from curl_cffi import requests as cffi_requests
        cffi = cffi_requests
        ok = True
    except ImportError:
        pass
    
    if not ok:
        return {"changed": True, "etag": None, "last_modified": None}
    
    try:
        headers = {}
        if cached_etag:
            headers["If-None-Match"] = cached_etag
        if cached_last_modified:
            headers["If-Modified-Since"] = cached_last_modified
        
        resp = cffi.get(url, impersonate="chrome120", timeout=10, headers=headers)
        
        # 304 = 没变
        if resp.status_code == 304:
            return {"changed": False, "etag": cached_etag,
                    "last_modified": cached_last_modified}
        
        # 200 = 有更新
        if resp.status_code < 400:
            new_etag = resp.headers.get("ETag")
            new_lm = resp.headers.get("Last-Modified")
            return {"changed": True, "etag": new_etag, "last_modified": new_lm}
        
        return {"changed": True, "etag": None, "last_modified": None}
    except Exception as e:
        logger.debug("check_page_changed 失败 %s: %s", url, e)
        return {"changed": True, "etag": None, "last_modified": None}


# ============================================================
# 3. 智能重试
# ============================================================
def smart_retry(fn, max_retries=3, retry_on=None):
    """
    智能重试：对特定错误码等待后重试
    retry_on: 需要重试的错误关键词列表
    """
    if retry_on is None:
        retry_on = ["503", "502", "timeout", "429",
                     "rate limit", "too many", "temporarily"]
    
    last_err = None
    for attempt in range(max_retries + 1):
        result, err = fn()
        
        if err is None:
            return result, None
        
        # 检查是否值得重试
        err_lower = str(err).lower()
        should_retry = any(kw in err_lower for kw in retry_on)
        
        if should_retry and attempt < max_retries:
            wait = min(5 * (2 ** attempt) + random.uniform(1, 3), 60)
            logger.info("第%d次重试，等待%.1fs: %s", attempt + 1, wait, err)
            time.sleep(wait)
            last_err = err
        else:
            return result, err
    
    return None, last_err


# ============================================================
# 4. Sitemap 发现
# ============================================================
def discover_sitemap(base_url, _depth=0, _max_depth=3) -> list:
    """
    从网站发现 sitemap，返回所有页面URL列表。
    递归深度限制为 _max_depth 层，防止循环引用导致栈溢出。
    """
    if _depth > _max_depth:
        logger.warning("Sitemap 递归深度已达 %d 层，停止继续发现", _depth)
        return []

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return []
    
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    
    sitemap_urls = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemaps.xml",
        f"{base}/sitemap/sitemap.xml",
        f"{base}/sitemap.xml.gz",
    ]
    
    all_urls = []
    
    for sitemap_url in sitemap_urls:
        try:
            resp = cffi_requests.get(sitemap_url, impersonate="chrome120", timeout=10)
            if resp.status_code != 200:
                continue
            
            text = resp.text
            sub_sitemaps = re.findall(r'<loc>(https?://[^<]+\.xml[^<]*)</loc>', text)
            if sub_sitemaps:
                for sub in sub_sitemaps:
                    all_urls.extend(
                        discover_sitemap(sub, _depth=_depth + 1,
                                        _max_depth=_max_depth)
                    )
                continue
            
            urls = re.findall(r'<loc>(https?://[^<]+)</loc>', text)
            all_urls.extend(urls)
            
            if all_urls:
                logger.info("从 %s 发现 %d 个URL", sitemap_url, len(urls))
                break
        except Exception as e:
            logger.debug("Sitemap 请求失败 %s: %s", sitemap_url, e)
            continue
    
    # 去重
    return list(dict.fromkeys(all_urls))


# ============================================================
# 5. 缓存层
# ============================================================
CACHE_DB_PATH = Path(__file__).parent.parent / ".cache" / "page_cache.db"

class PageCache:
    """本地页面缓存"""
    
    def __init__(self, ttl_hours=24):
        self.ttl_seconds = ttl_hours * 3600
        self._init_db()
    
    def _init_db(self):
        CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(CACHE_DB_PATH))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                url TEXT PRIMARY KEY,
                content TEXT,
                etag TEXT,
                last_modified TEXT,
                cached_at REAL,
                content_hash TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def get(self, url) -> dict | None:
        """获取缓存，过期返回 None"""
        conn = sqlite3.connect(str(CACHE_DB_PATH))
        row = conn.execute(
            "SELECT content, etag, last_modified, cached_at, content_hash "
            "FROM cache WHERE url = ?",
            (url,)
        ).fetchone()
        conn.close()
        
        if not row:
            return None
        
        content, etag, lm, cached_at, content_hash = row
        
        if time.time() - cached_at > self.ttl_seconds:
            return None
        
        return {
            "content": content,
            "etag": etag,
            "last_modified": lm,
            "content_hash": content_hash,
        }
    
    def set(self, url: str, content: str, etag=None, last_modified=None):
        """写入缓存"""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        conn = sqlite3.connect(str(CACHE_DB_PATH))
        conn.execute(
            """INSERT OR REPLACE INTO cache
               (url, content, etag, last_modified, cached_at, content_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (url, content, etag, last_modified, time.time(), content_hash)
        )
        conn.commit()
        conn.close()
    
    def is_changed(self, url: str, new_content: str) -> bool:
        """检查内容是否有变化"""
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()
        conn = sqlite3.connect(str(CACHE_DB_PATH))
        row = conn.execute(
            "SELECT content_hash FROM cache WHERE url = ?", (url,)
        ).fetchone()
        conn.close()
        
        if not row:
            return True
        return row[0] != new_hash
    
    def clear_expired(self):
        """清理过期缓存"""
        cutoff = time.time() - self.ttl_seconds
        conn = sqlite3.connect(str(CACHE_DB_PATH))
        conn.execute("DELETE FROM cache WHERE cached_at < ?", (cutoff,))
        count = conn.total_changes
        conn.commit()
        conn.close()
        return count


# ============================================================
# 6. robots.txt 检查
# ============================================================
class RobotsChecker:
    """robots.txt 合规检查"""
    
    def __init__(self):
        self._parsers = {}  # {domain: RobotFileParser}
    
    def can_fetch(self, url: str, user_agent="*") -> bool:
        """检查是否允许爬取"""
        parsed = urlparse(url)
        domain = parsed.netloc
        
        if domain not in self._parsers:
            rp = RobotFileParser()
            robots_url = f"{parsed.scheme}://{domain}/robots.txt"
            try:
                from curl_cffi import requests as cffi_requests
                resp = cffi_requests.get(
                    robots_url, impersonate="chrome120", timeout=5
                )
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.parse([])
            except Exception as e:
                logger.debug("robots.txt 获取失败 %s: %s", robots_url, e)
                rp.parse([])
            
            self._parsers[domain] = rp
        
        return self._parsers[domain].can_fetch(user_agent, url)
    
    def get_crawl_delay(self, url: str) -> float:
        """获取建议的爬取延迟"""
        parsed = urlparse(url)
        domain = parsed.netloc
        
        if domain not in self._parsers:
            self.can_fetch(url)  # 初始化
        
        rp = self._parsers.get(domain)
        if rp:
            delay = rp.crawl_delay("*")
            if delay and delay > 0:
                return float(delay)
        
        return 0.0  # 无限制


# ============================================================
# 统一增强管理器
# ============================================================
class ScrapingEnhancer:
    """整合所有增强功能"""
    
    def __init__(self):
        self.rate_limiter = DomainRateLimiter(min_interval=5, max_interval=30)
        self.cache = PageCache(ttl_hours=24)
        self.robots = RobotsChecker()
    
    def pre_fetch_check(self, url: str) -> dict:
        """抓取前检查：robots + 缓存 + 限速"""
        if not self.robots.can_fetch(url):
            return {"allowed": False, "reason": "robots.txt 禁止"}
        
        cached = self.cache.get(url)
        if cached:
            return {"allowed": True, "cached": True, "cache": cached}
        
        crawl_delay = self.robots.get_crawl_delay(url)
        if crawl_delay > 0:
            self.rate_limiter.min_interval = max(
                self.rate_limiter.min_interval, crawl_delay
            )
        
        self.rate_limiter.wait_if_needed(url)
        
        return {"allowed": True, "cached": False}
    
    def post_fetch(self, url: str, content: str,
                   etag=None, last_modified=None):
        """抓取后处理：缓存"""
        if content:
            self.cache.set(url, content, etag, last_modified)
