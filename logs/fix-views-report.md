# CSDN 仪表盘"阅读量抓不到 / 阅读量不增长" — 修复报告

- **日期**: 2026-10-21
- **分支**: `fix/audit` (worktree `D:\WebArchives\csdn-dashboard-cc`) → 合并回 `main`
- **触发**: Ma 原话 —— **"数据应该是增量的啊"**

> ⚠ 本报告里凡是带数字的结论, 都是**实际跑出来的**, 不是推演。
> 每条根因都附了复现方式。

---

## 0. 一句话结论

阅读量抓不到, **不是解析器的锅** (解析器基本是好的), 是**调度 + 复用 + 保护逻辑**三件事叠在一起:

1. 两个定时任务都在跑**全量**脚本, 一天两次重抓 385 篇 → 第二次被 CSDN 的 **HTTP 521** 风控拦掉 → 把第一次的好数据覆盖成 0。
2. `csdn_incremental.py` (真增量脚本) **从没被调度过**, 而且它自己的"老文章不重抓"逻辑写反了 —— 就算调度了, 阅读量也不会涨。
3. v2 的 "24h 复用" 是**死代码**, 所以每次全量都重抓 385 篇 —— 这才是触发 521 的直接原因。

修完之后: **total_views 从 1,297 → 62,930**, 有 views 的文章从 **14/385 (3.6%) → 273/385 (70%)**, 并且**连跑两次阅读量确实在涨** (13 篇涨 / 7 篇持平 / 0 篇跌)。

---

## 1. 根因确认 (我的实测证据)

### 1.1 CSDN 的风控响应是 HTTP 521 (关键发现)

任务文件里只说了"被限流", 没说是怎么限流的。我做了对照实验
(工具已固化成 `scripts/probe_csdn_limits.py`):

| 请求间隔 | 结果 |
|---|---|
| 0 秒 (连打) | 40 篇里**第 17 篇开始大面积 521**, ok=22/40 |
| 1.5 秒 | **40/40 全部 200**, 零 521 |
| 2.0 秒 | **40/40 全部 200**, 零 521 |
| 3.0 秒 | **30/30 全部 200**, 零 521 |

521 的响应体有 25839 字节 (不是空页), 所以"响应过短"那种检查抓不住它, **必须显式判 status_code**。

复现:
```bash
python scripts/probe_csdn_limits.py --delays 0,1.5,2.0 --n 40
```

### 1.2 两个定时任务跑的是同一个全量脚本 (证据: schtasks 实测)

```
\csdn-dashboard-monitor-0000  -> D:\WebArchives\csdn-dashboard\scripts\daily-monitor-and-push.cmd
\csdn-dashboard-monitor-0530  -> D:\WebArchives\csdn-dashboard\scripts\daily-monitor-and-push.cmd
```
两个都指向同一个 .cmd, 而该 .cmd 跑的是 `csdn-monitor-v2.py --force-now` = **全量重抓 385 篇**。

### 1.3 第二次全量确实覆盖了第一次 (证据: history-real/2026-10-21.json)

```
00:00 那次 -> class_stats.csdn.total_views = 7,604  ✅
05:30 那次 -> class_stats.csdn.total_views = 1,297  ❌
```

### 1.4 保护逻辑为什么没拦住

`should_preserve` 的触发条件是 `csdn_fail_count > 0.3 * len(students_state)`,
而 `csdn_fail_count` **只统计列表抓取失败**。
列表抓取 34/34 全成功, 只是**文章详情**被 521 拦了 → `csdn_fail_count = 0` → **保护永远不触发**。

### 1.5 复用逻辑是死代码 (证据: 05:30 那次跑出来的 snapshot)

`fetch_csdn_html` 每次返回**全新的** post 字典, 只有 `{title, link, pubDate, guid}` ——
没有 `views`, 没有 `stats_updated_at` → `aggregate_user_stats` 里那个
`if prev_ts > reuse_threshold and p["views"] > 0` 条件**永远不成立**。

