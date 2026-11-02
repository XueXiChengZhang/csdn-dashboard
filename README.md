# CSDN 学生项目监控仪表板

> 高职 31 名学生 CSDN 博客活跃度可视化 · 纯静态前端 · GitHub Pages 部署

## 先读这个：给 AI 的快速导航

| 你要找什么 | 去哪 |
|---|---|
| 页面源码 | `index.html` / `analytics.html` / `student.html` / `ranking.html` / `trend.html` |
| 当前数据 | `data.json` |
| 历史快照 | `history-real/YYYY-MM-DD.json`（每日一份，只增不改）|
| 抓取脚本 | `scripts/csdn_incremental.py`（增量）/ `scripts/csdn-monitor-v2.py`（全量）|
| 安全网 | `scripts/weekly_guard.py`（backup / check / rollback）|
| 定时任务入口 | `scripts/*.cmd` |
| 每日报告 | `reports/csdn-report-YYYY-MM-DD.md` |
| 修复与验证报告 | `reports/FIX-*.md` |
| 测试 | `scripts/test_*.py` / `scripts/test_*.js`（共 250 项，全绿）|
| 部署 | `DEPLOY.md`（GitHub Pages）/ `ALIYUN_DEPLOY.md`（阿里云）|
| 中文提交 | `scripts/git-commit-zh.sh`（**必须用**，见下）|
| **交付前闸门** | `python scripts/check_ci_gate.py`（**必须过**，见下）|

**数据口径**：`data.json` 是当前状态，`history-real/` 是逐日快照。**趋势计算读 `history-real/`，不要从 `data.json` 反推**。

---

## 页面

| 页面 | 功能 |
|---|---|
| `index.html` | 主仪表板：今日活跃榜 / 沉睡榜 / 趋势图 |
| `analytics.html` | 班级整体分析：发表频次 / 内容质量 / 时间分布 |
| `student.html` | 单生详情：历史轨迹 / 文章列表 / 增长曲线 |
| `ranking.html` | 排名页 |
| `trend.html` | 历史趋势页（读 `history-real/`）|

数据来源：`data.json`（由监控脚本生成）。

**线上地址**：<https://xuexichengzhang.github.io/csdn-dashboard/>
趋势页：<https://xuexichengzhang.github.io/csdn-dashboard/trend.html>

## 设计

- 米黄背景 `#f5f4ed` + 深蓝品牌色 `#1B365D`
- 衬线字体（思源宋体 / SimSun）
- Kami 风格（类印刷品质感）
- 完全响应式（手机 / 平板 / 桌面）

---

## 本地查看

```bash
cd D:\WebArchives\csdn-dashboard
python -m http.server 8765
```

浏览器打开 `http://localhost:8765`。

> ⚠️ 必须用 HTTP 协议。直接双击 `index.html` 用 `file://` 会因 fetch 跨域失败。

---

## 更新数据与部署

```bash
# 1. data.json 由监控脚本写入
# 2. 提交（必须用中文提交脚本，见下）
bash scripts/git-commit-zh.sh "data: 2026-10-23 监控 (30/31 CSDN, 20/31 Gitee)"
# 3. 推送
./deploy.sh        # Git Bash / WSL
# 或
.\deploy.ps1       # PowerShell
```

---

## 目录结构

```
csdn-dashboard/
├── index.html / analytics.html / student.html / ranking.html / trend.html   # 5 个页面
├── data.json                                    # 当前数据
├── history-real/YYYY-MM-DD.json                # 逐日快照（只增不改）
├── scripts/                                     # 抓取、测试、安全网、任务入口
├── reports/                                     # 每日报告 + 修复验证报告
├── logs/                                        # 运行日志
├── cache/                                       # 缓存
├── deploy.sh / deploy.ps1                       # 部署
├── DEPLOY.md / ALIYUN_DEPLOY.md                 # 部署文档
└── README.md                                    # 本文件
```

---

## 自动任务

| 任务 | 时间 | 在哪跑 | 脚本 |
|---|---|---|---|
| 增量抓取 | 每日 00:00 | 本机 | `scripts/incremental-monitor.cmd` |
| Gitee 采集 | 每日 05:10 | **ECS** | `/root/gitee_collect.sh` |
| Gitee 同步 + 汇报 | 每日 05:30 | 本机 | `scripts/gitee-monitor.cmd` |
| 周日全量 + 安全网 | 每周日 03:00 | 本机 | `scripts/weekly-full-monitor.cmd` |

**周日全量安全网**（`scripts/weekly_guard.py`）是数据正确性的唯一保护：
`backup` → `check`（访问量低于 60% 下限或失败率超 20% 则判定异常）→ `rollback`（自动回滚，**且不 push**）。

2026-10-22 已在 live checkout 实跑验证：4 个备份文件齐全，回滚前后 SHA256 一致。

### Gitee 采集为什么跑在 ECS（2026-10-23 起）

本机出口 IP 被 Gitee API 限流封了 —— HTML 页面 200 正常，但
`https://gitee.com/api/v5/...` 一律 `403 Rate Limit Exceeded`。
**根因是调试时打了大量 Gitee API 请求把自己的 IP 封了**（不是账号或仓库问题）。

绕行方案：在 ECS 上跑 `gitee_collector.py`，走两条**不受 API 限流**的通道：

