# CSDN 仪表板 索引

> 面向人和 AI 的导航表。**先看这里**。项目说明见 `README.md`。

## 概览

| 项 | 数量 |
|---|---|
| 页面 | 5 |
| 脚本 | 18 |
| 报告 | 35 |
| 历史快照 | 34 |
| 测试项 | 250（全绿）|

## 页面

| 文件 | 用途 |
|---|---|
| `index.html` | 主仪表板（活跃榜/沉睡榜） |
| `analytics.html` | 班级整体分析 |
| `student.html` | 单生详情 |
| `ranking.html` | 排名页 |
| `trend.html` | 历史趋势（读 history-real/） |

## 脚本

### 抓取

- `csdn-monitor-v2.py`
- `csdn_incremental.py`
- `csdn_stats.py`

### 测试

- `test_csdn_fixes.py`
- `test_csdn_stats.py`
- `test_frontend_views.js`
- `test_report_render.py`
- `test_trend_page.js`

### 任务

- `daily-monitor-and-push.cmd`
- `gitee-monitor.cmd`
- `incremental-monitor.cmd`
- `setup-schedule-tasks.cmd`
- `weekly-full-monitor.cmd`

### 部署提交

- `deploy-csdn-dashboard.py`
- `git-commit-zh.sh`

### 其他

- `install-cron.sh`
- `probe_csdn_limits.py`
- `weekly_guard.py`

## 报告

每日报告 `reports/csdn-report-YYYY-MM-DD.md`（最新 10 份）：

- `csdn-report-2026-10-13.md`
- `csdn-report-2026-10-14.md`
- `csdn-report-2026-10-15.md`
- `csdn-report-2026-10-16.md`
- `csdn-report-2026-10-17.md`
- `csdn-report-2026-10-18.md`
- `csdn-report-2026-10-19.md`
- `csdn-report-2026-10-20.md`
- `csdn-report-2026-10-21.md`
- `csdn-report-2026-10-22.md`

修复与验证报告：
- `FIX-2026-10-22.md`

## 历史快照

`history-real/YYYY-MM-DD.json` —— 逐日快照，**只增不改**，是趋势计算的事实来源。
最新 5 份：

- `2026-10-19.json`
- `2026-10-20.json`
- `2026-10-21.json`
- `2026-10-22.json`
- `index.json`

---

## 相关

- 项目说明：`README.md`
- 部署：`DEPLOY.md`（GitHub Pages）/ `ALIYUN_DEPLOY.md`（阿里云）
- 关键约束：绝不能破坏 live checkout；`history-real/` 只增不改
