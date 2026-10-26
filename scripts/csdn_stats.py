#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
csdn_stats.py — CSDN 页面解析的**唯一公共实现**

为什么要有这个模块:
  历史上 csdn-monitor-v2.py 和 csdn_incremental.py 各写了一份正则,
  两份都坏过, 而且坏法不同:
    - v2 用 `<span class="read">N</span>` x4 当作 [收藏, 点赞, 评论, 推荐]。
      实测 (2026-10-21) 这些 span 全部来自页面右侧 **"相关推荐/热门文章" 侧边栏**
      (前面跟着 readCountWhite.png 图片), 数值是**别人文章的阅读量**。
      结果: 李瑞的文章 168415428 被写上 collections=4958 / likes=677 ——
      全是张冠李戴的脏数据。
    - incremental 用 `class="read-count"` / `like-count` / `comment-count` /
      `collect-count`。实测: read-count 只在 1/3 的页面上出现, 另外三个正则
      **一次都没匹配上**。

实测可用性 (2026-10-21, 12 篇文章跨 12 个学生):
    <meta name="description"> 文章浏览阅读N次，点赞N次，收藏N次   views 12/12 ✅
    var viewCountFormat = N                                        views 12/12 ✅
    <span class="read-count">N 阅读</span>                          views  8/12
    <span class="read-count" id="blog-digg-num">N</span>            likes  8/12
    <span id="spanCount" class="count">N</span>                    likes  8/12
    id="get-collection" ... data-num="N"                        collections  8/12
    var commentscount = N                                        comments 12/12

结论: **views 用 meta description / viewCountFormat 最稳** (12/12 一致),
点赞/收藏用 span + meta description 互补 (两个来源各有约 8/12 和 4/12 命中,
合起来覆盖全部 12/12)。