证据: `cache/csdn/snapshot.json` (05:30 那次存的) 里每个学生的
`articles_skipped_reuse` 都是 **0** —— 一篇都没复用上。

### 1.6 增量脚本的正则是坏的 (证据: 实测三个学生的文章)

| 正则 | 增量脚本 | 结果 |
|---|---|---|
| `class="read-count"` | 增量脚本 | 3 篇里只中 1 篇 |
| `class="like-count"` | 增量脚本 | **0/3** |
| `class="comment-count"` | 增量脚本 | **0/3** |
| `class="collect-count"` | 增量脚本 | **0/3** |
| `viewCountFormat = N` | v2 | **3/3** ✅ |
| `文章浏览阅读N次` | v2 | **3/3** ✅ |

### 1.7 ★ v2 的 `span.read` x4 兜底是**张冠李戴** (新发现, 任务文件没提)

v2 里有这么一段兜底:

```python
reads = re.findall(r'<span class="read">\s*([\d,]+)\s*</span>', html)
if len(reads) >= 4:
    stats["collections"] = reads[0]; stats["likes"] = reads[1]; stats["comments"] = reads[2]
```

我把 `<span class="read">` 的上下文原样 dump 出来了:

```
... data-report-click='{"mod":"popu_541",...,"dest":"https://blog.csdn.net/liruisklfj/article/details/142862879","ab":"new"}'>
  Python新手入门指南 解决Python的pip在命令行无法使用的问题
  <img src=".../readCountWhite.png" alt="">
  <span class="read">4958</span>
</a> </li>
```

**这些 span 全在页面右侧"相关推荐/热门文章"侧边栏里**, 数值是**别人文章的阅读量**。
所以李瑞的文章 `168415428` 被写上了 `collections=4958 / likes=677` —— 全是脏数据。

(注: `4958` 恰好也是李瑞自己另一篇文章的真实阅读量, 所以光看数值发现不了,
必须看 DOM 上下文才能发现。)

### 1.8 真正可靠的解析源 (实测 12 篇文章 / 12 个学生)

| 来源 | 命中率 | 取什么 |
|---|---|---|
| `<meta name="description">` → `文章浏览阅读N次，点赞N次，收藏N次` | **12/12** | views + likes + collections |
| `var viewCountFormat = N` | **12/12** | views |
| `<span class="read-count">N 阅读</span>` | 8/12 | views |
| `<span class="read-count" id="blog-digg-num">N</span>` | 8/12 | likes |
| `id="get-collection" ... data-num="N"` | 8/12 | collections |
| `var commentscount = N` | 12/12 | comments |

两个来源合起来覆盖 12/12。

### 1.9 `fetch_user_total_views` 的正则**其实是好的** (修正任务文件的证据 5)

任务文件说"正则失效, profile_views 归零"。我实测发现**不是**:

```
v2 regex#1 -> 359        (Pxh_sky_998)
v2 regex#2 -> None       (第二条正则本来就打不中, 但第一条能中)
v2 fetch_user_total_views() -> 359
```

真实 DOM:
```html
<span title="" class="user-profile-statistics-views" data-v-f8205402>
  <div class="user-profile-statistics-num" data-v-f8205402>359</div>
  <div class="user-profile-statistics-name" data-v-f8205402>总访问量</div>
</span>
```

**真正的原因是限流**: profile 页被风控时, 旧代码 `return 0` —— 而 0 和"没抓到"分不开,
于是 `total_views = profile_views = 0`, 整个人的阅读量被清零。
这正是 2026-10-21 05:30 那次李瑞 `total_views=0` 但 `article_views_sum=878` 的原因。

→ 所以 **B 的真正价值是"失败返回 None + 多层兜底"**, 不是"修正则"。

---
(以下章节: 修改清单 / 增量方案设计 / 验证结果 / 提交与推送 / 遗留问题)

---

## 2. 修改清单 (文件 + 行号 + 改了什么 + 为什么)

