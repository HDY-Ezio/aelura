#!/usr/bin/env python3
"""
Aelura 免费代理池模块
从免费代理源抓取代理，存入本地 SQLite，提供存活检测与自动淘汰。

v2.0: SQLite 连接复用、代理安全警告、输入校验、类型注解统一。
"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from utils import try_import_curl_cffi, get_db, mark_db_initialized, is_db_initialized
from utils import PROXY_TABLE_NAME, PROXY_USAGE_TABLE_NAME

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================
PROXY_DB_PATH: str = "./.aelura_proxies.db"
MAX_DB_POOL_SIZE: int = 50              # 连接池最大条目（软限制）
FREE_PROXY_URLS = [
    "https://free-proxy-list.net/",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc",
]
PROXY_TIMEOUT: float = 10.0             # 代理检测超时（秒）
MAX_FAIL_COUNT: int = 5                 # 最大失败次数（超过则淘汰）
GOOD_RESPONSE_TIME: float = 5.0         # 良好响应时间阈值（秒）
REFRESH_COOLDOWN: int = 600             # 刷新冷却（秒）

# ============================================================
# 安全警告
# ============================================================
logger.warning(
    "⚠️ 免费代理存在安全风险：代理服务器可以拦截、篡改或记录所有流量。"
    "请勿用于传输敏感数据（密码、支付信息等）。生产环境请使用付费代理。"
)


# ============================================================
# 代理抓取
# ============================================================
def fetch_free_proxies() -> List[Dict]:
    """从多个免费源抓取代理列表。

    Returns:
        [{"ip": str, "port": int, "source": str}, ...]
    """
    proxies: List[Dict] = []

    has_cffi, cffi = try_import_curl_cffi()
    if not has_cffi:
        logger.warning("curl_cffi 未安装，无法抓取免费代理。pip install curl-cffi")
        return proxies

    # --- 源 1: free-proxy-list.net ---
    try:
        resp = cffi.get(
            FREE_PROXY_URLS[0],
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"},
            impersonate="chrome131",
        )
        html = resp.text
        # 简单正则提取 IP:Port
        ip_port_pattern = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*</td>\s*<td>(\d{2,5})")
        matches = ip_port_pattern.findall(html)
        for ip, port in matches[:200]:
            if _validate_proxy_format(ip, int(port)):
                proxies.append({"ip": ip, "port": int(port), "source": "free-proxy-list"})
        logger.info("[ProxyPool] free-proxy-list.net 抓取到 %d 条", len(matches[:200]))
    except Exception as e:
        logger.warning("[ProxyPool] free-proxy-list.net 抓取失败: %s", e)

    # --- 源 2: proxyscrape ---
    try:
        resp = cffi.get(
            FREE_PROXY_URLS[1],
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"},
        )
        lines = resp.text.strip().split("\n")
        for line in lines[:200]:
            line = line.strip()
            if ":" in line:
                parts = line.split(":")
                ip, port_str = parts[0], parts[1]
                try:
                    port = int(port_str)
                    if _validate_proxy_format(ip, port):
                        proxies.append({"ip": ip, "port": port, "source": "proxyscrape"})
                except ValueError:
                    continue
        logger.info("[ProxyPool] proxyscrape 抓取到 %d 条", len(lines[:200]))
    except Exception as e:
        logger.warning("[ProxyPool] proxyscrape 抓取失败: %s", e)

    # --- 源 3: geonode ---
    try:
        resp = cffi.get(
            FREE_PROXY_URLS[2],
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"},
        )
        data = resp.json()
        for item in data.get("data", []):
            ip = item.get("ip", "")
            port = item.get("port", "")
            try:
                port = int(port)
                if _validate_proxy_format(ip, port):
                    proxies.append({"ip": ip, "port": port, "source": "geonode"})
            except ValueError:
                continue
        logger.info("[ProxyPool] geonode 抓取到 %d 条", len(data.get("data", [])))
    except Exception as e:
        logger.warning("[ProxyPool] geonode 抓取失败: %s", e)

    # 去重
    seen: set = set()
    unique: List[Dict] = []
    for p in proxies:
        key = f"{p['ip']}:{p['port']}"
        if key not in seen:
            seen.add(key)
            unique.append(p)

    logger.info("[ProxyPool] 共抓取 %d 个不重复代理", len(unique))
    return unique


def _validate_proxy_format(ip: str, port: int) -> bool:
    """校验代理 IP:Port 格式的合法性。"""
    if port < 1 or port > 65535:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        try:
            num = int(part)
            if num < 0 or num > 255:
                return False
        except ValueError:
            return False
    return True


# ============================================================
# SQLite 存储（使用共享连接池 + 初始化追踪）
# ============================================================
def _init_db():
    """初始化代理数据库表（仅首次调用时执行）。"""
    if is_db_initialized(PROXY_DB_PATH):
        return
    
    conn = get_db(PROXY_DB_PATH)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {PROXY_TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            port INTEGER NOT NULL,
            source TEXT DEFAULT '',
            is_alive INTEGER DEFAULT 1,
            response_time REAL DEFAULT NULL,
            fail_count INTEGER DEFAULT 0,
            last_checked TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(ip, port)
        )
    """)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {PROXY_USAGE_TABLE_NAME} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proxy_id INTEGER,
            used_at TEXT DEFAULT (datetime('now')),
            success INTEGER DEFAULT 1,
            FOREIGN KEY(proxy_id) REFERENCES {PROXY_TABLE_NAME}(id)
        )
    """)
    conn.commit()
    mark_db_initialized(PROXY_DB_PATH)


def save_proxies(proxies: List[Dict]) -> None:
    """将代理列表保存到数据库。"""
    _init_db()
    conn = get_db(PROXY_DB_PATH)
    for p in proxies:
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {PROXY_TABLE_NAME} (ip, port, source) VALUES (?, ?, ?)",
                (p["ip"], p["port"], p.get("source", "")),
            )
        except Exception as e:
            logger.warning("[ProxyPool] 保存代理失败: %s", e)
    conn.commit()
    logger.info("[ProxyPool] 已保存 %d 条代理到数据库", len(proxies))


def refresh_proxy_pool() -> int:
    """刷新代理池：抓取 → 入库 → 检测存活 → 清理。

    Returns:
        存活的代理数量。
    """
    proxies = fetch_free_proxies()
    if not proxies:
        logger.warning("[ProxyPool] 未抓取到任何代理")
        return 0

    save_proxies(proxies)
    alive_count = check_proxy_alive()
    return alive_count


def check_proxy_alive() -> int:
    """检测数据库中所有代理的存活状态。

    每次检测间隔 0.2 秒，避免被 httpbin.org 限流。

    Returns:
        存活代理数量。
    """
    _init_db()
    conn = get_db(PROXY_DB_PATH)
    cursor = conn.execute(
        f"SELECT id, ip, port FROM {PROXY_TABLE_NAME} WHERE is_alive = 1"
    )
    rows = cursor.fetchall()

    has_cffi, cffi = try_import_curl_cffi()
    alive_count = 0

    for i, (proxy_id, ip, port) in enumerate(rows):
        # 限流：每 0.2 秒检测一个，避免被目标站 ban
        if i > 0:
            time.sleep(0.2)
        
        proxy_url = f"http://{ip}:{port}"
        start_time = time.time()
        try:
            if has_cffi:
                resp = cffi.get(
                    "https://httpbin.org/ip",
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=PROXY_TIMEOUT,
                )
                elapsed = time.time() - start_time
                if resp.status_code == 200:
                    conn.execute(
                        f"UPDATE {PROXY_TABLE_NAME} SET is_alive=1, response_time=?, last_checked=datetime('now') WHERE id=?",
                        (elapsed, proxy_id),
                    )
                    alive_count += 1
                else:
                    conn.execute(
                        f"UPDATE {PROXY_TABLE_NAME} SET fail_count=fail_count+1, last_checked=datetime('now') WHERE id=?",
                        (proxy_id,),
                    )
            else:
                conn.execute(
                    f"UPDATE {PROXY_TABLE_NAME} SET is_alive=0, last_checked=datetime('now') WHERE id=?",
                    (proxy_id,),
                )
        except Exception:
            conn.execute(
                f"UPDATE {PROXY_TABLE_NAME} SET fail_count=fail_count+1, is_alive=0, last_checked=datetime('now') WHERE id=?",
                (proxy_id,),
            )

    conn.commit()

    # 清理超过最大失败次数的代理
    conn.execute(f"DELETE FROM {PROXY_TABLE_NAME} WHERE fail_count >= ?", (MAX_FAIL_COUNT,))
    conn.commit()

    logger.info("[ProxyPool] 存活代理: %d / %d", alive_count, len(rows))
    return alive_count


def get_available_proxies(limit: int = 20) -> List[str]:
    """获取可用代理列表。

    优先返回响应时间快、失败次数少的代理。

    Args:
        limit: 返回数量上限。

    Returns:
        ["ip:port", ...]
    """
    _init_db()
    conn = get_db(PROXY_DB_PATH)

    # 优先选响应时间 < 5s 且失败 < 3 次的
    cursor = conn.execute(
        f"SELECT ip, port FROM {PROXY_TABLE_NAME} WHERE is_alive=1 AND response_time < ? AND fail_count < 3 "
        "ORDER BY response_time ASC LIMIT ?",
        (GOOD_RESPONSE_TIME, limit),
    )
    results = cursor.fetchall()

    # 不够的话放宽条件
    if len(results) < limit:
        cursor = conn.execute(
            f"SELECT ip, port FROM {PROXY_TABLE_NAME} WHERE is_alive=1 AND fail_count < ? "
            "ORDER BY response_time ASC LIMIT ?",
            (MAX_FAIL_COUNT, limit),
        )
        results = cursor.fetchall()

    proxies = [f"{ip}:{port}" for ip, port in results]

    if not proxies:
        logger.warning("[ProxyPool] 没有可用代理，建议执行 refresh_proxy_pool()")

    return proxies


def record_usage(proxy: str, success: bool) -> None:
    """记录代理使用结果。

    Args:
        proxy: "ip:port" 格式的代理字符串。
        success: 本次使用是否成功。
    """
    _init_db()
    conn = get_db(PROXY_DB_PATH)

    # 校验格式
    if not isinstance(proxy, str) or ":" not in proxy:
        logger.warning("[ProxyPool] record_usage: 非法代理格式 '%s'", proxy)
        return

    parts = proxy.rsplit(":", 1)
    if len(parts) != 2:
        logger.warning("[ProxyPool] record_usage: 非法代理格式 '%s'", proxy)
        return

    ip, port_str = parts
    try:
        port = int(port_str)
    except ValueError:
        logger.warning("[ProxyPool] record_usage: 端口非数字 '%s'", proxy)
        return

    cursor = conn.execute(f"SELECT id FROM {PROXY_TABLE_NAME} WHERE ip=? AND port=?", (ip, port))
    row = cursor.fetchone()
    if row:
        proxy_id = row[0]
        conn.execute(
            f"INSERT INTO {PROXY_USAGE_TABLE_NAME} (proxy_id, success) VALUES (?, ?)",
            (proxy_id, 1 if success else 0),
        )
        if not success:
            conn.execute(f"UPDATE {PROXY_TABLE_NAME} SET fail_count=fail_count+1 WHERE id=?", (proxy_id,))
        else:
            conn.execute(f"UPDATE {PROXY_TABLE_NAME} SET fail_count=MAX(0,fail_count-1) WHERE id=?", (proxy_id,))
        conn.commit()


# ============================================================
# CLI 入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aelura 免费代理池")
    parser.add_argument("--refresh", action="store_true", help="刷新代理池")
    parser.add_argument("--list", action="store_true", help="列出可用代理")
    parser.add_argument("--limit", type=int, default=20, help="列出数量上限")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    if args.refresh:
        count = refresh_proxy_pool()
        print(f"代理池刷新完成，存活代理: {count}")
    elif args.list:
        proxies = get_available_proxies(limit=args.limit)
        for p in proxies:
            print(p)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
