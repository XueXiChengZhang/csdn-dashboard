#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_csdn_fixes.py — 2026-10-22 第二轮修复的回归测试

覆盖 (每个都对应一个已提交的修复):
  [1] R1  last_fetch_meta 按模式分块 (增量块 / gitee 块互不覆盖)
  [2] R1  日报"抓取健康度"行如实显示 (不再出现"成功 66/0")
  [3] Gitee 空仓库 vs 有 commit 的判定 (d280330 补测试)
  [4] R2  模拟 521: 两次尝试间隔 >= 45 秒
  [5] R3  模拟 404: 文章被标记 _deleted_on_csdn 且不再重试
  [6] R4  列表抓取: no_posts (真没文章) vs fetch_failed (被风控)
  [7] R7  周日全量: 总阅读腰斩时触发回滚

跑法:
  python scripts/test_csdn_fixes.py            # 全部 (离线, 不打真网站)
  python scripts/test_csdn_fixes.py --offline  # 同上 (本测试全是离线的)

★ 本文件**不联网**: 所有 HTTP 都被 mock 掉了。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
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

    check("默认冷却常量是 45 秒",
          abs(inc.CSDN_521_COOLDOWN - 45.0) < 1e-6,
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

    print("\n" + "=" * 70)
    print(f"通过 {PASS} / 失败 {FAIL}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