### 2.0 新增: `scripts/csdn_stats.py` (公共解析模块, 325 行)

**为什么要有**: v2 和 incremental 各写了一份正则, 两份都坏过, 而且坏法不同
(见 §1.6 / §1.7)。现在两份脚本 import 同一个模块, 不会再各坏各的。

| 函数 | 作用 | 关键点 |
|---|---|---|
| `parse_article_stats(html)` | 文章页 -> views/likes/comments/collections | 主源 `<meta name="description">`, 备份 `viewCountFormat`; **取不到返回 None, 绝不返回 0** |
| `parse_profile_views(html)` | 主页 -> 总访问量 | **三层兜底**: 精确容器 -> 标签前后 200 字符 -> 双向宽松; **失败返回 None** |
| `article_id_from_link(link)` | URL -> article id | 两个脚本共用 |
| `RateLimiter` | 全局限流 | 线程安全 + **自适应退避** (撞 521 就拉长间隔, 顺利了慢慢收回) |

★ 顺手修掉的一个脏数据 bug: 旧的 `<span class="read">` x4 兜底被**整个删掉**,
因为那些数字来自"相关推荐"侧边栏, 是**别人文章的阅读量** (§1.7)。

### 2.1 `scripts/csdn-monitor-v2.py`

| 位置 | 改了什么 | 为什么 |
|---|---|---|
| L47-54 | `from csdn_stats import ...` | 共用解析 |
| L136-139 | `CSDN_MIN_INTERVAL` / `CSDN_LIMITER` / `CSDN_REQUESTS` | 全局限流 + 真实请求计数 |
| L146-183 | 新增 `csdn_get()`: 走全局限流、**识别 521 并冷却**、**自适应退避**、真实计数 | 旧 `http_get` 把 521 当普通响应返回, 然后上层重试 → 白白烧 10 分钟 |
| L299-360 | `fetch_csdn_html`: 新拿到 post 后, 按 link 从 `PREV_POSTS_BY_LINK` **合并上次的 stats** | ★ 这是"24h 复用"能生效的前提。旧代码返回的 post 只有 4 个字段, 复用条件永远不成立 |
| L395-430 | `fetch_user_total_views`: 改用 `parse_profile_views` 三层兜底; **失败返回 None 而不是 0**; 打印明确日志 | 0 和"没抓到"必须能区分, 否则一次风控就把人清零 |
| L486-586 | `aggregate_user_stats`: 新增 `no_reuse` 参数; **连续 5 次失败就放弃该学生**; 失败时用旧值参与求和; **total_views 三源择优**; 返回值里分别保留 `profile_views` / `article_views_sum` / `views_source` | 见 §2.2 (C) 和 §2.5 (F) |
| L856-900 | `write_data_json`: 多写 `profile_views` / `article_views_sum` / `views_source` / `stats_updated_at` | 让报告和前端能看出每个数字的来源 |
| L1161-1175 | `_count_actual_requests` 改用**真实计数器** `CSDN_REQUESTS` | 报告里要拿它对比"增量 vs 全量"的请求数, 估算会算错 |
| L1385-1500 | 新增 `apply_data_protection()` (从 `run()` 里抽出来, 变成可单测的函数); 新增 `merge_posts_stats()` | 见 §2.3 (D) |
| L1445-1469 | `PREV_POSTS_BY_LINK` 建索引的规则改成"**两份里带 stats_updated_at 的那份优先**" | ★ 这是我自己踩的坑, 见 §2.6 |
| L1476-1484 | 线程数 `max_workers=3` -> **2** | 瓶颈是 IP 级风控, 线程多只会更快撞 521 |
| L1610+ | 新增 `--no-reuse` 参数 | 每周日全量兜底要强制每篇重抓 |

### 2.2 (C) total_views 三源择优 — `aggregate_user_stats`

```python
if profile_views is not None and profile_views > 0:
    total_views = profile_views;  views_source = "profile"
elif article_stats["views"] > 0:
    total_views = article_stats["views"]; views_source = "articles"
else:
    total_views = 0; views_source = "none"
```
返回里**同时保留** `profile_views` / `article_views_sum` / `views_source`。