只放纯函数, 不放 HTTP, 方便两个脚本共用 + 方便单测。
"""
from __future__ import annotations

import random
import re
import threading
import time

__all__ = [
    "parse_article_stats",
    "parse_profile_views",
    "article_id_from_link",
    "RateLimiter",
    "STATS_FIELDS",
]

STATS_FIELDS = ("views", "likes", "comments", "collections")


def _to_int(raw: str | None) -> int | None:
    """'7,272' -> 7272; None/'' -> None"""
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d]", "", raw)
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def article_id_from_link(link: str | None) -> str | None:
    """从文章 URL 里抽 article id: .../article/details/168415428 -> '168415428'"""
    if not link:
        return None
    m = re.search(r"/article/details/(\d+)", link)
    return m.group(1) if m else None


# ---------- 文章统计 ----------
# meta description 是最可靠的一个: CSDN 自己生成, 三个数字连在一起, 顺序固定
_META_DESC_RE = re.compile(
    r'<meta\s+name="description"\s+content="([^"]*)"', re.IGNORECASE)
_OG_DESC_RE = re.compile(
    r'<meta\s+property="og:description"\s+content="([^"]*)"', re.IGNORECASE)
_ARTICLE_DESC_VAR_RE = re.compile(r'articleDesc\s*=\s*"([^"]*)"')

_VIEWS_IN_TEXT_RE = re.compile(r"文章浏览阅读\s*([\d,]+)\s*次")
_LIKES_IN_TEXT_RE = re.compile(r"点赞\s*([\d,]+)\s*次")
_COLLECTIONS_IN_TEXT_RE = re.compile(r"收藏\s*([\d,]+)\s*次")
_COMMENTS_IN_TEXT_RE = re.compile(r"评论\s*([\d,]+)\s*次")


def _parse_stats_text(text: str) -> dict:
    """从 '文章浏览阅读155次，点赞2次，收藏2次' 这种文本里抽四个数"""
    out = {"views": None, "likes": None, "comments": None, "collections": None}
    if not text:
        return out
    m = _VIEWS_IN_TEXT_RE.search(text)
    if m:
        out["views"] = _to_int(m.group(1))
    m = _LIKES_IN_TEXT_RE.search(text)
    if m:
        out["likes"] = _to_int(m.group(1))
    m = _COLLECTIONS_IN_TEXT_RE.search(text)
    if m:
        out["collections"] = _to_int(m.group(1))
    m = _COMMENTS_IN_TEXT_RE.search(text)
    if m:
        out["comments"] = _to_int(m.group(1))
    return out


_VIEW_COUNT_FORMAT_RE = re.compile(r"viewCountFormat\s*=\s*(\d+)")
_READ_COUNT_SPAN_RE = re.compile(r'<span class="read-count"[^>]*>\s*([\d,]+)')
_DIGG_NUM_RE = re.compile(r'id="blog-digg-num"[^>]*>\s*([\d,]+)')
_SPAN_COUNT_RE = re.compile(r'id="spanCount"[^>]*>\s*([\d,]+)')
_GET_COLLECTION_ID_RE = re.compile(r'id="get-collection"[^>]*data-num="([\d,]+)"')
_GET_COLLECTION_DATA_RE = re.compile(r'data-num="([\d,]+)"[^>]*id="get-collection"')
_GET_COLLECTION_SPAN_RE = re.compile(r'<span class="get-collection"[^>]*>\s*([\d,]+)')
_COMMENTS_COUNT_VAR_RE = re.compile(r"commentscount\s*=\s*(\d+)")

# ★ 2026-10-26 新增: CSDN 文章页实际把评论数放在这几个地方 (meta description 里没有)。
#   按可靠性从高到低排, parse_article_stats 依次尝试。
#   - commentCount:  CSDN 前端 JS 的全局计数变量 (形如 commentCount: 3 / commentCount = 3)
#   - title="N 条评论": 评论区标题
#   - commentId=...&...: 评论锚点, 只能说明"有评论", 拿不到总数时兜底为 1 不可靠 → 不用
_COMMENT_COUNT_JSON_RE = re.compile(r'commentCount["\']?\s*[:=]\s*["\']?(\d+)')
_COMMENT_TITLE_RE = re.compile(r'title="(\d+)\s*条评论"')
_COMMENT_ID_RE = re.compile(r'id="commentCount"[^>]*>\s*(\d+)')


def parse_article_stats(html: str | None) -> dict:
    """从文章详情页 HTML 里解析 views/likes/comments/collections。

    返回 dict, 每个 key 要么是 int 要么是 None (None = 这个页面没拿到,
    绝不返回 0 去冒充 "真的是 0 次" —— 那是把抓取失败当数据的经典错误)。
    """
    out = {"views": None, "likes": None, "comments": None, "collections": None}
    if not html:
        return out

    # --- meta description: 一次拿到三个 ---
    text = None
    for rx in (_META_DESC_RE, _OG_DESC_RE):
        m = rx.search(html)
        if m:
            text = m.group(1)
            break
    if text is None:
        m = _ARTICLE_DESC_VAR_RE.search(html)
        if m:
            text = m.group(1)
    meta_stats = _parse_stats_text(text)

    # --- views: meta description -> viewCountFormat -> 全文 -> read-count span ---
    if meta_stats["views"] is not None:
        out["views"] = meta_stats["views"]
    else:
        m = _VIEW_COUNT_FORMAT_RE.search(html)
        if m:
            out["views"] = _to_int(m.group(1))
    if out["views"] is None:
        m = _VIEWS_IN_TEXT_RE.search(html)
        if m:
            out["views"] = _to_int(m.group(1))
    if out["views"] is None:
        m = _READ_COUNT_SPAN_RE.search(html)
        if m:
            out["views"] = _to_int(m.group(1))

    # --- likes: blog-digg-num -> spanCount -> meta description ---
    for rx in (_DIGG_NUM_RE, _SPAN_COUNT_RE):
        m = rx.search(html)
        if m:
            out["likes"] = _to_int(m.group(1))
            break
    if out["likes"] is None:
        out["likes"] = meta_stats["likes"]

    # --- collections: get-collection (data-num / span) -> meta description ---
    for rx in (_GET_COLLECTION_ID_RE, _GET_COLLECTION_DATA_RE, _GET_COLLECTION_SPAN_RE):
        m = rx.search(html)
        if m:
            out["collections"] = _to_int(m.group(1))
            break
    if out["collections"] is None:
        out["collections"] = meta_stats["collections"]

    # --- comments: commentscount 变量 -> commentCount -> DOM 计数 -> meta description ---
    # ★ 2026-10-26 调查记录（别再把结论搞错）:
    #   抓真实页面确认 CSDN 的 meta description 原文是
    #     "文章浏览阅读155次，点赞2次，收藏2次"  —— 里面没有"评论"字段;
    #   但 `<span class="count">0</span>&nbsp;条评论` 和 `var commentscount = 0`
    #   都真实存在, 说明 commentscount 一直是有效来源, 现有数据里的 0 是**真的 0**。
    #   这里补三个备用来源, 只为防 CSDN 改版; 仍然遵守"拿不到就 None, 绝不编 0"。
    for rx in (_COMMENTS_COUNT_VAR_RE,
               _COMMENT_COUNT_JSON_RE,
               _COMMENT_TITLE_RE,
               _COMMENT_ID_RE):
        m = rx.search(html)
        if m:
            out["comments"] = _to_int(m.group(1))
            break
    if out["comments"] is None:
        out["comments"] = meta_stats["comments"]

    return out


# ---------- 个人主页总访问量 ----------
# 实测 DOM (2026-10-21):
#   <span title="" class="user-profile-statistics-views" data-v-f8205402>
#     <div class="user-profile-statistics-num" data-v-f8205402>7,272</div>
#     <div class="user-profile-statistics-name" data-v-f8205402>总访问量</div>
#   </span>
_PROFILE_VIEWS_EXACT_RE = re.compile(
    r'user-profile-statistics-views[^>]*>\s*<div[^>]*user-profile-statistics-num[^>]*>\s*([\d,]+)\s*<',
    re.DOTALL,
)
# 中等: "总访问量" 前后 200 字符内找最近的数字 (数字可能在标签前)
_PROFILE_VIEWS_NEAR_RE = re.compile(
    r'([\d,]{1,12})[^0-9]{0,200}总访问量|总访问量[^0-9]{0,200}([\d,]{1,12})',
    re.DOTALL,
)
# 宽松: 双向
_PROFILE_VIEWS_LOOSE_RE = re.compile(
    r'总访问量[^0-9]{0,200}([\d,]+)|([\d,]+)[^0-9]{0,200}总访问量',
    re.DOTALL,
)


def parse_profile_views(html: str | None) -> int | None:
    """解析个人主页的"总访问量"。

    三层兜底 (从精确到宽松):
      1. 精确: 定位 user-profile-statistics-views 容器, 内部找 num
      2. 中等: "总访问量" 前后 200 字符内找最近的数字 (数字可能在标签前)
      3. 宽松: 总访问量 与 数字 双向匹配

    **失败返回 None, 不返回 0** —— 0 和 "没抓到" 必须能区分, 否则上层会把
    抓取失败当成 "这个人真的 0 阅读", 直接把上一次的好数据覆盖成 0。
    """
    if not html:
        return None

    m = _PROFILE_VIEWS_EXACT_RE.search(html)
    if m:
        val = _to_int(m.group(1))
        if val is not None:
            return val

    m = _PROFILE_VIEWS_NEAR_RE.search(html)
    if m:
        val = _to_int(m.group(1) or m.group(2))
        if val is not None:
            return val

    m = _PROFILE_VIEWS_LOOSE_RE.search(html)
    if m:
        val = _to_int(m.group(1) or m.group(2))
        if val is not None:
            return val

    return None


# ---------- 限流 ----------
class RateLimiter:
    """全局请求节流器 (线程安全)。

    实测 (2026-10-21, 同一 IP):
      - 不加延迟连打 40 篇文章 -> 第 17 篇开始大量 **HTTP 521** (CSDN WAF)
      - 间隔 3.0s 连打 30 篇     -> 30/30 全部 200, 一个 521 都没有
    521 是 CSDN 的反爬响应, 不是 404/500, 所以必须显式识别。

    线程数越多不一定越快 —— 被 521 之后重试反而更慢 (v2 曾经因此跑了 627 秒)。
    """
    def __init__(self, min_interval: float = 2.5, jitter: float = 0.8,
                 max_interval: float = 9.0):
        self.min_interval = min_interval      # 基准间隔 (最快能有多快)
        self.max_interval = max_interval      # 退避上限
        self.jitter = jitter
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self.waits = 0
        self.escalations = 0
        # ★ 自适应: 撞到风控就自动拉长间隔, 连续顺利再慢慢收回来。
        #   固定间隔的问题是: CSDN 的风控会**随着请求量收紧**。
        #   2026-10-21 实测全量跑: 固定 2.5s, 前半程还好, 后半程 521 越来越密,
        #   每次 521 都要 5s+10s 冷却, 整个 CSDN 阶段被拖到 34.6 分钟。
        #   自适应退避能让它自己找到当下真正安全的速率。
        self._current = min_interval

    @property
    def current_interval(self) -> float:
        return self._current

    def wait(self) -> float:
        """阻塞到可以发下一个请求为止, 返回实际等待秒数"""
        with self._lock:
            now = time.monotonic()
            target = max(now, self._next_allowed)
            self._next_allowed = target + self._current + random.uniform(0, self.jitter)
        delay = target - now
        if delay > 0:
            self.waits += 1
            time.sleep(delay)
            return delay
        return 0.0

    def penalize(self, seconds: float = 5.0) -> None:
        """遇到 521/429 时, 给全局加一个冷却窗口 (惩罚所有人, 因为瓶颈是 IP)"""
        with self._lock:
            self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)

    def escalate(self, factor: float = 1.5) -> float:
        """撞到风控: 把基准间隔拉长 (最多到 max_interval)"""
        with self._lock:
            self._current = min(self.max_interval, self._current * factor)
            self.escalations += 1
            return self._current

    def decay(self, factor: float = 0.94) -> float:
        """连续顺利: 慢慢把间隔收回去 (只收到基准间隔为止)"""
        with self._lock:
            if self._current > self.min_interval:
                self._current = max(self.min_interval, self._current * factor)
            return self._current

    def stats(self) -> dict:
        return {"waits": self.waits, "min_interval": self.min_interval,
                "current_interval": round(self._current, 2),
                "max_interval": self.max_interval, "jitter": self.jitter,
                "escalations": self.escalations}
