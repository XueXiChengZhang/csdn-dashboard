# CSDN 学生项目监控仪表板

> 高职 31 名学生 CSDN 博客活跃度可视化 · 静态前端 · GitHub Pages 部署

## 📊 项目概述

一个**纯静态**的监控仪表板,展示 31 名学生在 CSDN 平台的博客更新情况。三个页面:

| 页面 | 功能 |
| --- | --- |
| `index.html` | 主仪表板:今日活跃榜 / 沉睡榜 / 趋势图 |
| `analytics.html` | 班级整体分析:发表频次 / 内容质量 / 时间分布 |
| `student.html` | 单生详情:历史轨迹 / 文章列表 / 增长曲线 |

数据来源:`data.json`(由监控脚本生成)。

## 🎨 设计

- 米黄背景 `#f5f4ed` + 深蓝品牌色 `#1B365D`
- 衬线字体(思源宋体 / SimSun)
- Kami 风格(类印刷品质感)
- 完全响应式(手机 / 平板 / 桌面)

## 🚀 本地查看

```bash
cd D:\WebArchives\csdn-dashboard
python -m http.server 8765
```

浏览器打开 `http://localhost:8765`。

> ⚠️ 必须用 HTTP 协议,直接双击 `index.html` 用 `file://` 协议会因 fetch 跨域失败。

## 🌐 GitHub Pages 部署

详见 [DEPLOY.md](./DEPLOY.md)。

部署地址:见 DEPLOY.md 中的 `Pages URL` 字段。

## 🔄 更新数据

```bash
# 1. 更新 data.json(由外部监控脚本写入)
# 2. 推送
./deploy.sh        # Git Bash / WSL
# 或
.\deploy.ps1       # PowerShell
```

## 📁 目录结构

```
csdn-dashboard/
├── index.html          # 主仪表板
├── analytics.html      # 班级分析
├── student.html        # 学生详情
├── data.json           # 学生数据
├── deploy.sh           # Bash 部署脚本
├── deploy.ps1          # PowerShell 部署脚本
├── DEPLOY.md           # 部署文档
├── README.md           # 本文件
└── .gitignore          # Git 忽略规则
```

## ⚠️ 不推送到远端的内容

- `history-test/` — 示例数据快照
- `history-real/` — 真实历史快照(隐私)
- `.openclaw/` `credentials/` `*.env` — 本地凭证
- `*.bak-*` — 备份文件