实测效果: 2026-10-21 05:30 那次, 李瑞 `profile_views=0` 但 `article_views_sum=878`
—— 旧代码把 total_views 直接 = profile_views, 于是李瑞的 7254 被清成 0。
新代码在 profile 拿不到时会退到文章累加, **不会再归零**。

### 2.3 (D) 保护逻辑加 "stats 维度" 触发臂 — `apply_data_protection`

旧逻辑只有一条臂 (`csdn_fail_count > 0.3 * N`, 只统计**列表**失败), 列表 34/34 成功时永远不触发。
现在 4 条臂, 任一成立就保护:

| 臂 | 条件 |
|---|---|
| A (原有) | 列表失败率 > 30% **且** 总文章数 < 上次 70% |
| B (新增) | `sum(articles_scanned) < 上次 60%` **且** 上次 > 10 |
| C (新增) | `sum(total_views) == 0` 或 `< 上次 30%` **且** 上次 > 1000 |
| D (新增) | 总 posts < 上次 70% |

触发后保留:
- **整个 post 数组** (含 views/likes/comments/collections/stats_updated_at) —— 用 `merge_posts_stats()`
  合并, 这样既保住老文章的 stats, 又不丢这次新发现的文章, 也不因为一次异常少文章。
- 上次 `total_views > 0` 而这次是 0 时, 保留上次的 total_views。
- 单人级别: 这次比上次腰斩 (阅读量只涨不跌, 腰斩 = 抓取残废) 也回退。

### 2.4 (E) v2 的复用逻辑修好了

`fetch_csdn_html` 拿到的 4 字段 post, 现在会按 link/guid 从 `PREV_POSTS_BY_LINK`
把 `views/likes/comments/collections/stats_updated_at` **合并进来**。
于是 `aggregate_user_stats` 里那个 24h 复用判断终于能成立。

⚠ **我自己踩的坑 (已修)**: 建索引时我一开始让 `snapshot.json` 优先于 `data.json`。
但 `snapshot.json` 只有 v2 跑完才更新, 而**增量脚本只写 data.json** ——
所以增量跑完之后 snapshot 还是 05:30 那份旧的 (只有 14/385 篇有 stats),
一让 snapshot 优先, 371 篇文章就都拿不到 stats, 复用形同虚设。
**实测证据: 全量跑的 CSDN 请求数是 665 (而不是 ~230)。**
修法: 索引规则改成"**两份里带 `stats_updated_at` 的那份优先**",
并且让增量脚本**也写 `cache/csdn/snapshot.json`** (格式跟 v2 一致)。

### 2.5 (F) 限流友好

| 改动 | 位置 | 效果 |
|---|---|---|
| 全局 `RateLimiter` (2.2s + 0.6s 抖动) | 两个脚本 | 跨线程限流, 实测 2.0s 间隔 40/40 通过 |
| **自适应退避** | `RateLimiter.escalate/decay` | 撞 521 -> 间隔 ×1.5 (上限 9s), 连续顺利 -> 慢慢收回基准。实测 521 从 56 次降到 4 次 |
| 521 显式识别 + 全局冷却 | `csdn_get` | 521 不是 404/500, 必须单独判 |
| 线程数 3 -> 2 | `run()` | 瓶颈是 IP 不是线程 |
| **连续 5 次失败就放弃该学生** | `aggregate_user_stats` / `incremental_update_student` | 不再死磕, 走保护逻辑。旧的 CSDN 阶段跑过 627 秒 |
| 每次失败都打印明确日志 | 两个脚本 | 便于诊断 |
| 请求数真实计数 | 两个脚本 | 报告里能对比 |

### 2.6 `scripts/csdn_incremental.py` — 重写 (见 §3)

### 2.7 调度 (.cmd)

