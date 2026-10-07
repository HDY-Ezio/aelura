#!/usr/bin/env python3
"""
Aelura 行为模拟器
模拟真人浏览行为：鼠标轨迹（贝塞尔曲线）、滚动节奏、打字间隔等。
用于 Playwright 浏览器层级的反检测增强。

v2.0: 魔法数字常量化，增加类型注解。
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Tuple, Optional

# ============================================================
# 常量
# ============================================================
MOUSE_STEP_DISTANCE: int = 30           # 鼠标轨迹采样间隔（像素）
SCROLL_BACK_PROBABILITY: float = 0.08   # 滚动过程中回滚的概率
LONG_PAUSE_PROBABILITY: float = 0.1     # 长停顿概率（10%）
LONG_PAUSE_MULTIPLIER_MIN: int = 2      # 长停顿倍率下限
LONG_PAUSE_MULTIPLIER_MAX: int = 4      # 长停顿倍率上限
HESITATION_PROBABILITY: float = 0.05    # 打字犹豫概率
HESITATION_MULTIPLIER_MIN: int = 3      # 犹豫倍率下限
HESITATION_MULTIPLIER_MAX: int = 8      # 犹豫倍率上限
RETURN_TO_TOP_PROBABILITY: float = 0.2  # 浏览结束后回到顶部的概率
QUICK_BURST_PROBABILITY: float = 0.15   # 快速连续滚动概率
LONG_LEAVE_PROBABILITY: float = 0.05    # 长时间离开概率
BASE_TYPE_DELAY_MIN: float = 0.05       # 基础打字延迟下限（秒）
BASE_TYPE_DELAY_MAX: float = 0.2        # 基础打字延迟上限（秒）
SENTENCE_START_PAUSE_MIN: float = 0.3   # 句首停顿下限
SENTENCE_START_PAUSE_MAX: float = 0.6   # 句首停顿上限
SCROLL_PX_PER_STEP_MIN: int = 100       # 每步滚动像素下限
SCROLL_PX_PER_STEP_MAX: int = 500       # 每步滚动像素上限


def _gauss_noise(mu: float = 0.0, sigma: float = 1.0) -> float:
    """高斯噪声。"""
    return random.gauss(mu, sigma)


def _quadratic_bezier(p0: Tuple[float, float],
                      p1: Tuple[float, float],
                      p2: Tuple[float, float],
                      t: float) -> Tuple[float, float]:
    """二次贝塞尔曲线插值。"""
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
    return (x, y)


def generate_mouse_path(start: Tuple[int, int],
                        end: Tuple[int, int],
                        num_control_points: int = 2) -> List[Tuple[int, int]]:
    """
    生成一条模拟真人鼠标移动的贝塞尔曲线路径。

    Args:
        start: 起点 (x, y)
        end: 终点 (x, y)
        num_control_points: 控制点数量（越多越弯曲）

    Returns:
        路径点列表 [(x, y), ...]
    """
    # 生成随机控制点
    control_points = [start]
    for _ in range(num_control_points):
        cx = (start[0] + end[0]) / 2 + _gauss_noise(0, abs(end[0] - start[0]) * 0.3 + 1)
        cy = (start[1] + end[1]) / 2 + _gauss_noise(0, abs(end[1] - start[1]) * 0.3 + 1)
        control_points.append((cx, cy))
    control_points.append(end)

    # 使用多段贝塞尔拼接，简化为分段线性采样
    path: List[Tuple[int, int]] = []
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    steps = max(int(distance / MOUSE_STEP_DISTANCE), 5)

    for i in range(steps + 1):
        t = i / steps
        # 在控制点序列上做线性插值近似
        if len(control_points) == 3:
            pt = _quadratic_bezier(control_points[0], control_points[1], control_points[2], t)
        else:
            # 多点简化：按 t 在首尾和控制点之间插值
            pt = (
                start[0] + (end[0] - start[0]) * t + _gauss_noise(0, 2),
                start[1] + (end[1] - start[1]) * t + _gauss_noise(0, 2),
            )
        path.append((int(pt[0]), int(pt[1])))

    # 确保终点精确
    if path[-1] != end:
        path.append(end)
    return path


def generate_scroll_sequence(total_height: int = 3000,
                             viewport_height: int = 900) -> List[Tuple[int, float]]:
    """
    生成模拟真人阅读习惯的滚动序列。

    Returns:
        [(scroll_delta_px, pause_seconds), ...]
    """
    sequence: List[Tuple[int, float]] = []
    current_pos = 0

    while current_pos < total_height - viewport_height:
        # 不均匀速度：大部分时候中等滚动，偶尔快速或慢速
        if random.random() < QUICK_BURST_PROBABILITY:
            scroll_px = random.randint(SCROLL_PX_PER_STEP_MAX, SCROLL_PX_PER_STEP_MAX * 2)
        else:
            scroll_px = random.randint(SCROLL_PX_PER_STEP_MIN, SCROLL_PX_PER_STEP_MAX)

        # 偶尔回滚（模拟回看）
        if random.random() < SCROLL_BACK_PROBABILITY and current_pos > 200:
            scroll_px = -random.randint(50, 200)
        
        current_pos = max(0, min(current_pos + scroll_px, total_height - viewport_height))
        
        # 停顿时间：模拟阅读速度
        base_pause = random.uniform(0.5, 3.0)
        # 10% 概率出现长停顿（去倒水、看手机等）
        if random.random() < LONG_PAUSE_PROBABILITY:
            base_pause *= random.uniform(LONG_PAUSE_MULTIPLIER_MIN, LONG_PAUSE_MULTIPLIER_MAX)
        
        sequence.append((scroll_px, base_pause))

        if current_pos >= total_height - viewport_height:
            break

    return sequence


def generate_typing_rhythm(text: str) -> List[Tuple[str, float]]:
    """
    模拟真人打字节奏。

    Returns:
        [(char, delay_seconds), ...]
    """
    result: List[Tuple[str, float]] = []
    sentence_starters = {'.', '!', '?', '。', '！', '？', '\n'}

    for i, char in enumerate(text):
        delay = random.uniform(BASE_TYPE_DELAY_MIN, BASE_TYPE_DELAY_MAX)

        # 句首停顿更长
        if i > 0 and text[i - 1] in sentence_starters:
            delay += random.uniform(SENTENCE_START_PAUSE_MIN, SENTENCE_START_PAUSE_MAX)

        # 5% 概率犹豫
        if random.random() < HESITATION_PROBABILITY:
            delay *= random.uniform(HESITATION_MULTIPLIER_MIN, HESITATION_MULTIPLIER_MAX)

        result.append((char, delay))

    return result


def simulate_page_browsing(page, total_height: int = 3000) -> None:
    """
    模拟一次完整的页面浏览行为（滚动 + 停顿 + 偶尔回滚）。

    Args:
        page: Playwright Page 对象
        total_height: 页面总高度（像素）
    """
    viewport_height = 900
    try:
        viewport_height = page.viewport_size.get("height", 900) if page.viewport_size else 900
    except Exception:
        pass

    sequence = generate_scroll_sequence(total_height, viewport_height)

    for scroll_px, pause in sequence:
        try:
            page.mouse.wheel(0, scroll_px)
        except Exception:
            pass
        time.sleep(pause)

    # 浏览结束后：20% 概率回到顶部，5% 概率长时间停留后离开
    if random.random() < RETURN_TO_TOP_PROBABILITY:
        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        time.sleep(random.uniform(0.5, 2.0))

    if random.random() < LONG_LEAVE_PROBABILITY:
        time.sleep(random.uniform(5.0, 15.0))
