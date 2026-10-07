#!/usr/bin/env python3
"""
Aelura 指纹轮换引擎
生成真实浏览器指纹（UA / 屏幕 / 时区 / 语言 / 硬件参数），
并按地理区域保持一致性，避免被反爬系统识别为机器人。

v2.0: 递归改迭代，消除栈溢出风险；魔法数字常量化。
"""

from __future__ import annotations

import hashlib
import random
import time
from typing import Optional, Dict, List, Tuple

# ============================================================
# 常量
# ============================================================
MAX_UNIQUE_FINGERPRINTS: int = 200   # 去重池大小上限
SCREEN_RESOLUTIONS: List[str] = [
    "1920x1080", "2560x1440", "1366x768", "1536x864",
    "1440x900", "1280x720", "3840x2160",
    "360x740", "390x844", "412x915",   # 移动端
    "428x926", "393x873", "375x812", "375x667",
    "414x896", "320x568",
]

VIEWPORT_MAP: Dict[str, Tuple[int, int]] = {
    "1920x1080": (1920, 969),
    "2560x1440": (2560, 1369),
    "1366x768":  (1366, 728),
    "1536x864":  (1536, 824),
    "1440x900":  (1440, 861),
    "1280x720":  (1280, 651),
    "3840x2160": (3840, 2089),
    "360x740":   (360, 740),
    "390x844":   (390, 844),
    "412x915":   (412, 915),
    "428x926":   (428, 926),
    "393x873":   (393, 873),
    "375x812":   (375, 812),
    "375x667":   (375, 667),
    "414x896":   (414, 896),
    "320x568":   (320, 568),
}

# 地理一致性配置：(时区, 语言, 平台关键词)
GEO_PROFILES: List[Tuple[str, str, List[str]]] = [
    ("Asia/Shanghai",    "zh-CN", ["Win", "Android"]),
    ("America/New_York", "en-US", ["Win", "Mac", "iPhone"]),
    ("Europe/London",    "en-GB", ["Win", "Mac", "Linux"]),
    ("Asia/Tokyo",       "ja-JP", ["Win", "Android"]),
    ("Europe/Berlin",    "de-DE", ["Win", "Linux"]),
    ("America/Los_Angeles", "en-US", ["Mac", "iPhone", "Win"]),
    ("Asia/Kolkata",     "en-IN", ["Android", "Win"]),
    ("Australia/Sydney", "en-AU", ["Win", "Mac"]),
]

USER_AGENTS: List[str] = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Chrome Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    # iPhone Safari
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
]


class FingerprintEngine:
    """
    指纹轮换引擎。
    - 地理一致性：UA、时区、语言、平台互相匹配
    - 去重：同一会话内不重复使用指纹（直到池满）
    """

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)
        self._used: set = set()
        self._max_unique: int = MAX_UNIQUE_FINGERPRINTS

    def _pick_geo_profile(self) -> Tuple[str, str, List[str]]:
        """选择一个地理配置。"""
        return self._rng.choice(GEO_PROFILES)

    def _match_ua(self, platform_keywords: List[str]) -> str:
        """根据平台关键词筛选匹配的 UA。"""
        matched = [ua for ua in USER_AGENTS if any(kw in ua for kw in platform_keywords)]
        if not matched:
            return self._rng.choice(USER_AGENTS)
        return self._rng.choice(matched)

    def _pick_screen(self, ua: str) -> str:
        """根据 UA 选择合适的屏幕分辨率（移动端 vs 桌面端）。"""
        is_mobile = "Mobile" in ua or "Android" in ua or "iPhone" in ua
        if is_mobile:
            mobile_screens = [r for r in SCREEN_RESOLUTIONS if "x" in r and int(r.split("x")[0]) < 500]
            return self._rng.choice(mobile_screens) if mobile_screens else self._rng.choice(SCREEN_RESOLUTIONS)
        else:
            desktop_screens = [r for r in SCREEN_RESOLUTIONS if "x" in r and int(r.split("x")[0]) >= 1000]
            return self._rng.choice(desktop_screens) if desktop_screens else self._rng.choice(SCREEN_RESOLUTIONS)

    def generate(self) -> dict:
        """生成一套完整的浏览器指纹（迭代方式，避免递归栈溢出）。"""
        max_attempts = self._max_unique * 2

        for _ in range(max_attempts):
            timezone, language, platforms = self._pick_geo_profile()
            ua = self._match_ua(platforms)
            screen = self._pick_screen(ua)
            w, h = VIEWPORT_MAP.get(screen, (1920, 1080))

            fingerprint = {
                "user_agent": ua,
                "viewport": {"width": w, "height": h},
                "screen": {"width": int(screen.split("x")[0]), "height": int(screen.split("x")[1])},
                "timezone": timezone,
                "locale": language,
                "platform": "Win32" if "Win" in ua else ("MacIntel" if "Mac" in ua else ("Linux" if "X11" in ua else "")),
                "hardware_concurrency": self._rng.choice([2, 4, 6, 8, 12, 16]),
                "device_memory": self._rng.choice([2, 4, 8, 16, 32]),
                "color_depth": self._rng.choice([24, 32]),
                "color_scheme": self._rng.choice(["light", "light", "light", "dark"]),
            }

            # 去重 key
            fp_key = hashlib.md5(str(sorted(fingerprint.items())).encode()).hexdigest()

            if fp_key not in self._used or len(self._used) >= self._max_unique:
                if len(self._used) >= self._max_unique:
                    self._used.clear()
                self._used.add(fp_key)
                return fingerprint

        # 兜底：超出最大尝试次数，返回最后一次生成的指纹（允许重复）
        return fingerprint

    def generate_playwright_args(self) -> dict:
        """生成适用于 Playwright launch / context 的指纹参数。"""
        fp = self.generate()
        return {
            "user_agent": fp["user_agent"],
            "viewport": fp["viewport"],
            "screen": fp["screen"],
            "locale": fp["locale"],
            "timezone_id": fp["timezone"],
            "device_scale_factor": self._rng.uniform(1.0, 3.0),
            "color_scheme": fp["color_scheme"],
        }

    def generate_curl_cfi_kwargs(self) -> dict:
        """生成适用于 curl_cffi 的请求参数。"""
        fp = self.generate()
        return {
            "headers": {
                "User-Agent": fp["user_agent"],
                "Accept-Language": f'{fp["locale"]},{fp["locale"].split("-")[0]};q=0.9',
            },
            "timeout": 15,
        }


# ============================================================
# 模块级便捷接口
# ============================================================
_default_engine = None


def get_engine(seed: Optional[int] = None) -> FingerprintEngine:
    """获取默认指纹引擎（单例模式）。"""
    global _default_engine
    if _default_engine is None:
        _default_engine = FingerprintEngine(seed=seed)
    return _default_engine


def generate_fingerprint() -> dict:
    """快速生成一套指纹。"""
    return get_engine().generate()