| 文件 | 用途 |
|---|---|
| `scripts/incremental-monitor.cmd` | **00:00 增量抓取** + push (新增: 日志重定向、pack flags 避开 push OOM、push 后校验同步) |
| `scripts/gitee-monitor.cmd` | **05:30 只刷 Gitee** + push (新增) |
| `scripts/weekly-full-monitor.cmd` | **周日 03:00 全量兜底** (`--force-now --no-reuse`) + push (新增) |
| `scripts/daily-monitor-and-push.cmd` | 保留给"手动全量"用, 加了 `--no-reuse` 和 pack flags |
| `scripts/setup-schedule-tasks.cmd` | 装/改三个定时任务 (新增, 管理员权限) |

### 2.8 测试与诊断工具 (新增)

| 文件 | 作用 |
|---|---|
| `scripts/test_csdn_stats.py` | 解析 + 保护逻辑自检 (27 条离线 + 8 条联网) |
| `scripts/test_frontend_views.js` | **从 HTML 里原样抠出渲染函数**在 node 里跑真实数据 (8 条) |
| `scripts/probe_csdn_limits.py` | CSDN 限流实测工具 (CSDN 改版后重测用) |

---

---

## 3. 增量方案的具体设计 (Ma 最关心的部分)

### 3.1 先说清楚"增量"到底指什么

**增量 ≠ 省请求。** 增量 = **用可接受的成本, 让阅读量持续更新**。

旧代码的注释写的是:
> "老文章不重抓 stats —— 重新发 HTTP = 浪费 + 容易被限流"

这是把 **"省请求"** 和 **"拿到新数据"** 搞混了。
老文章的阅读量恰恰是**会增长的** —— 实测李瑞的个人总访问量:
`7254 (00:00) -> 7272 -> 7297 -> 7307 -> 7313 -> 7320 -> 7326`,
一天涨了几十。**不重抓 = 阅读量永远停在第一次抓到的值 = Ma 说的"阅读量不增长"。**

### 3.2 每天对每个学生做什么 (四层 + 主页)

| 层 | 抓什么 | 抓几次 | 为什么 |
|---|---|---|---|
| **A. 新文章** | CSDN 列表里有、data.json 里没有的 | **全抓** (全字段 + 全 stats) | 新文章必须立刻有数据, 不然前端永远显示 "—" |
| **B. 最近 N 篇老文章** | 按 pubDate 倒序的前 4 篇 | **每篇都重抓** | ★ 阅读量增长**高度集中**在最新发布的文章上, 这是变化最快的数据, 一天不抓就落后 |
| **C. 轮转 M 篇老文章** | 从"除最近 4 篇以外"的池子里按天轮转挑 2 篇 | **每篇都重抓** | ★ 老文章增长慢, 但**不能完全不更新** (那就退化成旧 bug 了)。轮转保证每篇老文章每隔几天被刷新一次, **不遗漏** |
| **D. 其余老文章** | 剩下的 | **复用上次值** (views 原样保留) | 这些文章基本不再增长, 复用是合理的取舍。它们会在 C 层被周期性校准, 也会在**周日全量**里被完全重刷 |
| **E. 个人主页** | `总访问量` | **每学生 1 次** | 这是 CSDN 官方口径的权威数字, 是仪表盘上 `total_views` 的主来源, 而且**每天都在涨** |

**N = 4, M = 2 是默认值**, 可配 (`--recent` / `--rotate`)。

### 3.3 轮转算法 (确定性, 可复现)

对每个学生:
1. 把"除最近 N 篇以外"的老文章按 `(pubDate, link)` **稳定排序**, 得到轮转池。
2. `offset = (today_ordinal * M) % len(pool)`, 其中 `today_ordinal = datetime.now().toordinal()`
   (连续递增的整数, 不受时区/夏令时影响)。
3. 取 `pool[offset : offset+M]`, 不够就**环绕**从池头再取。

