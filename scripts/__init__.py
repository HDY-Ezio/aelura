"""
Aelura · 黑豹跳蛛 — 轻量化全功能网页抓取框架

模块化架构：
  - utils.py            公共工具（导入辅助、SSRF 防护、配置加载）
  - fingerprint_engine.py  指纹轮换引擎（50+ UA、地理一致性）
  - behavior_simulator.py  行为模拟器（贝塞尔鼠标、不均匀滚动）
  - free_proxy_pool.py   免费代理池（3 数据源、SQLite 存储）
  - enhancer.py          增强模块（限速/增量/重试/Sitemap/缓存/robots）
  - web_scrape.py        主入口（6 级智能降级链）
"""
