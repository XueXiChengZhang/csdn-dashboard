#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_csdn_fixes.py — 2026-10-22 第二轮修复的回归测试

覆盖 (每个都对应一个已提交的修复):
  [1] R1  last_fetch_meta 按模式分块 (增量块 / gitee 块互不覆盖)
  [2] R1  日报"抓取健康度"行如实显示 (不再出现"成功 66/0")
  [3] Gitee 空仓库 vs 有 commit 的判定 (d280330 补测试)
  [4] R2  模拟 521: 两次尝试间隔 >= 45 秒 (默认冷却 60s)
  [5] R3  模拟 404: 文章被标记 _deleted_on_csdn 且不再重试
  [6] R4  列表抓取: no_posts (真没文章) vs fetch_failed (被风控)
  [7] R5  增量跑完重算 class_stats
  [8] R7  周日全量安全网: 备份 / 校验 / 回滚 (总阅读腰斩 + 部分学生失败)
  [9] R7  05:30 gitee-only 之后, 班级总阅读保持 00:00 增量算出的值

跑法:
  python scripts/test_csdn_fixes.py            # 全部 (离线, 不打真网站)
  python scripts/test_csdn_fixes.py --offline  # 同上 (本测试全是离线的)

★ 本文件**不联网**: 所有 HTTP 都被 mock 掉了。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}  ({detail})")
    return bool(cond)


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def load_v2():
    return load_module("v2mod", "csdn-monitor-v2.py")


def load_incremental():
    return load_module("incmod", "csdn_incremental.py")


class FakeResp:
    """假的 requests.Response"""

    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


# ---------- [1] R1: last_fetch_meta 按模式分块 ----------
def test_fetch_meta_blocks():
    print("\n[1] R1 — last_fetch_meta 按模式分块 (增量块 / gitee 块互不覆盖)")
    v2 = load_v2()

    students = [
        {"sid": "s1", "name": "甲", "csdn_status": "ok", "posts": [],
         "stats": {"total_views": 100},
         "gitee": {"ok": True, "status": "ok", "commit_count": 2, "stars": 1,
                   "forks": 0, "source": "api"}},
        {"sid": "s2", "name": "乙", "csdn_status": "skipped_reused", "posts": [],
         "stats": {"total_views": 50},
         "gitee": {"ok": False, "status": "empty", "commit_count": 0, "stars": 0,
                   "forks": 0, "source": "api"}},
    ]
    # 上一次增量跑出来的 meta (438 个请求) —— gitee-only 之后必须还在
    prev_meta = {
        "fetched_at": "2026-10-22T00:24:00",
        "incremental": {"fetched_at": "2026-10-22T00:24:00",
                        "total_requests": 438, "students_ok": 33,
                        "students_total": 34},
    }
    v2.GITEE_REQUESTS = 66

    meta = v2.build_fetch_meta(students, prev_meta, run_csdn=False, run_gitee=True,
                                csdn_ms=0, gitee_ms=1234, total_ms=1234)
    check("gitee-only 之后 incremental 块还在 (438 没被抹掉)",
          (meta.get("incremental") or {}).get("total_requests") == 438,
          str((meta.get("incremental") or {}).get("total_requests")))
    check("gitee 块被写进去了",
          (meta.get("gitee") or {}).get("total_requests") == 66,
          str((meta.get("gitee") or {}).get("total_requests")))
    check("顶层 total_requests = 两块之和",
          meta.get("total_requests") == 438 + 66, str(meta.get("total_requests")))
    check("顶层 fetched_at = 本次运行时间",
          meta.get("fetched_at") == v2.NOW_ISO, str(meta.get("fetched_at")))
    check("last_run.csdn_ran == False (这次没抓 CSDN)",
          meta["last_run"]["csdn_ran"] is False)
    check("last_run.reused_students == 1",
          meta["last_run"]["reused_students"] == 1,
          str(meta["last_run"]["reused_students"]))

    # --- 增量脚本那一侧: 只写 incremental 块, 不碰 gitee 块 ---
    inc = load_incremental()
    d = {"last_update": "2026-10-22T00:24:00",
         "last_fetch_meta": {"gitee": {"total_requests": 66, "ok": 4, "failed": 0}}}
    inc.REQUESTS = 438
    agg = {"skipped": 1, "new": 2, "updated": 3, "rotated": 4, "backfilled": 5,
           "reused": 6, "failed": 0, "class_total_views": 63112,
           "class_total_views_all": 65777}
    meta2 = inc.build_incremental_meta(d, 100.0, agg, students_ok=33,
                                       students_total=34)
    check("增量脚本写入 incremental 块",
          meta2["incremental"]["total_requests"] == 438,
          str(meta2["incremental"]["total_requests"]))
    check("增量脚本**没有**覆盖 gitee 块",
          (meta2.get("gitee") or {}).get("total_requests") == 66,
          str((meta2.get("gitee") or {}).get("total_requests")))
    check("增量后顶层 total_requests = 两块之和",
          meta2["total_requests"] == 438 + 66, str(meta2["total_requests"]))
    check("增量块带覆盖人数 (33/34)",
          meta2["incremental"]["students_ok"] == 33
          and meta2["incremental"]["students_total"] == 34)