这个设计满足:
- **同一天跑多次结果一致** (幂等, 不会因为重跑而漏掉/重复)
- **每天自动向后移动 M 格**
- 每篇文章最多 `ceil(len(pool) / M)` 天被轮转刷新一次
- 当前规模 (34 人, 平均 11.3 篇/人, 池子平均约 5 篇) → **一轮完整轮转约 2-3 天**

### 3.4 请求数: 与文章总数无关

| | 全量 (旧) | 增量 (新) |
|---|---|---|
| 文章 stats 请求 | 385 (全部重抓) | 34 × (4 + 2) = **204** |
| 个人主页 | 34 | 34 |
| 列表翻页 | ~50 | ~50 |
| **合计** | **~470** | **~288** |
| 撞 521 的概率 | **极高** (实测: 667 次请求 / 35.6 分钟, 36 次 521) | **低** (实测: 529 次请求, 自适应退避后只有 4-15 次 521) |

关键性质: **增量的成本是 O(学生数 × (N+M)), 不是 O(文章总数)**。
现在 385 篇比全量省 39%; 等文章涨到 1000 篇, 增量还是 ~288, 全量要 ~1080。

### 3.5 另外加了一层: "补历史"

2026-10-21 事故现场, 385 篇文章里有 **371 篇从来没抓到过 stats** (前端全是 "—")。
增量脚本多了一层优先级最低的 **"补历史"**: 把 `views` 还是 `None` 的老文章也补上,
但**每人每次最多 3 篇** (34 × 3 = 102 个请求 ≈ 4.3 分钟), 剩下的分摊到后面几天补完。

为什么限 3 篇/次: 一次全补 371 篇要 ~15 分钟, 整个跑批会超时。
限成 3 篇/次后,  backlog 大约 3-4 天清零, 之后每天稳定在 ~290 个请求。

### 3.6 三条兜底

| 兜底 | 触发时机 | 做什么 |
|---|---|---|
| **自适应退避** | 每次撞 521 | 基准间隔 ×1.5 (2.2s -> 9s 上限), 连续顺利再慢慢收回。**实测 521 从 56 次降到 4 次** |
| **连续失败放弃** | 同一学生连续 5 次失败 | 放弃该学生剩余文章, 保留旧值, 走保护逻辑 (不再死磕) |
| **时间预算** | 整个跑批超过 `--max-minutes` (默认 18) | 只截断"补历史"层; "最近 4 篇 + 轮转 2 篇"**保证跑完** (那才是阅读量要涨的部分) |

### 3.7 三个定时任务的分工

| 任务 | 时间 | 跑什么 | 请求数 | 耗时 |
|---|---|---|---|---|
| `\csdn-dashboard-monitor-0000` | 每天 00:00 | **增量** (`csdn_incremental.py --all --recent 4 --rotate 2 --max-minutes 18`) | ~290 | ~12-15 分钟 |
| `\csdn-dashboard-monitor-0530` | 每天 05:30 | **只刷 Gitee** (`csdn-monitor-v2.py --gitee-only`) | ~70 (Gitee, 不碰 CSDN) | ~1 分钟 |
| `\csdn-dashboard-monitor-weekly` | **周日 03:00** | **全量兜底** (`csdn-monitor-v2.py --force-now --no-reuse`) | ~470 | ~20-35 分钟 |

**为什么 05:30 改成只刷 Gitee**: 旧的 00:00 和 05:30 都跑全量 CSDN, 第二次必然被限流,
然后把第一次的好数据覆盖成 0 (7604 -> 1297)。现在两次任务分工明确: 00:00 管 CSDN 阅读量,
05:30 管 Gitee 活跃度, 两者互不干扰。

**为什么周日还要全量**: 增量是**有取舍**的 —— 老文章的数据会慢慢变旧 (轮转一圈 2-3 天),
偶尔会漏掉新文章或已删除的文章。周日全量用来修数据漂移、补漏、把 385 篇全部校准一遍。
`--no-reuse` 保证它是**真全量** (不然周日增量刚抓过的那几篇会被"24h 内已抓过"跳过,
全量就名存实亡了)。

---
