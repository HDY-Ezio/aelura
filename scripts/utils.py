#!/usr/bin/env python3
"""
Aelura 公共工具模块
提供共享的导入辅助、URL 校验（含 SSRF 防护）、配置加载、资源生命周期管理。

v1.1: 
  - SSRF 防护增加 DNS 解析（防 DNS rebinding）
  - 统一资源清理（atexit）
  - 表名常量化
  - deepcopy 替代 JSON hack
"""

from __future__ import annotations

import atexit
import copy
import ipaddress
import json
import logging
import socket
from pathlib import Path
from typing import Optional, Tuple, Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ============================================================
# 共享常量
# ============================================================
MIN_CONTENT_LENGTH: int = 100  # 内容有效性的最低字符长度
VALID_SCHEMES: set = {"http", "https"}
DEFAULT_MODE: str = "auto"
DEFAULT_OUTPUT: str = "json"

# SQLite 表名常量（消除魔法字符串）
PROXY_TABLE_NAME: str = "proxies"
PROXY_USAGE_TABLE_NAME: str = "proxy_usage"
CACHE_TABLE_NAME: str = "page_cache"

# ============================================================
# SSRF 防护（含 DNS 解析）
# ============================================================
_BLOCKED_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_blocked_ip(ip_str: str) -> bool:
    """检查 IP 地址是否在封锁列表中。"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in _BLOCKED_IP_RANGES)
    except ValueError:
        return False


def is_private_host(hostname: str) -> bool:
    """检查 hostname 是否为内网/回环地址。
    
    先检查是否为字面 IP，如果是则直接判断；
    否则做 DNS 解析，检查解析后的 IP 地址。
    这样可以防止 DNS rebinding 攻击。
    """
    # 先检查是否为字面 IP
    if _is_blocked_ip(hostname):
        return True
    
    # 如果是域名，做 DNS 解析
    try:
        # getaddrinfo 返回 (family, type, proto, canonname, sockaddr)
        # sockaddr 是 (address, port) 元组
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if _is_blocked_ip(ip_str):
                return True
    except (socket.gaierror, socket.herror, OSError):
        # DNS 解析失败，放行（让后续请求处理）
        pass
    
    return False


def validate_url(url: str) -> Optional[str]:
    """校验 URL 格式合法性，包含 SSRF 防护。

    Returns:
        None 表示合法；否则返回错误描述字符串。
    """
    if not url or not isinstance(url, str):
        return "URL 不能为空"

    parsed = urlparse(url)

    if parsed.scheme not in VALID_SCHEMES:
        return f"不支持的协议: {parsed.scheme}，仅允许 http/https"

    if not parsed.netloc:
        return "URL 缺少有效域名"

    # SSRF 防护：检查是否为内网地址
    hostname = parsed.hostname
    if hostname and is_private_host(hostname):
        return f"不允许访问内网地址: {hostname}"

    return None


# ============================================================
# 延迟导入辅助
# ============================================================
def try_import_curl_cffi() -> Tuple[bool, Any]:
    """尝试导入 curl_cffi 库。

    Returns:
        tuple: (True, cffi_requests) 成功时；(False, None) 失败时。
    """
    try:
        from curl_cffi import requests as cffi_requests
        return True, cffi_requests
    except ImportError:
        return False, None


def try_import_scrapling() -> Tuple[bool, Any]:
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


def try_import_playwright() -> Tuple[bool, Any]:
    """尝试导入 Playwright 库。

    Returns:
        tuple: (True, sync_playwright) 成功时；(False, None) 失败时。
    """
    try:
        from playwright.sync_api import sync_playwright
        return True, sync_playwright
    except ImportError:
        return False, None


# ============================================================
# 资源生命周期管理（atexit 清理）
# ============================================================
_cleanup_callbacks: list = []


def register_cleanup(callback, *args, **kwargs) -> None:
    """注册退出时的清理回调。"""
    _cleanup_callbacks.append((callback, args, kwargs))


def _run_cleanup() -> None:
    """执行所有注册的清理回调。"""
    for callback, args, kwargs in reversed(_cleanup_callbacks):
        try:
            callback(*args, **kwargs)
        except Exception as e:
            logger.debug("[Cleanup] 清理回调异常: %s", e)
    _cleanup_callbacks.clear()


# 注册 atexit 处理器
atexit.register(_run_cleanup)


# ============================================================
# SQLite 连接复用
# ============================================================
_db_connections: dict = {}
_db_initialized: set = set()  # 追踪已初始化的数据库


def get_db(db_path) -> Any:
    """获取或复用 SQLite 连接。

    避免频繁开关连接，同一 db_path 始终返回同一个连接对象。

    Args:
        db_path: 数据库文件路径。

    Returns:
        sqlite3.Connection 实例。
    """
    path_str = str(db_path)
    if path_str not in _db_connections:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        try:
            _db_connections[path_str] = sqlite3.connect(str(db_path))
        except sqlite3.Error as e:
            logger.error("[DB] 连接失败 %s: %s", db_path, e)
            raise
    return _db_connections[path_str]


def mark_db_initialized(db_path) -> None:
    """标记数据库已初始化（避免重复 CREATE TABLE）。"""
    _db_initialized.add(str(db_path))


def is_db_initialized(db_path) -> bool:
    """检查数据库是否已初始化。"""
    return str(db_path) in _db_initialized


def close_all_db() -> None:
    """关闭所有缓存的 SQLite 连接。"""
    for conn in _db_connections.values():
        try:
            conn.close()
        except Exception:
            pass
    _db_connections.clear()
    _db_initialized.clear()


# 注册 atexit 清理
register_cleanup(close_all_db)


# ============================================================
# 配置加载
# ============================================================
DEFAULT_CONFIG: dict = {
    "default_mode": "auto",
    "default_output": "json",
    "delay": 0,
    "warmup": False,
    "proxy": False,
    "respect_robots": False,
    "incremental": False,
    "timeout": 15,
    "max_retries": 3,
    "min_content_length": MIN_CONTENT_LENGTH,
    "rate_limit": {
        "min_interval": 5.0,
        "max_interval": 30.0,
    },
    "cache": {
        "ttl_hours": 24,
        "enabled": True,
    },
    "browser": {
        "headless": True,
        "timeout": 20,
    },
    "proxy_pool": {
        "auto_refresh": False,
        "min_alive": 10,
    },
}


def load_config(config_path: Optional[str] = None) -> dict:
    """加载配置文件，与默认配置深度合并。

    配置文件为 JSON 格式。未提供的字段使用默认值。
    默认搜索路径（按优先级）：
      1. 命令行指定的 --config 路径
      2. 当前目录的 aelura.json
      3. 当前目录的 .aelura.json
      4. 用户主目录的 .aelura.json

    Args:
        config_path: 配置文件路径。为 None 时自动搜索默认路径。

    Returns:
        合并后的配置字典。
    """
    config = copy.deepcopy(DEFAULT_CONFIG)

    if config_path is None:
        default_paths = [
            Path.cwd() / "aelura.json",
            Path.cwd() / ".aelura.json",
            Path.home() / ".aelura.json",
        ]
        for p in default_paths:
            if p.exists():
                config_path = str(p)
                break

    if config_path and Path(config_path).exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config = json.load(f)
            config = _deep_merge(config, user_config)
            logger.info("已加载配置文件: %s", config_path)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("配置文件加载失败 (%s): %s", config_path, e)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 中的值覆盖 base 中的值。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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