# ---------- [2] R1: 日报健康度行 ----------
def test_report_health_line():
    print("\n[2] R1 — 日报'抓取健康度'行如实显示")
    v2 = load_v2()
    students = [
        {"sid": "s1", "name": "甲", "csdn_status": "ok", "csdn_username": "a",
         "posts": [{"title": "t", "guid": "g"}], "new_posts": [],
         "stats": {"total_views": 100, "articles_scanned": 1},
         "gitee": {"ok": True, "status": "ok", "stars": 1, "forks": 0,
                   "commit_count": 2, "sha": "abc", "date": "2026-10-22",
                   "msg": "m", "source": "api"}},
        {"sid": "s2", "name": "乙", "csdn_status": "skipped_reused",
         "csdn_username": "b", "posts": [], "new_posts": [],
         "stats": {"total_views": 50, "articles_scanned": 0},
         "gitee": {"ok": False, "status": "empty", "stars": 0, "forks": 0,
                   "commit_count": 0, "sha": "", "date": "", "msg": "",
                   "source": "api", "err": "仓库为空 (未提交任何代码)"}},
    ]
    stats = v2.compute_class_stats(students)

    # --- gitee-only: 必须说清楚 "CSDN 未抓取 (复用 N 人)" ---
    v2.GITEE_REQUESTS = 66
    meta_g = v2.build_fetch_meta(students, {}, run_csdn=False, run_gitee=True,
                                 csdn_ms=0, gitee_ms=79, total_ms=79)
    report_g = v2.build_report(students, [], meta_g, stats)
    line_g = [l for l in report_g.split("\n") if "健康度" in l]
    check("gitee-only 健康度行存在", bool(line_g), str(line_g))
    if line_g:
        check("gitee-only 显示 'CSDN 未抓取 (复用 1 人)'",
              "CSDN 未抓取" in line_g[0] and "复用 1 人" in line_g[0], line_g[0])
        check("gitee-only 不再出现 '成功 66/0'",
              "成功 66/0" not in line_g[0], line_g[0])

    # --- 正常增量: 显示真实的两个耗时 + 真实请求数 ---
    v2.CSDN_REQUESTS = 438
    v2.GITEE_REQUESTS = 66
    meta_n = v2.build_fetch_meta(students, {}, run_csdn=True, run_gitee=True,
                                 csdn_ms=1234, gitee_ms=79, total_ms=1313)
    report_n = v2.build_report(students, [], meta_n, stats)
    line_n = [l for l in report_n.split("\n") if "健康度" in l]
    check("正常增量健康度行存在", bool(line_n), str(line_n))
    if line_n:
        check("正常增量显示 CSDN 1234ms / Gitee 79ms",
              "CSDN 1234ms" in line_n[0] and "Gitee 79ms" in line_n[0], line_n[0])
        check("正常增量显示请求数 504 次", "504 次" in line_n[0], line_n[0])

    # --- 空仓库要在报告里如实显示 ---
    check("报告显示空仓库 1 个", "空仓库 1 个" in report_n, "")


# ---------- [3] Gitee 空仓库 vs 有 commit ----------
def test_gitee_empty_vs_ok():
    print("\n[3] Gitee — 空仓库 vs 有 commit (d280330 补测试)")
    v2 = load_v2()

    # --- 场景 A: 仓库存在但没提交 (commits API 404 + "Repository is empty") ---
    def fake_http_get_empty(url, timeout=12, headers=None, retries=2):
        if "per_page=100" in url:
            return FakeResp(404, '{"message":"Repository is empty"}')
        if "/commits" in url:
            return FakeResp(404, '{"message":"Repository is empty"}')
        return FakeResp(200, json_data={"stargazers_count": 0, "forks_count": 0})

    v2.http_get = fake_http_get_empty
    out = v2.fetch_gitee_all("owner", "emptyrepo")
    check("空仓库 status == 'empty'", out["status"] == "empty", out["status"])
    check("空仓库 ok == False (不再是假成功)", out["ok"] is False, str(out["ok"]))
    check("空仓库 empty == True", out["empty"] is True)
    check("空仓库 err 说清楚是空仓库",
          "空" in (out.get("err") or ""), str(out.get("err")))

    # --- 场景 B: 有 commit 的仓库 ---
    def fake_http_get_ok(url, timeout=12, headers=None, retries=2):
        if "per_page=100" in url:
            return FakeResp(200, json_data=[{"sha": "s1"}, {"sha": "s2"}])
        if "/commits" in url:
            return FakeResp(200, json_data=[{
                "sha": "abcdef1234567890",
                "commit": {"author": {"name": "张三"},
                           "authored_date": "2026-10-20 10:00:00",
                           "message": "init commit"}}])
        return FakeResp(200, json_data={"stargazers_count": 3, "forks_count": 1})

    v2.http_get = fake_http_get_ok
    out2 = v2.fetch_gitee_all("owner", "realrepo")
    check("有 commit 的仓库 status == 'ok'", out2["status"] == "ok", out2["status"])
    check("有 commit 的仓库 ok == True", out2["ok"] is True)
    check("拿到 sha (前 7 位)", out2["sha"] == "abcdef1", out2["sha"])
    check("拿到 commit_count", out2["commit_count"] == 2, str(out2["commit_count"]))
    check("拿到 stars", out2["stars"] == 3, str(out2["stars"]))
    check("有 commit 的仓库不算 empty", out2["empty"] is False)


# ---------- [4] R2: 模拟 521, 两次尝试间隔 >= 45 秒 ----------
def test_521_backoff():
    print("\n[4] R2 — 模拟 521: 重试前冷却 >= 45 秒")
    inc = load_incremental()
    from csdn_stats import RateLimiter

    # ★ 2026-10-24: 冷却从 45s 提到 60s (521 是分钟级 IP 封禁, 45s 常常不够)。
    #   断言不再写死具体秒数, 只保证"默认冷却 >= 45 秒"这个安全底线。
    check("默认冷却常量 >= 45 秒 (60s)",
          inc.CSDN_521_COOLDOWN >= 45.0,
          str(inc.CSDN_521_COOLDOWN))

    slept = []
    orig_sleep = time.sleep

    def fake_sleep(sec):
        slept.append(sec)

    class _R:
        status_code = 521
        text = ""

    calls = []

    def fake_get(*a, **k):
        calls.append(a[0] if a else "")
        return _R()

    orig_get = inc.requests.get
    orig_limiter = inc.LIMITER
    orig_requests = inc.REQUESTS
    try:
        time.sleep = fake_sleep
        inc.requests.get = fake_get
        # 基准间隔设 0, 这样记录到的 sleep 就只有"冷却"那部分
        inc.LIMITER = RateLimiter(min_interval=0, jitter=0)
        inc.REQUESTS = 0
        r = inc.csdn_get("https://blog.csdn.net/x/article/details/1", retries=1)
        n_requests = inc.REQUESTS
    finally:
        time.sleep = orig_sleep
        inc.requests.get = orig_get
        inc.LIMITER = orig_limiter
        inc.REQUESTS = orig_requests

    check("返回的是 521 响应", getattr(r, "status_code", None) == 521,
          str(getattr(r, "status_code", None)))
    check("重试了 1 次 (共 2 次请求)", n_requests == 2, str(n_requests))
    max_sleep = max(slept) if slept else 0
    # 45 秒是 penalize 的目标值, 实际 sleep 会比它少几毫秒 (两次调用之间
    # 过去了点时间), 所以按 44 秒起判。
    check("两次尝试之间冷却 >= 45 秒 (容差 1s)", max_sleep >= 44.0,
          f"最长 sleep={max_sleep:.2f}s")
    check("冷却确实来自 penalize (不是基准间隔)",
          any(s >= 44.0 for s in slept), str([round(s, 1) for s in slept]))


