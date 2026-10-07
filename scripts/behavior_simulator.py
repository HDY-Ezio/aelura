#!/usr/bin/env python3
"""
行为模拟器 - 纯Python，零外部API
模拟真人浏览行为：鼠标轨迹、滚动曲线、随机停顿、键盘节奏
用于浏览器自动化时降低被反爬系统检测的概率
"""

import math
import random
import time


def human_pause(min_s=1.0, max_s=3.0) -> float:
    """模拟真人阅读停顿"""
    wait = random.uniform(min_s, max_s)
    # 偶尔出现较长的停顿（像在仔细看某段内容）
    if random.random() < 0.1:
        wait *= random.uniform(2, 4)
    time.sleep(wait)
    return wait


def generate_mouse_path(from_x, from_y, to_x, to_y, steps=None) -> list:
    """
    生成真人鼠标移动轨迹
    使用贝塞尔曲线 + 随机扰动，模拟人手移动的自然弧度
    """
    if steps is None:
        distance = math.sqrt((to_x - from_x) ** 2 + (to_y - from_y) ** 2)
        steps = max(5, int(distance / 30))  # 每30像素一步

    points = []

    # 控制点（产生弧度）
    ctrl_x = (from_x + to_x) / 2 + random.uniform(-100, 100)
    ctrl_y = (from_y + to_y) / 2 + random.uniform(-100, 100)

    for i in range(steps + 1):
        t = i / steps
        # 二次贝塞尔曲线
        x = (1 - t) ** 2 * from_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * to_x
        y = (1 - t) ** 2 * from_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * to_y
        # 添加微小抖动（人手不可能完全精准）
        x += random.gauss(0, 1.5)
        y += random.gauss(0, 1.5)
        points.append((round(x, 1), round(y, 1)))

    return points


def generate_scroll_sequence(page_height, viewport_height, target_ratio=None) -> list:
    """
    生成真人滚动序列
    特征：不均匀速度、偶尔回滚、末端减速
    """
    max_scroll = page_height - viewport_height
    if max_scroll <= 0:
        return [0]

    if target_ratio is None:
        target_ratio = random.uniform(0.6, 1.0)

    target_pos = int(max_scroll * target_ratio)

    scrolls = []
    current_pos = 0
    remaining = target_pos

    while remaining > 5:
        # 每次滚动距离：大多在50-300px之间
        if remaining > 500:
            step = random.randint(100, 400)
        elif remaining > 100:
            step = random.randint(50, 200)
        else:
            step = random.randint(10, remaining)

        # 偶尔回滚（真人经常往回翻一点）
        if random.random() < 0.08 and len(scrolls) > 2:
            step = -random.randint(20, 80)

        current_pos += step
        current_pos = max(0, min(current_pos, max_scroll))
        scrolls.append(current_pos)
        remaining = target_pos - current_pos

        if remaining < 0:
            break

    return scrolls


def generate_typing_rhythm(text: str) -> list:
    """
    生成真人打字节奏（每个字符的延迟，单位毫秒）
    特征：不均匀速度、偶尔停顿思考、纠错回删
    """
    delays = []
    i = 0
    while i < len(text):
        # 基础延迟：50-200ms
        delay = random.randint(50, 200)

        # 句首慢一点
        if i == 0 or (i > 0 and text[i - 1] in "。！？.!?"):
            delay *= random.randint(2, 4)

        # 长词中间可能犹豫
        if random.random() < 0.05:
            delay *= random.randint(3, 8)

        delays.append(delay)
        i += 1

    return delays


def generate_hover_before_click(page) -> None:
    """
    在点击前先hover到目标元素（真人不会直接点）
    需要在Playwright环境中调用
    """
    try:
        page.mouse.move(
            random.randint(100, 800),
            random.randint(100, 600),
            steps=random.randint(5, 15)
        )
        time.sleep(random.uniform(0.3, 1.2))
    except Exception:
        pass


def simulate_page_browsing(page, max_scroll_ratio=0.9) -> None:
    """
    模拟真人浏览页面：进入 → 停顿 → 滚动 → 再停顿 → 可能回滚
    需要在Playwright环境中调用
    """
    try:
        # 1. 页面加载后先看一会
        human_pause(1.5, 3.5)

        # 2. 获取页面高度
        page_height = page.evaluate("document.body.scrollHeight")
        viewport_height = page.evaluate("window.innerHeight")

        if page_height <= viewport_height:
            # 页面太短不需要滚动
            human_pause(1, 2)
            return

        # 3. 生成滚动序列
        scroll_seq = generate_scroll_sequence(
            page_height, viewport_height, max_scroll_ratio
        )

        # 4. 执行滚动
        for pos in scroll_seq:
            page.evaluate(f"window.scrollTo(0, {pos})")
            # 每次滚动后停顿
            human_pause(0.5, 2.0)

        # 5. 偶尔回到顶部再看看
        if random.random() < 0.2:
            page.evaluate("window.scrollTo(0, 0)")
            human_pause(1, 3)

    except Exception:
        # 行为模拟失败不影响数据抓取
        pass


def generate_request_timing(request_count: int) -> list:
    """
    生成一组请求的时间间隔序列
    模拟真人访问多个页面的节奏
    """
    intervals = []
    for _ in range(request_count):
        # 基础间隔 5-25 秒
        interval = random.uniform(5, 25)

        # 偶尔快速连续访问（像在翻页）
        if random.random() < 0.15:
            interval = random.uniform(1, 3)

        # 偶尔长时间离开（像去干别的了）
        if random.random() < 0.05:
            interval = random.uniform(60, 300)

        intervals.append(interval)

    return intervals
