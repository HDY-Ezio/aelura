#!/usr/bin/env python3
"""
免费代理池 - 纯Python，零付费API
自动从免费代理网站抓取、检测存活、轮换使用
数据源：free-proxy-list.net, proxyscrape.com, geonode.com 等
"""

import json
import logging
import random
import sqlite3
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 延迟导入
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


# ============================================================
# 代理池数据库
# ============================================================
DB_PATH = Path(__file__).parent.parent / ".cache" / "proxy_pool.db"

def init_db():
    """初始化代理池 SQLite 数据库。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS proxies (
            ip TEXT,
            port INTEGER,
            protocol TEXT DEFAULT 'https',
            country TEXT DEFAULT '',
            source TEXT DEFAULT '',
            status TEXT DEFAULT 'unknown',
            response_time REAL DEFAULT 999,
            last_checked TEXT DEFAULT '',
            fail_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ip, port)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS proxy_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proxy TEXT,
            url TEXT,
            success INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def get_available_proxies(conn, min_count=5) -> list:
    """获取可用代理（response_time < 5s, fail_count < 3）"""
    cursor = conn.execute("""
        SELECT ip, port, protocol, country, response_time 
        FROM proxies 
        WHERE status = 'alive' AND fail_count < 3 AND response_time < 5
        ORDER BY response_time ASC
        LIMIT 50
    """)
    proxies = cursor.fetchall()
    
    # 如果不够，放宽条件
    if len(proxies) < min_count:
        cursor = conn.execute("""
            SELECT ip, port, protocol, country, response_time 
            FROM proxies 
            WHERE status = 'alive' AND fail_count < 5
            ORDER BY response_time ASC
            LIMIT 50
        """)
        proxies = cursor.fetchall()
    
    return proxies


def record_usage(conn, proxy: str, url: str, success: bool):
    """记录代理使用情况"""
    conn.execute(
        "INSERT INTO proxy_usage (proxy, url, success) VALUES (?, ?, ?)",
        (proxy, url, 1 if success else 0)
    )
    if not success:
        ip, port = proxy.split(":")
        conn.execute(
            "UPDATE proxies SET fail_count = fail_count + 1 WHERE ip = ? AND port = ?",
            (ip, int(port))
        )
    conn.commit()


# ============================================================
# 免费代理抓取器
# ============================================================

PROXY_SOURCES = [
    {
        "name": "free-proxy-list.net",
        "url": "https://free-proxy-list.net/",
        "parse": "parse_free_proxy_list",
    },
    {
        "name": "proxyscrape.com",
        "url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
        "parse": "parse_proxyscrape",
    },
    {
        "name": "geonode.com",
        "url": "https://proxylist.geonode.com/api/proxy-list?limit=100&page=1&sort_by=lastChecked&sort_type=desc",
        "parse": "parse_geonode",
    },
]


def fetch_from_source(source: dict) -> list:
    """从单个源抓取代理"""
    ok, cffi = try_import_curl_cffi()
    if not ok:
        logger.warning("curl_cffi 未安装，跳过 %s", source['name'])
        return []
    
    try:
        resp = cffi.get(source["url"], impersonate="chrome120", timeout=10)
        if resp.status_code != 200:
            return []
        
        parse_fn_name = source["parse"]
        parse_fn = globals().get(parse_fn_name)
        if parse_fn:
            return parse_fn(resp.text)
    except Exception as e:
        logger.warning("%s 抓取失败: %s", source['name'], e)
    
    return []


def parse_free_proxy_list(html: str) -> list:
    """解析 free-proxy-list.net"""
    proxies = []
    try:
        import re
        pattern = r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})</td>\s*<td>(\d{2,5})</td>'
        matches = re.findall(pattern, html)
        for ip, port in matches:
            proxies.append({
                "ip": ip, "port": int(port),
                "protocol": "https", "source": "free-proxy-list"
            })
    except Exception as e:
        logger.debug("解析 free-proxy-list 失败: %s", e)
    return proxies


def parse_proxyscrape(text: str) -> list:
    """解析 proxyscrape API"""
    proxies = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if ":" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    proxies.append({
                        "ip": parts[0],
                        "port": int(parts[1]),
                        "protocol": "https",
                        "source": "proxyscrape",
                    })
                except ValueError:
                    pass
    return proxies


def parse_geonode(json_text: str) -> list:
    """解析 geonode API"""
    proxies = []
    try:
        data = json.loads(json_text)
        for item in data.get("data", []):
            proxies.append({
                "ip": item.get("ip", ""),
                "port": item.get("port", 0),
                "protocol": (
                    item.get("protocols", ["https"])[0]
                    if item.get("protocols") else "https"
                ),
                "country": item.get("country", ""),
                "source": "geonode",
            })
    except Exception as e:
        logger.debug("解析 geonode 失败: %s", e)
    return proxies


def check_proxy_alive(proxy: dict, timeout=5) -> bool:
    """检测代理是否存活"""
    ok, cffi = try_import_curl_cffi()
    if not ok:
        return False
    
    try:
        proxy_url = f"{proxy['protocol']}://{proxy['ip']}:{proxy['port']}"
        start = time.time()
        resp = cffi.get(
            "https://httpbin.org/ip",
            impersonate="chrome120",
            timeout=timeout,
            proxies={"https": proxy_url, "http": proxy_url},
        )
        elapsed = time.time() - start
        if resp.status_code == 200:
            proxy["response_time"] = elapsed
            return True
    except Exception as e:
        logger.debug("代理存活检测失败 %s: %s", proxy_url, e)
    return False


def refresh_proxy_pool(conn, min_alive=10):
    """刷新代理池：抓取 + 检测"""
    logger.info("开始刷新代理池...")
    
    # 1. 从各源抓取
    all_proxies = []
    for source in PROXY_SOURCES:
        proxies = fetch_from_source(source)
        all_proxies.extend(proxies)
        logger.info("%s: 抓取 %d 个", source['name'], len(proxies))
    
    # 去重
    seen = set()
    unique = []
    for p in all_proxies:
        key = f"{p['ip']}:{p['port']}"
        if key not in seen:
            seen.add(key)
            unique.append(p)
    
    logger.info("去重后: %d 个", len(unique))
    
    # 2. 存入数据库
    for p in unique:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO proxies
                   (ip, port, protocol, country, source)
                   VALUES (?, ?, ?, ?, ?)""",
                (p["ip"], p["port"], p.get("protocol", "https"),
                 p.get("country", ""), p.get("source", ""))
            )
        except Exception as e:
            logger.debug("代理入库写入失败 %s:%s: %s",
                         p["ip"], p["port"], e)
    conn.commit()
    
    # 3. 检测存活
    to_check = conn.execute(
        "SELECT ip, port, protocol, country, source FROM proxies "
        "WHERE status != 'alive' "
        "OR last_checked < datetime('now', '-2 hours') LIMIT 30"
    ).fetchall()
    
    alive_count = 0
    for row in to_check:
        proxy = {
            "ip": row[0], "port": row[1], "protocol": row[2],
            "country": row[3], "source": row[4]
        }
        if check_proxy_alive(proxy):
            conn.execute(
                """UPDATE proxies SET status='alive',
                   response_time=?, last_checked=datetime('now'),
                   fail_count=0
                   WHERE ip=? AND port=?""",
                (proxy["response_time"], proxy["ip"], proxy["port"])
            )
            alive_count += 1
        else:
            conn.execute(
                """UPDATE proxies SET status='dead',
                   last_checked=datetime('now')
                   WHERE ip=? AND port=?""",
                (proxy["ip"], proxy["port"])
            )
        conn.commit()
    
    total_alive = conn.execute(
        "SELECT COUNT(*) FROM proxies WHERE status='alive'"
    ).fetchone()[0]
    logger.info("本次检测存活: %d, 总存活: %d", alive_count, total_alive)
    
    # 4. 清理死代理
    conn.execute("DELETE FROM proxies WHERE fail_count >= 5")
    conn.commit()
    
    return total_alive


def get_random_proxy(conn) -> str | None:
    """随机获取一个可用代理，返回 ip:port 格式"""
    proxies = get_available_proxies(conn)
    if not proxies:
        return None
    
    proxy = random.choice(proxies)
    return f"{proxy[0]}:{proxy[1]}"