# ---------- [5] R3: 模拟 404, 文章被标记 _deleted_on_csdn 且不再重试 ----------
def test_404_marked_deleted():
    print("\n[5] R3 — 模拟 404: 文章标记 _deleted_on_csdn, 以后不再重抓")
    inc = load_incremental()

    # --- 单元 1: fetch_article_stats 遇到 404 返回 deleted ---
    class _Resp404:
        status_code = 404
        text = ""

    orig_get = inc.requests.get
    orig_limiter = inc.LIMITER
    try:
        inc.requests.get = lambda *a, **k: _Resp404()
        inc.LIMITER = type(inc.LIMITER)(min_interval=0, jitter=0)
        s = inc.fetch_article_stats("164961487", "someuser")
    finally:
        inc.requests.get = orig_get
        inc.LIMITER = orig_limiter
    check("404 返回 deleted=True", bool(s) and s.get("deleted") is True, str(s))
    check("404 的 views 是 None (不是 0)",
          s.get("views") is None, str(s.get("views")))

    # --- 单元 2: 整轮增量里, 404 文章被标记且不再重抓 ---
    fetched = []

    def fake_fetch_article_stats(aid, username):
        fetched.append(aid)
        if aid == "404001":
            return {"deleted": True, "views": None, "likes": None,
                    "comments": None, "collections": None}
        return {"views": 123, "likes": 1, "comments": 0, "collections": 0}

    def fake_fetch_blog_list(username, max_pages=20):
        return ([
            {"article_id": "404001", "link": "https://blog.csdn.net/u/article/details/404001",
             "title": "已删除的文章", "pub_raw": "2026-10-01"},
            {"article_id": "100001", "link": "https://blog.csdn.net/u/article/details/100001",
             "title": "正常文章", "pub_raw": "2026-10-02"},
        ], "ok")

    student = {
        "sid": "s1", "name": "测试同学", "csdn_username": "u",
        "posts": [
            {"title": "已删除的文章", "link": "https://blog.csdn.net/u/article/details/404001",
             "pubDate": "2026-10-01T00:00:00", "guid": "g1", "views": None},
            {"title": "正常文章", "link": "https://blog.csdn.net/u/article/details/100001",
             "pubDate": "2026-10-02T00:00:00", "guid": "g2", "views": 50},
        ],
    }
    orig_fas = inc.fetch_article_stats
    orig_fbl = inc.fetch_blog_list
    orig_fpv = inc.fetch_profile_views
    try:
        inc.fetch_article_stats = fake_fetch_article_stats
        inc.fetch_blog_list = fake_fetch_blog_list
        inc.fetch_profile_views = lambda u: 1000
        stats = inc.incremental_update_student(student, recent_n=4, rotate_m=2,
                                               backfill_per_student=3)
    finally:
        inc.fetch_article_stats = orig_fas
        inc.fetch_blog_list = orig_fbl
        inc.fetch_profile_views = orig_fpv

    posts = {p["link"].rsplit("/", 1)[-1]: p for p in student["posts"]}
    check("404 文章被标记 _deleted_on_csdn",
          posts["404001"].get("_deleted_on_csdn") is True,
          str(posts["404001"].get("_deleted_on_csdn")))
    check("404 文章的标记没被清理掉 (持久标记)",
          "_deleted_on_csdn" in posts["404001"],
          str(list(posts["404001"].keys())))
    check("正常文章没被误标", not posts["100001"].get("_deleted_on_csdn"))
    check("统计里记了删除数", stats.get("deleted", 0) >= 1, str(stats.get("deleted")))

    # --- 单元 3: 第二轮跑的时候, 已删除的文章**不再进重抓队列** ---
    fetched.clear()
    try:
        inc.fetch_article_stats = fake_fetch_article_stats
        inc.fetch_blog_list = fake_fetch_blog_list
        inc.fetch_profile_views = lambda u: 1000
        inc.incremental_update_student(student, recent_n=4, rotate_m=2,
                                       backfill_per_student=3)
    finally:
        inc.fetch_article_stats = orig_fas
        inc.fetch_blog_list = orig_fbl
        inc.fetch_profile_views = orig_fpv
    check("第二轮不再抓已删除的文章", "404001" not in fetched, str(fetched))
    check("第二轮仍然抓正常文章", "100001" in fetched, str(fetched))


# ---------- [6] R4: 列表抓取 no_posts vs fetch_failed ----------
def _resp(status, text):
    class _R:
        pass
    r = _R()
    r.status_code = status
    r.text = text
    return r


def test_list_fetch_diagnosis():
    print("\n[6] R4 — 列表抓取: '真的没文章' vs '被风控/软反爬'")
    inc = load_incremental()
    orig = inc.csdn_get

    def call_with(html, status=200):
        try:
            inc.csdn_get = lambda *a, **k: _resp(status, html)
            return inc.fetch_blog_list("u")
        finally:
            inc.csdn_get = orig

    # --- A: HTTP 200 但一个文章块都没解析到 (软反爬) ---
    arts, reason = call_with("<html><body>" + "x" * 3000 + "</body></html>")
    check("200 但没解析到文章块 -> fetch_failed", reason == "fetch_failed", reason)
    check("这种情况不返回文章", arts == [], str(arts))

    # --- B: 空博客页 (页面写着"暂无文章") -> no_posts ---
    arts, reason = call_with(
        "<html><body>您还没有发布任何博客" + "y" * 2000 + "</body></html>")
    check("页面写着'暂无文章' -> no_posts", reason == "no_posts", reason)

    # --- C: 响应过短 (软反爬的典型特征) ---
    arts, reason = call_with("<html>blocked</html>")
    check("响应过短 -> fetch_failed", reason == "fetch_failed", reason)

    # --- D: <article> 兜底 (class 不再是 blog-list-box) ---
    html = ('<html><body><article class="new-style">'
            '<h3>标题</h3>'
            '<a href="https://blog.csdn.net/u/article/details/123456">看文章</a>'
            '<div class="view-time-box">博文更新于 2026.10.01</div>'
            '</article></body></html>')
    arts, reason = call_with(html)
    check("<article> 兜底能解析出文章", len(arts) == 1, str(len(arts)))
    check("兜底解析出的 article_id 正确",
          arts and arts[0]["article_id"] == "123456",
          str(arts[0]["article_id"]) if arts else "")

    # --- E: 别人的文章链接被过滤 (不能当成"新文章"写进这个学生) ---
    html2 = ('<html><body><article>'
             '<a href="https://blog.csdn.net/otheruser/article/details/999">推荐</a>'
             '</article></body></html>')
    arts2, reason2 = call_with(html2)
    check("别人的文章链接被过滤掉", arts2 == [], str(arts2))

    # --- F: 上层能拿到不同的 skipped 原因 ---
    orig_fbl = inc.fetch_blog_list
    try:
        inc.fetch_blog_list = lambda *a, **k: ([], "no_posts")
        s1 = inc.incremental_update_student(
            {"sid": "s1", "name": "甲", "csdn_username": "u", "posts": []})
        inc.fetch_blog_list = lambda *a, **k: ([], "fetch_failed")
        s2 = inc.incremental_update_student(
            {"sid": "s2", "name": "乙", "csdn_username": "u", "posts": []})
    finally:
        inc.fetch_blog_list = orig_fbl
    check("没文章的学生 skipped=no_posts",
          s1.get("skipped") == "no_posts", str(s1.get("skipped")))
    check("被风控的学生 skipped=fetch_failed",
          s2.get("skipped") == "fetch_failed", str(s2.get("skipped")))


