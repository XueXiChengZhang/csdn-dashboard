#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_csdn_stats.py — 解析 + 保护逻辑的自检

跑法:
  python scripts/test_csdn_stats.py            # 全部 (含联网自检)
  python scripts/test_csdn_stats.py --offline  # 只跑离线单测

包含:
  1. 离线: profile 解析的三层兜底 (精确 / 中等 / 宽松)
  2. 离线: 文章 stats 解析 (meta description / viewCountFormat / span)
  3. 离线: **保护逻辑单测** —— 构造"stats 全失败"场景,
     断言 data.json 的 posts 和 total_views 保持不变
  4. 联网: 真实主页断言能取到 > 0
  5. 联网: 真实文章断言 views 能取到, 且与 viewCountFormat 一致
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from csdn_stats import parse_article_stats, parse_profile_views  # noqa: E402

PASS = 0
FAIL = 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}  ({detail})")
    RESULTS.append((name, bool(cond), detail))


def load_v2():
    spec = importlib.util.spec_from_file_location("v2mod", SCRIPT_DIR / "csdn-monitor-v2.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


# ---------- 1. profile 解析三层兜底 ----------
def test_profile_parsing():
    print("\n[1] profile 总访问量 —— 三层兜底")

    # 第 1 层: 精确 (2026-10-21 真实 DOM 结构)
    exact = ('<span title="" class="user-profile-statistics-views" data-v-f8205402>'
             '<div class="user-profile-statistics-num" data-v-f8205402>7,272</div>'
             '<div class="user-profile-statistics-name" data-v-f8205402>总访问量</div>'
             '</span>')
    check("第1层 精确容器", parse_profile_views(exact) == 7272,
          str(parse_profile_views(exact)))

    # 第 2 层: 数字在标签**前** (结构变了, 容器 class 也变了)
    near = '<div class="stats-num">4,096</div><span>总访问量</span>'
    check("第2层 数字在标签前", parse_profile_views(near) == 4096,
          str(parse_profile_views(near)))

    # 第 3 层: 数字在标签**后**, 中间隔了一堆非数字
    loose = '<span>总访问量</span> <div class="x">1,234</div>'
    check("第3层 宽松双向", parse_profile_views(loose) == 1234,
          str(parse_profile_views(loose)))

    # 失败必须返回 None 而不是 0
    check("失败返回 None (不是 0)",
          parse_profile_views("<html><body>没有这个标签</body></html>") is None,
          repr(parse_profile_views("<html><body>没有这个标签</body></html>")))
    check("空 HTML 返回 None", parse_profile_views("") is None)

    # 关键: 0 是合法值 (真的是 0 访问量), 不能被当成 None
    zero = ('<span class="user-profile-statistics-views">'
            '<div class="user-profile-statistics-num">0</div>'
            '<div class="user-profile-statistics-name">总访问量</div></span>')
    check("真的 0 也返回 0 (不是 None)", parse_profile_views(zero) == 0,
          repr(parse_profile_views(zero)))


# ---------- 2. 文章 stats 解析 ----------
def test_article_parsing():
    print("\n[2] 文章 stats 解析")

    # 真实页面片段 (2026-10-21)
    html = ('<meta name="description" content="文章浏览阅读155次，点赞2次，收藏2次。'
            '本文记录...">'
            '<script>var viewCountFormat = 155; var commentscount = 3;</script>'
            '<span class="read-count" id="blog-digg-num">2</span>'
            '<span class="count get-collection " data-num="2" id="get-collection">')

    st = parse_article_stats(html)
    check("views 来自 meta description", st["views"] == 155, str(st["views"]))
    check("likes 来自 meta description", st["likes"] == 2, str(st["likes"]))
    check("collections 来自 meta description", st["collections"] == 2, str(st["collections"]))
    check("comments 来自 commentscount", st["comments"] == 3, str(st["comments"]))

    # 没有 meta description 时退回 viewCountFormat
    html2 = ('<script>var viewCountFormat = 208; var commentscount = 0;</script>')
    st2 = parse_article_stats(html2)
    check("views 退回 viewCountFormat", st2["views"] == 208, str(st2["views"]))

    # 只有 read-count span
    html3 = '<span class="read-count">136 阅读</span>'
    st3 = parse_article_stats(html3)
    check("views 退回 read-count span", st3["views"] == 136, str(st3["views"]))

    # ★ 侧边栏的 <span class="read"> 必须被忽略 (那是别人文章的阅读量)
    html4 = ('<script>var viewCountFormat = 135;</script>'
             '<span class="read">4958</span><span class="read">677</span>'
             '<span class="read">234</span><span class="read">216</span>')
    st4 = parse_article_stats(html4)
    check("忽略侧边栏 span.read (不污染 likes/collections)",
          st4["likes"] is None and st4["collections"] is None,
          f"likes={st4['likes']} collections={st4['collections']}")
    check("忽略侧边栏后 views 仍是本文的", st4["views"] == 135, str(st4["views"]))

    # ★ meta description 里的阅读量有时是缩写形式 "文章浏览阅读4.9k次"
    #   这时正则不能误匹配成 "4", 必须退回 viewCountFormat 拿精确值
    html5 = ('<meta name="description" content="文章浏览阅读4.9k次，点赞20次，收藏23次。">'
             '<script>var viewCountFormat = 4959;</script>')
    st5 = parse_article_stats(html5)
    check("meta 用缩写形式 (4.9k次) 时退回 viewCountFormat 拿精确值",
          st5["views"] == 4959, str(st5["views"]))
    check("缩写形式下 likes/collections 仍从 meta description 拿到",
          st5["likes"] == 20 and st5["collections"] == 23,
          f"likes={st5['likes']} collections={st5['collections']}")

    # 空页面不能返回假的 0
    st5 = parse_article_stats("")
    check("空 HTML 全部返回 None", all(v is None for v in st5.values()), str(st5))


# ---------- 3. 保护逻辑单测 ----------
def test_protection():
    print("\n[3] 保护逻辑单测 —— 构造'stats 全失败'场景")
    v2 = load_v2()

    # --- 构造上一轮的好数据 ---
    prev = {
        "total_posts": 2,
        "students": [
            {"sid": "s1", "name": "甲", "total_views": 5000, "total_likes": 10,
             "total_comments": 3, "total_collections": 7,
             "articles_scanned": 12, "profile_views": 5000,
             "article_views_sum": 480, "views_source": "profile",
             "stats_updated_at": "2026-10-21T00:00:00",
             "posts": [
                 {"title": "文章1", "link": "https://blog.csdn.net/a/article/details/1",
                  "pubDate": "2026-10-20T08:00:00", "guid": "g1",
                  "views": 100, "likes": 2, "comments": 1, "collections": 3,
                  "stats_updated_at": "2026-10-21T00:00:00"},
                 {"title": "文章2", "link": "https://blog.csdn.net/a/article/details/2",
                  "pubDate": "2026-10-19T08:00:00", "guid": "g2",
                  "views": 200, "likes": 3, "comments": 2, "collections": 4,
                  "stats_updated_at": "2026-10-21T00:00:00"},
             ]},
            {"sid": "s2", "name": "乙", "total_views": 3000, "articles_scanned": 8,
             "profile_views": 3000, "article_views_sum": 280, "views_source": "profile",
             "posts": [
                 {"title": "文章3", "link": "https://blog.csdn.net/b/article/details/3",
                  "pubDate": "2026-10-18T08:00:00", "guid": "g3",
                  "views": 300, "likes": 1, "comments": 0, "collections": 2,
                  "stats_updated_at": "2026-10-21T00:00:00"},
             ]},
        ],
    }

    # --- 场景 1: 列表全成功, 但文章详情全被限流 (就是 2026-10-21 05:30 那个情况) ---
    students_state = [
        {"sid": "s1", "name": "甲", "csdn_status": "ok", "csdn_username": "a",
         "posts": [{"title": "文章1", "link": "https://blog.csdn.net/a/article/details/1",
                    "pubDate": "2026-10-20T08:00:00", "guid": "g1"},
                   {"title": "文章2", "link": "https://blog.csdn.net/a/article/details/2",
                    "pubDate": "2026-10-19T08:00:00", "guid": "g2"}],
         "stats": {"total_views": 0, "articles_scanned": 0}},
        {"sid": "s2", "name": "乙", "csdn_status": "ok", "csdn_username": "b",
         "posts": [{"title": "文章3", "link": "https://blog.csdn.net/b/article/details/3",
                    "pubDate": "2026-10-18T08:00:00", "guid": "g3"}],
         "stats": {"total_views": 0, "articles_scanned": 0}},
    ]

    triggered = v2.apply_data_protection(students_state, prev, verbose=False)
    check("stats 全失败时触发保护", triggered is True)

    # posts 必须保留**整个数组** (含 stats 字段), 否则前端会显示 "—"
    s1 = students_state[0]
    check("posts 数组被完整保留 (2 篇)", len(s1["posts"]) == 2, str(len(s1["posts"])))
    check("post 保留 views", s1["posts"][0].get("views") == 100,
          str(s1["posts"][0].get("views")))
    check("post 保留 stats_updated_at",
          s1["posts"][0].get("stats_updated_at") == "2026-10-21T00:00:00",
          str(s1["posts"][0].get("stats_updated_at")))
    check("total_views 保留上次值 (5000)",
          s1["stats"]["total_views"] == 5000, str(s1["stats"]["total_views"]))
    check("第二个学生也保住",
          students_state[1]["stats"]["total_views"] == 3000,
          str(students_state[1]["stats"]["total_views"]))

    # --- 场景 2: 一切正常, 不应该触发 ---
    good_state = [
        {"sid": "s1", "name": "甲", "csdn_status": "ok", "csdn_username": "a",
         "posts": [{"title": "文章1", "link": "https://blog.csdn.net/a/article/details/1"}],
         "stats": {"total_views": 5000, "articles_scanned": 12}},
        {"sid": "s2", "name": "乙", "csdn_status": "ok", "csdn_username": "b",
         "posts": [{"title": "文章3", "link": "https://blog.csdn.net/b/article/details/3"}],
         "stats": {"total_views": 3000, "articles_scanned": 8}},
    ]
    triggered2 = v2.apply_data_protection(good_state, prev, verbose=False)
    check("正常时不触发保护", triggered2 is False)
    check("正常时 total_views 不被改",
          good_state[0]["stats"]["total_views"] == 5000)

    # --- 场景 3: 阅读量塌方但列表正常 (只塌了 views) ---
    drop_state = [
        {"sid": "s1", "name": "甲", "csdn_status": "ok", "csdn_username": "a",
         "posts": [{"title": "文章1", "link": "https://blog.csdn.net/a/article/details/1"}],
         "stats": {"total_views": 100, "articles_scanned": 12}},
        {"sid": "s2", "name": "乙", "csdn_status": "ok", "csdn_username": "b",
         "posts": [{"title": "文章3", "link": "https://blog.csdn.net/b/article/details/3"}],
         "stats": {"total_views": 50, "articles_scanned": 8}},
    ]
    triggered3 = v2.apply_data_protection(drop_state, prev, verbose=False)
    check("阅读量塌方 (8000 -> 150) 触发保护", triggered3 is True)
    check("塌方时 total_views 回到上次值",
          drop_state[0]["stats"]["total_views"] == 5000,
          str(drop_state[0]["stats"]["total_views"]))


# ---------- 4/5. 联网自检 ----------
def test_live():
    print("\n[4] 联网自检 —— 真实主页")
    import requests
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
    H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
         "Referer": "https://blog.csdn.net/"}

    for u in ["Pxh_sky_998", "liruisklfj", "pjl20051011"]:
        try:
            r = requests.get(f"https://blog.csdn.net/{u}?type=blog", headers=H, timeout=20)
            val = parse_profile_views(r.text)
            check(f"主页 {u} 总访问量 > 0",
                  r.status_code == 200 and val is not None and val > 0,
                  f"HTTP {r.status_code} len={len(r.text or '')} val={val}")
        except Exception as e:
            check(f"主页 {u} 总访问量 > 0", False, f"{type(e).__name__}: {e}")
        time.sleep(2.0)

    print("\n[5] 联网自检 —— 真实文章 (views 能取到且与 viewCountFormat 一致)")
    data_path = DASHBOARD_DIR / "data.json"
    if not data_path.exists():
        check("data.json 存在", False, str(data_path))
        return
    data = json.loads(data_path.read_text(encoding="utf-8"))
    links = []
    for s in data.get("students", []):
        for p in s.get("posts", []):
            links.append(p.get("link"))
    sample = links[:5]
    for link in sample:
        try:
            r = requests.get(link, headers=H, timeout=20)
            st = parse_article_stats(r.text)
            m = re.search(r"viewCountFormat\s*=\s*(\d+)", r.text or "")
            vcf = int(m.group(1)) if m else None
            ok = st["views"] is not None and st["views"] > 0
            agree = (vcf is None) or (st["views"] == vcf)
            check(f"文章 {link.rsplit('/', 1)[-1]} views>0 且与 viewCountFormat 一致",
                  ok and agree,
                  f"parsed={st['views']} viewCountFormat={vcf}")
        except Exception as e:
            check(f"文章 {link.rsplit('/', 1)[-1]} 抓取", False,
                  f"{type(e).__name__}: {e}")
        time.sleep(2.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="跳过联网自检")
    args = ap.parse_args()

    print("=" * 70)
    print("csdn_stats 自检")
    print("=" * 70)

    test_profile_parsing()
    test_article_parsing()
    test_protection()
    if not args.offline:
        test_live()
    else:
        print("\n[4][5] 联网自检已跳过 (--offline)")

    print("\n" + "=" * 70)
    print(f"通过 {PASS} / 失败 {FAIL}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