| 通道 | 拿到什么 | 状态 |
|---|---|---|
| `https://gitee.com/<owner>/<repo>.md` | stars / forks / Last Updated | 两个 IP 都 200 |
| `git clone --bare` | sha / date / subject / commit_count | 两个 IP 都通 |

**关键坑**：`git clone --depth 1` 只能看到 1 个 commit，**拿不到真实提交次数**
（实测 shallow=1 / full=2）—— 必须完整 clone + 超时保护。

**数据流**：

```
ECS cron 05:10  →  /root/gitee_result.json
                        ↓ ssh 取回
本机 05:30  →  合并进 data.json  →  commit + push  →  飞书汇报
```

本机脚本里 `load_gitee_from_ecs()` / `merge_ecs_gitee()` 负责取回与合并，
ECS 没覆盖到的才走本机直连补抓。

**效果**：Gitee 阶段 257 秒 → **407 毫秒**，0 次 403，
而且能区分「真空仓库」和「抓取失败」（此前 API 报"成功"的很多其实是空仓库被误判）。

---

## 测试

```bash
cd D:\WebArchives\csdn-dashboard
python scripts/test_csdn_fixes.py      # 抓取层
python scripts/test_csdn_stats.py      # 统计层
python scripts/test_report_render.py   # 报告渲染
node scripts/test_frontend_views.js    # 前端
node scripts/test_trend_page.js        # 趋势页
```

共 250 项，2026-10-22 全绿。

---

## 中文 Commit Message

仓库已配置 `i18n.commitencoding=utf-8` + `.gitattributes` 强制 UTF-8，
但 Windows Git Bash 下直接用 `git commit -m "中文"` 仍可能乱码（bash 重排 argv 字节）。

**推荐用法**：

```bash
bash scripts/git-commit-zh.sh "data: 2026-10-23 监控 (30/31 CSDN, 20/31 Gitee)"
```

或者手动：

```bash
GIT_COMMIT_ENCODING=utf-8 git commit -m "$(cat <<'EOF'
data: 2026-10-23 监控 (30/31 CSDN, 20/31 Gitee)
EOF
)"
```

**绝不**直接 `git commit -m "中文"`（会变 `?????`）。

---

## 不推送到远端的内容

- `history-test/` — 示例数据快照
- `.openclaw/` `credentials/` `*.env` — 本地凭证（`.env.local` **绝不提交**）
- `*.bak-*` — 备份文件
- `__pycache__/` `*.pyc`
- `dashboard-v*.png` 等迭代截图 — 体积大，历史版本可清理

> `history-real/` 现在**已入库**（趋势页依赖它），是趋势计算的事实来源。

---

## 重要约束

- **绝不能破坏 live checkout** —— 实验在独立 worktree 做，验证后合并
- 数据只增不改：`history-real/` 是趋势的事实来源
- 抓取有频率限制，遇 HTTP 521 须退避（`scripts/probe_csdn_limits.py` 是探测器）
- 本机 GitHub 推送走 `ssh.github.com:443`（本机到 `github.com:443` 的 HTTPS 不通）


## 交付前闸门（2026-11-02 起强制）

> **改完任何页面，必须先跑过这个闸门再 commit。**

```bash
python scripts/check_ci_gate.py            # 全查（含浏览器实测，约 40 秒）
python scripts/check_ci_gate.py --quick    # 只查语法+括号（1.5 秒）
```

**检查三件事**：

| 检查 | 抓什么 | 为什么 node --check 不够 |
|---|---|---|
| JS 语法 | `node --check` 每个 `<script>` | 基础防线 |
| **CSS 规则完整性** | 源码大括号配对 + CRCRLF 行尾 | **2026-11-01 真实事故**：`.gitee-sha` 少一个 `}`，导致其后所有 CSS 被浏览器丢弃（`.student-grid { display:grid }` 一起没了），页面从多列网格塌成单列。`node --check` 完全发现不了 —— 那不是 JS 问题 |
| **移动端不溢出** | CDP 真视口 375px 下 `scrollWidth == innerWidth` | headless 的 `--window-size=375` 有窗口装饰补偿，实测 `innerWidth` 给到 504，必须用 CDP `Emulation.setDeviceMetricsOverride` |

**闸门已做故障注入验证**：故意删掉 `index.html` 里一个 `}`，闸门报
`✗ index.html CSS 括号  142 开 / 141 闭 ← 不配对!`（结果 14/15），还原后恢复 15/15。

## 交付流程（写死的分工）

```
谁写代码          → Claude Code (CC) 派单执行
谁验证            → Hermes 独立复验（不采信 CC 的自报）
谁进 Git          → 复验通过后，Hermes commit + push
```

**关键约束**（来自 DeepSeek《Super-agent 设计探索》§7.1 的实验结论）：

> 中性提示下，模型在 5 次运行中一次都没有调用 Subagent —— 所有工作都在主 Session 里做、
> 不断被新消息打断，p95 达 29.5 分钟。

**光在提示词里说「该派子代理」是没用的。** 所以分工必须写成**架构层约束**
（派单时明确限定：改哪个文件、只改哪一处、验收标准是什么），而不是靠临场自觉。

**每次改完页面，必须由 Hermes 独立复验**：CC 的自报不算数，
要用真实浏览器实测拿数字（CSSOM 规则数、computed style、scrollWidth、卡片数）。