# ---------- [7] R5: 增量脚本重算 class_stats (结构与 v2 完全一致) ----------
def test_class_stats_refreshed():
    print("\n[7] R5 — 增量跑完重算 class_stats (结构与 v2 一致)")
    v2 = load_v2()
    inc = load_incremental()

    # v2 内存态: stats 在 s["stats"] 里
    state = [
        {
            "sid": "s1", "name": "甲", "csdn_username": "jia",
            "posts": [{"views": 10}, {"views": 20}],
            "stats": {"total_views": 300, "total_likes": 5, "total_comments": 2,
                      "total_collections": 1, "articles_scanned": 2},
            "gitee": {"ok": True, "commit_count": 4, "stars": 2, "forks": 1},
        },
        {
            "sid": "s2", "name": "乙", "csdn_username": "yi",
            "posts": [{"views": 7}],
            "stats": {"total_views": 100, "total_likes": 1, "total_comments": 0,
                      "total_collections": 0, "articles_scanned": 1},
            "gitee": {"ok": False, "commit_count": 0, "stars": 0, "forks": 0},
        },
        {
            "sid": "s3", "name": "丙", "csdn_username": "",
            "posts": [],
            "stats": {"total_views": 0, "total_likes": 0, "total_comments": 0,
                      "total_collections": 0, "articles_scanned": 0},
            "gitee": {"ok": False},
        },
    ]
    # data.json 落盘态: 字段摊平在 s 顶层
    flat = [
        {
            "sid": "s1", "name": "甲", "csdn_username": "jia",
            "posts": [{"views": 10}, {"views": 20}],
            "total_views": 300, "total_likes": 5, "total_comments": 2,
            "total_collections": 1, "articles_scanned": 2,
            "gitee": {"ok": True, "commit_count": 4, "stars": 2, "forks": 1},
        },
        {
            "sid": "s2", "name": "乙", "csdn_username": "yi",
            "posts": [{"views": 7}],
            "total_views": 100, "total_likes": 1, "total_comments": 0,
            "total_collections": 0, "articles_scanned": 1,
            "gitee": {"ok": False, "commit_count": 0, "stars": 0, "forks": 0},
        },
        {
            "sid": "s3", "name": "丙", "csdn_username": "",
            "posts": [],
            "total_views": 0, "total_likes": 0, "total_comments": 0,
            "total_collections": 0, "articles_scanned": 0,
            "gitee": {"ok": False},
        },
    ]

    a = v2.compute_class_stats(state)
    b = inc.compute_class_stats({"students": flat, "last_update": "2026-10-22T01:00:00"})

    check("class_stats 顶层 key 集合与 v2 一致",
          set(a.keys()) == set(b.keys()),
          f"v2={sorted(a.keys())} inc={sorted(b.keys())}")
    check("class_stats.csdn key 集合与 v2 一致",
          set(a["csdn"].keys()) == set(b["csdn"].keys()),
          f"v2={sorted(a['csdn'].keys())} inc={sorted(b['csdn'].keys())}")
    check("class_stats.gitee key 集合与 v2 一致",
          set(a["gitee"].keys()) == set(b["gitee"].keys()),
          f"v2={sorted(a['gitee'].keys())} inc={sorted(b['gitee'].keys())}")
    for k in ("students_total", "students_with_csdn", "students_active",
              "students_inactive"):
        check(f"class_stats.{k} 与 v2 一致 ({a[k]})", a[k] == b[k], str(b[k]))
    for k in ("total_posts", "total_views", "total_likes", "total_comments",
              "total_collections", "avg_posts_per_active", "avg_views_per_student"):
        check(f"class_stats.csdn.{k} 与 v2 一致 ({a['csdn'][k]})",
              a["csdn"][k] == b["csdn"][k], str(b["csdn"][k]))
    for k in ("students_with_repo", "total_commits", "total_stars",
              "total_forks", "avg_commits_per_student"):
        check(f"class_stats.gitee.{k} 与 v2 一致 ({a['gitee'][k]})",
              a["gitee"][k] == b["gitee"][k], str(b["gitee"][k]))

    # 增量跑完之后 class_stats 必须跟着涨 (不是停在旧值)
    flat2 = json.loads(json.dumps(flat))
    flat2[0]["total_views"] = 999
    flat2[0]["posts"].append({"views": 5})
    c = inc.compute_class_stats({"students": flat2})
    check("增量后班级总阅读被刷新 (400 -> 1099 = 999+100+0)",
          c["csdn"]["total_views"] == 1099, str(c["csdn"]["total_views"]))
    check("增量后总文章数被刷新 (3 -> 4)",
          c["csdn"]["total_posts"] == 4, str(c["csdn"]["total_posts"]))


