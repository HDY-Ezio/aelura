#!/usr/bin/env python3
"""
指纹轮换引擎 - 纯Python，零外部API
生成随机浏览器指纹，每次请求用不同的身份
覆盖：UA、屏幕分辨率、语言、时区、Canvas指纹、WebGL、平台
"""

import random
import time

# ============================================================
# 指纹池（50+ 真实组合）
# ============================================================

USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Chrome Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

# 地理一致性配置：(时区, 语言, 平台)
GEO_PROFILES = [
    ("Asia/Shanghai", "zh-CN,zh;q=0.9,en;q=0.8", "Win32"),
    ("Asia/Shanghai", "zh-CN,zh;q=0.9", "Win32"),
    ("Asia/Hong_Kong", "zh-TW,zh;q=0.9,en;q=0.8", "Win32"),
    ("America/New_York", "en-US,en;q=0.9", "Win32"),
    ("America/Los_Angeles", "en-US,en;q=0.9", "MacIntel"),
    ("Europe/London", "en-GB,en;q=0.9", "Win32"),
    ("Europe/Berlin", "de-DE,de;q=0.9,en;q=0.8", "Win32"),
    ("Asia/Tokyo", "ja-JP,ja;q=0.9,en;q=0.8", "Win32"),
]

SCREEN_RESOLUTIONS = [
    (1920, 1080), (1920, 1200), (2560, 1440), (3840, 2160),
    (1366, 768), (1440, 900), (1536, 864), (1680, 1050),
    (1280, 720), (1280, 800), (1600, 900), (2560, 1600),
    # 移动端
    (390, 844), (375, 812), (414, 896), (360, 800), (412, 915),
]

VIEWPORT_SIZES = [
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
    (1280, 720), (2560, 1440), (1600, 900),
    (390, 844), (375, 812), (414, 896),
]

HARDWARE_CONCURRENCY = [4, 8, 12, 16]
DEVICE_MEMORY = [4, 8, 16]
MAX_TOUCH_POINTS = [0, 5]  # 0=桌面, 5=触屏


class FingerprintEngine:
    """指纹生成引擎 - 每次生成一个完整且一致的浏览器指纹"""

    def __init__(self):
        self._used = set()
        self._max_unique = 200  # 最多200个不重复指纹后重置

    def generate(self) -> dict:
        """生成一个完整的浏览器指纹"""
        # 地理一致性：时区/语言/平台必须匹配
        geo = random.choice(GEO_PROFILES)
        timezone, language, platform = geo

        # 根据平台筛选UA
        if platform == "MacIntel":
            pool = [ua for ua in USER_AGENTS if "Macintosh" in ua]
        elif platform == "Linux":
            pool = [ua for ua in USER_AGENTS if "Linux" in ua]
        else:
            pool = [ua for ua in USER_AGENTS
                    if "Linux" not in ua and "Macintosh" not in ua]

        if not pool:
            pool = USER_AGENTS

        ua = random.choice(pool)

        # 屏幕
        screen_w, screen_h = random.choice(SCREEN_RESOLUTIONS)
        viewport_w, viewport_h = random.choice(VIEWPORT_SIZES)

        # 确保viewport不超过screen
        if viewport_w > screen_w:
            viewport_w = screen_w
        if viewport_h > screen_h:
            viewport_h = screen_h

        # 硬件
        hw_cores = random.choice(HARDWARE_CONCURRENCY)
        device_mem = random.choice(DEVICE_MEMORY)
        touch_points = random.choice(MAX_TOUCH_POINTS)

        # Canvas噪声（模拟不同GPU的渲染差异）
        canvas_noise = round(random.uniform(-0.001, 0.001), 6)

        # WebGL噪声
        webgl_noise = round(random.uniform(-0.002, 0.002), 6)

        fingerprint = {
            "user_agent": ua,
            "platform": platform,
            "timezone": timezone,
            "language": language,
            "screen": {"width": screen_w, "height": screen_h},
            "viewport": {"width": viewport_w, "height": viewport_h},
            "hardware": {
                "cores": hw_cores,
                "memory": device_mem,
                "touch_points": touch_points,
            },
            "canvas_noise": canvas_noise,
            "webgl_noise": webgl_noise,
            "color_depth": random.choice([24, 30]),
            "pixel_ratio": random.choice([1, 1.25, 1.5, 2]),
            "do_not_track": random.choice(
                ["null", "null", "null", "1"]
            ),  # 75%不启用DNT
        }

        # 去重
        fp_key = hash(
            fingerprint["user_agent"]
            + fingerprint["timezone"]
            + str(fingerprint["screen"])
        )
        if fp_key in self._used and len(self._used) < self._max_unique:
            return self.generate()
        self._used.add(fp_key)

        return fingerprint

    def generate_playwright_args(self) -> dict:
        """生成Playwright启动参数"""
        fp = self.generate()
        return {
            "user_agent": fp["user_agent"],
            "viewport": fp["viewport"],
            "locale": fp["language"].split(",")[0],
            "timezone_id": fp["timezone"],
            "screen": fp["screen"],
            "device_scale_factor": fp.get("device_scale_factor", 1),
            "color_scheme": random.choice(
                ["light", "light", "light", "dark"]
            ),  # 75%浅色
        }

    def generate_curl_cffi_kwargs(self) -> dict:
        """生成curl_cffi请求参数"""
        fp = self.generate()
        return {
            "impersonate": random.choice(
                ["chrome120", "chrome131", "chrome124"]
            ),
            "headers": {
                "User-Agent": fp["user_agent"],
                "Accept-Language": fp["language"],
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,image/webp,*/*;q=0.8"
                ),
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": fp["do_not_track"],
            },
        }

    def stats(self) -> dict:
        """返回使用统计"""
        return {
            "used_count": len(self._used),
            "max_unique": self._max_unique,
        }

    def reset(self):
        """重置指纹池"""
        self._used.clear()