# ---------- [8] R7: 周日全量安全网 (备份 / 校验 / 回滚) ----------
def _make_class(n=34, views=1934, posts=11):
    """造一份"好的"班级数据 (n 个学生, 每人 views 阅读 / posts 篇文章)。"""
    return {
        "total_students": n,
        "total_posts": n * posts,
        "class_stats": {"csdn": {"total_views": n * views, "total_posts": n * posts}},
        "students": [
            {
                "sid": f"s{i}", "name": f"学生{i}", "total_views": views,
                "post_count": posts,
                "posts": [{"title": f"t{j}", "link": f"https://x/{i}/{j}"}
                          for j in range(posts)],
                "gitee": {"ok": True, "status": "ok"},
            }
            for i in range(n)
        ],
    }


def test_weekly_guard_rollback():
    print("\n[8] R7 — 周日全量安全网: 备份 / 校验 / 回滚")
    import tempfile
    import shutil
    guard = load_module("guardmod", "weekly_guard.py")

    good = _make_class()
    total_good = 34 * 1934

    # ---------- 场景 A: 总阅读腰斩 (只掉 40%, 保护逻辑的 C 臂挡不住) ----------
    halved = _make_class(views=int(1934 * 0.6))   # 掉 40%
    res_a = guard.evaluate(halved, good)
    check("场景A 总阅读掉 40% -> 判不合格", res_a["ok"] is False,
          str(res_a["reasons"]))
    check("场景A 命中的是 R1 绝对下限 (60%)",
          any(r.startswith("R1") for r in res_a["reasons"]), str(res_a["reasons"]))

    # 掉 39% (刚好在 60% 门槛之上) -> 不该误杀
    almost = _make_class(views=int(1934 * 0.62))
    res_a2 = guard.evaluate(almost, good)
    check("场景A 只掉 38% 不误杀", res_a2["ok"] is True, str(res_a2["reasons"]))

    # ---------- 场景 B: 部分学生失败 (占比 > 20%) ----------
    partial = _make_class()
    for i in range(9):            # 9/34 = 26% > 20%
        partial["students"][i]["total_views"] = 0
    res_b = guard.evaluate(partial, good)
    check("场景B 9/34 学生归零 -> 判不合格", res_b["ok"] is False,
          str(res_b["reasons"]))
    check("场景B 命中的是 R2 学生级 (>20%)",
          any(r.startswith("R2") for r in res_b["reasons"]), str(res_b["reasons"]))
    # 总阅读只掉 26% (还 > 60%) -> 证明 R2 是独立于 R1 的一层网
    check("场景B 总阅读还剩 74% (R1 没触发, 是 R2 抓住的)",
          res_b["stats"]["new_total_views"] > 0.6 * total_good,
          f"{res_b['stats']['new_total_views']} / {total_good}")

    # 5/34 = 15% -> 不该误杀
    small = _make_class()
    for i in range(5):
        small["students"][i]["total_views"] = 0
    res_b2 = guard.evaluate(small, good)
    check("场景B 只挂 5/34 (15%) 不误杀", res_b2["ok"] is True, str(res_b2["reasons"]))

    # ---------- 场景 C: 正常一周 (阅读量只涨) ----------
    up = _make_class(views=2100)
    res_c = guard.evaluate(up, good)
    check("场景C 阅读量涨了 -> 通过", res_c["ok"] is True, str(res_c["reasons"]))

    # ---------- 端到端: 备份 -> 写坏 -> 校验失败 -> 回滚 ----------
    tmp = tempfile.mkdtemp(prefix="weekly_guard_test_")
    try:
        d = Path(tmp)
        (d / "cache" / "csdn").mkdir(parents=True, exist_ok=True)
        (d / "data.json").write_text(
            json.dumps(good, ensure_ascii=False), encoding="utf-8")
        (d / "cache" / "csdn" / "snapshot.json").write_text(
            json.dumps({"last_run": "old"}, ensure_ascii=False), encoding="utf-8")

        bdir = guard.backup(d, keep=8)
        check("备份目录建出来了", bdir.exists(), str(bdir))
        check("备份里有 data.json", (bdir / "data.json").exists(), str(bdir))
        check("备份里有 snapshot.json",
              (bdir / "cache" / "csdn" / "snapshot.json").exists(), str(bdir))
        check("LATEST.txt 指向刚建的备份",
              (guard.read_latest(d) or Path()).resolve() == bdir.resolve(),
              str(guard.read_latest(d)))

        # 模拟全量跑砸了: data.json 被覆盖成残缺数据
        (d / "data.json").write_text(
            json.dumps(halved, ensure_ascii=False), encoding="utf-8")
        rc = guard.main(["--dir", str(d), "check", "--backup", str(bdir)])
        check("check 对残缺数据返回非 0 (会让 .cmd 停下)", rc != 0, str(rc))

        # 回滚
        rc2 = guard.main(["--dir", str(d), "rollback", "--backup", str(bdir)])
        check("rollback 返回 0", rc2 == 0, str(rc2))
        restored = json.loads((d / "data.json").read_text(encoding="utf-8"))
        check("回滚后 data.json 恢复到跑之前的值",
              guard.class_reading(restored) == total_good,
              f"{guard.class_reading(restored)} vs {total_good}")
        check("回滚后 34 个学生都在",
              len(restored.get("students", [])) == 34,
              str(len(restored.get("students", []))))
        rc3 = guard.main(["--dir", str(d), "check", "--backup", str(bdir)])
        check("回滚后再 check 就通过了", rc3 == 0, str(rc3))

        # 备份保留份数: 连存 10 份只留 8 份
        for i in range(10):
            guard.backup(d, keep=8, when=f"2026-01-01T00-00-{i:02d}")
        dirs = [p for p in (d / "cache" / "backup").iterdir() if p.is_dir()]
        check("备份只保留最近 8 份", len(dirs) == 8, str(len(dirs)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- [9] R7: 05:30 gitee-only 不能把班级总阅读打残 ----------
def test_gitee_only_keeps_class_reading():
    print("\n[9] R7 — 05:30 gitee-only 之后, 班级总阅读保持 00:00 增量算出的值")
    v2 = load_v2()

    # 00:00 增量跑完的好数据 (class 总阅读 65777)
    prev_data = {
        "total_students": 2, "total_posts": 4,
        "students": [
            {"sid": "s1", "name": "甲", "csdn_username": "a", "total_views": 7379,
             "total_likes": 1, "total_comments": 1, "total_collections": 1,
             "profile_views": 7379, "article_views_sum": 7337,
             "views_source": "profile", "articles_scanned": 2,
             "stats_updated_at": "2026-10-22T00:24:00",
             "posts": [{"title": "a1", "link": "https://x/1",
                        "views": 3000, "stats_updated_at": "2026-10-22T00:24:00"}],
             "gitee": {"ok": True, "status": "ok"}},
            {"sid": "s2", "name": "乙", "csdn_username": "b", "total_views": 1297,
             "total_likes": 0, "total_comments": 0, "total_collections": 0,
             "profile_views": 1297, "article_views_sum": 1297,
             "views_source": "profile", "articles_scanned": 1,
             "stats_updated_at": "2026-10-22T00:24:00",
             "posts": [{"title": "b1", "link": "https://x/2",
                        "views": 1297, "stats_updated_at": "2026-10-22T00:24:00"}],
             "gitee": {"ok": False, "status": "empty"}},
        ],
    }
    # 05:30 gitee-only: CSDN 一个字段都没抓, 原样复用
    students_state = [
        {"sid": "s1", "name": "甲", "csdn": "https://blog.csdn.net/a",
         "gitee_url": "", "gitee": {}, "csdn_username": "", "csdn_status": "skipped",
         "posts": [], "new_posts": [], "stats": {},
         "csdn_err": "skipped (--gitee-only)"},
        {"sid": "s2", "name": "乙", "csdn": "https://blog.csdn.net/b",
         "gitee_url": "", "gitee": {}, "csdn_username": "", "csdn_status": "skipped",
         "posts": [], "new_posts": [], "stats": {},
         "csdn_err": "skipped (--gitee-only)"},
    ]
    prev_by_sid = {s["sid"]: s for s in prev_data["students"]}
    for rec in students_state:
        p = prev_by_sid.get(rec["sid"])
        rec["posts"] = p.get("posts", [])
        rec["csdn_username"] = p.get("csdn_username", "")
        rec["csdn_status"] = "skipped_reused"
        rec["stats"] = {
            "total_views": p.get("total_views", 0),
            "total_likes": p.get("total_likes", 0),
            "total_comments": p.get("total_comments", 0),
            "total_collections": p.get("total_collections", 0),
            "profile_views": p.get("profile_views"),
            "article_views_sum": p.get("article_views_sum", 0),
            "views_source": p.get("views_source", "reused"),
            "articles_scanned": p.get("articles_scanned", 0),
            "fetched_at": p.get("stats_updated_at", ""),
        }

    class_stats = v2.compute_class_stats(students_state)
    check("gitee-only 之后班级总阅读还是 8,676 (不是 1,297 那种残缺值)",
          class_stats["csdn"]["total_views"] == 7379 + 1297,
          str(class_stats["csdn"]["total_views"]))
    check("gitee-only 之后总文章数不变", class_stats["csdn"]["total_posts"] == 2,
          str(class_stats["csdn"]["total_posts"]))

    # 保护逻辑在 gitee-only 下**不该**触发 (数据没坏, 只是没抓)
    v2.apply_data_protection(students_state, prev_data, verbose=False)
    check("gitee-only 不误触发数据保护",
          students_state[0]["stats"]["total_views"] == 7379,
          str(students_state[0]["stats"]["total_views"]))


# ---------- [10] R8: 口径如实显示 ----------
def test_r8_truthful_counting():
    print("\n[10] R8 — 口径如实显示 (views_source / 复用计数 / 覆盖人数)")
    v2 = load_v2()
    inc = load_incremental()

    # --- 1. gitee-only 复用: views_source 用真实值, 不被写死成 "reused" ---
    st = v2.reuse_prev_csdn_stats({
        "total_views": 7379, "total_likes": 3, "total_comments": 1,
        "total_collections": 2, "profile_views": 7379,
        "article_views_sum": 7337, "views_source": "profile",
        "articles_scanned": 11, "stats_updated_at": "2026-10-22T00:24:00"})
    check("gitee-only 复用保留真实 views_source (profile)",
          st["views_source"] == "profile", st["views_source"])
    check("gitee-only 复用保留 profile_views / article_views_sum",
          st["profile_views"] == 7379 and st["article_views_sum"] == 7337,
          f"{st['profile_views']}/{st['article_views_sum']}")
    st_old = v2.reuse_prev_csdn_stats({"total_views": 100})
    check("老数据没有 views_source 时退化成 none (不是 reused)",
          st_old["views_source"] == "none", st_old["views_source"])

    # --- 2. 增量脚本的"复用"计数: 本轮真没重抓的都算复用 ---
    def make_student():
        posts = []
        for i in range(10):
            posts.append({
                "title": f"文章{i}", "link": f"https://blog.csdn.net/u/article/details/{i}",
                "pubDate": f"2026-10-{i + 1:02d}T00:00:00", "guid": f"g{i}",
                # 前 4 篇 (最新) 有 stats, 后 6 篇从没抓到过 (views=None)
                "views": 100 if i >= 6 else None,
            })
        return {"sid": "s1", "name": "甲", "csdn_username": "u", "posts": posts}

    orig_fas = inc.fetch_article_stats
    orig_fbl = inc.fetch_blog_list
    orig_fpv = inc.fetch_profile_views
    tried = []

    def fake_fetch_article_stats(aid, username):
        tried.append(aid)
        return {"views": 123, "likes": 1, "comments": 0, "collections": 0}

    def fake_fetch_blog_list(username, max_pages=20):
        arts = [{"article_id": str(i),
                 "link": f"https://blog.csdn.net/u/article/details/{i}",
                 "title": f"文章{i}", "pub_raw": "2026-10-01"}
                for i in range(10)]
        return arts, "ok"

    try:
        inc.fetch_article_stats = fake_fetch_article_stats
        inc.fetch_blog_list = fake_fetch_blog_list
        inc.fetch_profile_views = lambda u: 1000

        # --- 2a. 正常跑 (backfill 每人最多 3 篇) ---
        s = make_student()
        r = inc.incremental_update_student(s, recent_n=4, rotate_m=0,
                                          backfill_per_student=3)
        check("backfill 截断时顺延的篇数被单独记下来",
              r.get("backfill_deferred") == 3, str(r.get("backfill_deferred")))
        check("复用数 = 本轮真没重抓的 (10 篇 - 抓了 7 篇)",
              r.get("reused") == 3, str(r.get("reused")))

        # --- 2b. 时间预算用尽: backfill 一篇都没抓成 ---
        tried.clear()
        s2 = make_student()
        r2 = inc.incremental_update_student(
            s2, recent_n=4, rotate_m=0, backfill_per_student=3,
            deadline=time.time() - 1)      # 预算早就用光了
        check("时间预算用尽时只抓了最近 4 篇", len(tried) == 4, str(tried))
        check("预算用尽时复用数 = 6 (不是旧算法的 3)",
              r2.get("reused") == 6, str(r2.get("reused")))
        check("预算用尽的篇数记在 budget_exhausted 上",
              r2.get("budget_exhausted") == 3, str(r2.get("budget_exhausted")))
    finally:
        inc.fetch_article_stats = orig_fas
        inc.fetch_blog_list = orig_fbl
        inc.fetch_profile_views = orig_fpv

    # --- 3. 班级合计那一行要说清覆盖人数 ---
    line = inc.class_total_line(33, 34, 63112, 1, 65777)
    check("班级合计行说明覆盖人数 (33/34)",
          "33/34" in line, line)
    check("班级合计行说明沿用上次数据的人数 (1 人)",
          "1 人沿用上次数据" in line, line)
    check("班级合计行给出本轮合计与全班两个数",
          "63,112" in line and "65,777" in line, line)

    # --- 4. 日报/摘要里的"空仓库 N 个" (d280330 做的, 这里确认没回退) ---
    students = [
        {"sid": "s1", "name": "甲", "csdn_status": "ok", "csdn_username": "a",
         "posts": [{"title": "t", "guid": "g"}], "new_posts": [],
         "stats": {"total_views": 100, "articles_scanned": 1},
         "gitee": {"ok": True, "status": "ok", "stars": 1, "forks": 0,
                   "commit_count": 2, "sha": "abc", "date": "2026-10-22",
                   "msg": "m", "source": "api"}},
        {"sid": "s2", "name": "乙", "csdn_status": "skipped_reused",
         "csdn_username": "b", "posts": [], "new_posts": [],
         "stats": {"total_views": 50, "articles_scanned": 0},
         "gitee": {"ok": False, "status": "empty", "stars": 0, "forks": 0,
                   "commit_count": 0, "sha": "", "date": "", "msg": "",
                   "source": "api", "err": "仓库为空"}},
    ]
    stats = v2.compute_class_stats(students)
    v2.GITEE_REQUESTS = 66
    meta = v2.build_fetch_meta(students, {}, run_csdn=False, run_gitee=True,
                              csdn_ms=0, gitee_ms=79, total_ms=79)
    report = v2.build_report(students, [], meta, stats)
    summary = v2.build_summary(students, 0, stats)
    check("日报里有'空仓库 1 个'", "空仓库 1 个" in report, "")
    check("飞书摘要里有'空仓库: 1 个'", "空仓库: 1 个" in summary, "")


# ---------- [11] R9: CSDN_ARTICLE_BASE 测试钩子 + RSS feed 顺序 ----------
def test_r9_article_base_hook():
    print("\n[11] R9 — CSDN_ARTICLE_BASE 测试钩子 / RSS 顺序")
    v2 = load_v2()

    # --- 1. 默认 (不设环境变量) 文章基址 = 列表基址, 行为完全不变 ---
    check("默认 CSDN_ARTICLE_BASE == CSDN_BASE (生产行为不变)",
          v2.CSDN_ARTICLE_BASE == v2.CSDN_BASE,
          f"{v2.CSDN_ARTICLE_BASE} / {v2.CSDN_BASE}")

    # --- 2. 设了 CSDN_ARTICLE_BASE_URL 就单独换文章基址 ---
    os.environ["CSDN_ARTICLE_BASE_URL"] = "https://csdn-articles.example.invalid"
    try:
        v2b = load_module("v2mod_article_hook", "csdn-monitor-v2.py")
    finally:
        os.environ.pop("CSDN_ARTICLE_BASE_URL", None)
    check("CSDN_ARTICLE_BASE_URL 钩子生效",
          v2b.CSDN_ARTICLE_BASE == "https://csdn-articles.example.invalid",
          v2b.CSDN_ARTICLE_BASE)
    check("列表基址不受影响 (CSDN_BASE 不变)",
          v2b.CSDN_BASE == v2.CSDN_BASE, v2b.CSDN_BASE)

    # --- 3. fetch_article_stats 打的 URL 用的是**文章基址** ---
    asked = []

    def fake_get(url, *a, **k):
        asked.append(url)
        return FakeResp(200, "<html>" + "x" * 600 + "</html>")

    orig_get = v2b.csdn_get
    try:
        v2b.csdn_get = fake_get
        v2b.fetch_article_stats("123456", "zhangsan")
    finally:
        v2b.csdn_get = orig_get
    check("详情页 URL 走的是文章基址",
          asked and asked[0].startswith("https://csdn-articles.example.invalid/"),
          asked[0] if asked else "(没发请求)")
    check("详情页 URL 结构没变 (.../{user}/article/details/{id})",
          asked and asked[0].endswith("/zhangsan/article/details/123456"),
          asked[0] if asked else "")

    # --- 4. 列表页仍走列表基址 → "列表正常但详情页全挂" 可以端到端模拟 ---
    list_asked = []

    def fake_list_get(url, *a, **k):
        list_asked.append(url)
        return FakeResp(200, "<html>没有 blog-list-box</html>")

    orig_get = v2b.csdn_get
    try:
        v2b.csdn_get = fake_list_get
        v2b.fetch_csdn_html("zhangsan", limit=5)
    finally:
        v2b.csdn_get = orig_get
    check("列表页仍走 CSDN_BASE (跟文章基址分开)",
          list_asked and all(u.startswith(v2b.CSDN_BASE) for u in list_asked),
          list_asked[0] if list_asked else "")

    # --- 5. 详情页全挂 (521) 时 fetch_article_stats 明确返回失败 ---
    def fake_521(url, *a, **k):
        return FakeResp(521, "")

    orig_get = v2b.csdn_get
    try:
        v2b.csdn_get = fake_521
        st = v2b.fetch_article_stats("123456", "zhangsan")
    finally:
        v2b.csdn_get = orig_get
    check("详情页 521 时返回 ok=False (不会静默当成 0 阅读量)",
          st.get("ok") is False, str(st))

    # --- 6. RSS feed 顺序: 官方在前, rsshub 兜底 ---
    feed_asked = []

    def fake_http_get(url, *a, **k):
        feed_asked.append(url)
        return FakeResp(200, "")

    orig_http = v2.http_get
    try:
        v2.http_get = fake_http_get
        v2.fetch_csdn_rss("zhangsan")
    finally:
        v2.http_get = orig_http
    check("RSS 先试 CSDN 官方 feed",
          len(feed_asked) >= 1 and feed_asked[0].endswith("/zhangsan/rss/list")
          and feed_asked[0].startswith(v2.CSDN_BASE),
          feed_asked[0] if feed_asked else "")
    check("RSS 第二个是 rsshub 兜底",
          len(feed_asked) >= 2 and "rsshub.app" in feed_asked[1],
          feed_asked[1] if len(feed_asked) > 1 else "")
    check("RSS 顺序是 官方 → rsshub (不是反过来)",
          len(feed_asked) >= 2 and "rsshub.app" not in feed_asked[0],
          " → ".join(feed_asked[:2]))


# ---------- [12] R10: data.json / snapshot.json 紧凑化 ----------
def test_r10_compact_json():
    print("\n[12] R10 — data.json / snapshot.json 紧凑化")
    import tempfile
    v2 = load_v2()
    inc = load_incremental()

    def is_compact(text):
        """紧凑 = 顶层对象里没有换行 + 缩进 (json.dumps 默认带 ", " / ": ")"""
        if "\n" in text:
            return False
        return '": ' not in text and '", "' not in text

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # --- 1. v2 save_snapshot 写紧凑 ---
        orig_snap, orig_cache = v2.SNAPSHOT, v2.CACHE_DIR
        try:
            v2.CACHE_DIR = tmp / "cache" / "csdn"
            v2.SNAPSHOT = v2.CACHE_DIR / "snapshot.json"
            v2.save_snapshot({"hello": "世界", "n": [1, 2]})
            txt = v2.SNAPSHOT.read_text(encoding="utf-8")
        finally:
            v2.SNAPSHOT, v2.CACHE_DIR = orig_snap, orig_cache
        check("v2 save_snapshot 写的是紧凑格式", is_compact(txt), txt[:60])
        check("紧凑格式内容仍然正确 (roundtrip)",
              json.loads(txt) == {"hello": "世界", "n": [1, 2]}, "")

        # --- 2. v2 write_data_json 写紧凑 ---
        orig_dj = v2.DATA_JSON
        try:
            v2.DATA_JSON = tmp / "data.json"
            v2.write_data_json([], {"fetched_at": "2026-10-22T00:00:00"})
            txt = v2.DATA_JSON.read_text(encoding="utf-8")
        finally:
            v2.DATA_JSON = orig_dj
        check("v2 write_data_json 写的是紧凑格式", is_compact(txt), txt[:60])

        # --- 3. 增量脚本 write_snapshot 也必须紧凑 (否则每天被改回缩进) ---
        orig_dd = inc.DASHBOARD_DIR
        try:
            inc.DASHBOARD_DIR = tmp / "inc"
            inc.write_snapshot({"last_update": "2026-10-22",
                                "students": [{"sid": "s1", "name": "甲"}]})
            txt = (tmp / "inc" / "cache" / "csdn" / "snapshot.json").read_text(
                encoding="utf-8")
        finally:
            inc.DASHBOARD_DIR = orig_dd
        check("增量脚本 write_snapshot 写的是紧凑格式", is_compact(txt), txt[:60])

        # --- 4. history-real 归档**保持缩进** (要人可读, 供分析用) ---
        orig_hist = v2.HISTORY_DIR
        try:
            v2.HISTORY_DIR = tmp / "history-real"
            v2.write_history_snapshot({
                "total_students": 1, "total_posts": 2, "class_stats": {},
                "students": [{"sid": "s1", "name": "甲", "post_count": 2,
                              "total_views": 10,
                              "gitee": {"ok": True, "commit_count": 1,
                                        "sha": "abc", "date": "2026-10-22"}}],
            })
            hist_files = list((tmp / "history-real").glob("*.json"))
            htxt = hist_files[0].read_text(encoding="utf-8") if hist_files else ""
        finally:
            v2.HISTORY_DIR = orig_hist
        check("history-real 归档仍是缩进格式 (人可读)",
              bool(htxt) and "\n" in htxt and '": ' in htxt, htxt[:40].replace("\n", "⏎"))

    # --- 5. 真实 data.json: 紧凑 + 能读 + 内容没变 ---
    real = DASHBOARD_DIR / "data.json"
    raw = real.read_text(encoding="utf-8")
    check("仓库里的 data.json 已是紧凑格式", is_compact(raw), raw[:60])
    try:
        parsed = json.loads(raw)
        check("紧凑 data.json 能正常 json.load", isinstance(parsed, dict), "")
        check("紧凑 data.json 内容没丢 (students 都在)",
              isinstance(parsed.get("students"), list)
              and len(parsed["students"]) > 30, str(len(parsed.get("students", []))))
    except Exception as e:
        check("紧凑 data.json 能正常 json.load", False, str(e))

    # --- 6. 现有 history-real 文件仍然是缩进形态 ---
    hist_files = sorted((DASHBOARD_DIR / "history-real").glob("*.json"))
    indented = 0
    for hp in hist_files:
        t = hp.read_text(encoding="utf-8", errors="replace")
        if "\n" in t and '": ' in t:
            indented += 1
    check("仓库里的 history-real/*.json 全是缩进形态",
          hist_files and indented == len(hist_files),
          f"{indented}/{len(hist_files)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="(本测试全是离线的)")
    args = ap.parse_args()

    print("=" * 70)
    print("2026-10-22 第二轮修复回归测试")
    print("=" * 70)

    test_fetch_meta_blocks()
    test_report_health_line()
    test_gitee_empty_vs_ok()
    test_521_backoff()
    test_404_marked_deleted()
    test_list_fetch_diagnosis()
    test_class_stats_refreshed()
    test_weekly_guard_rollback()
    test_gitee_only_keeps_class_reading()
    test_r8_truthful_counting()
    test_r9_article_base_hook()
    test_r10_compact_json()

    print("\n" + "=" * 70)
    print(f"通过 {PASS} / 失败 {FAIL}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
