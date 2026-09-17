# CSDN Dashboard 部署记录

> 最后更新: 2026-09-17 · 待 Ma 完成 `gh auth login` 后补全

## 📦 仓库信息

| 项 | 值 |
| --- | --- |
| **GitHub 用户名** | `<待 gh auth login 后填入>` |
| **仓库名** | `csdn-dashboard` |
| **仓库 URL** | `https://github.com/<用户名>/csdn-dashboard` |
| **可见性** | `public` |
| **描述** | 高职学生 CSDN 监控仪表板 |
| **默认分支** | `main` |

## 🌐 GitHub Pages 部署

| 项 | 值 |
| --- | --- |
| **Pages URL** | `https://<用户名>.github.io/csdn-dashboard/` |
| **源分支** | `main` |
| **源路径** | `/` (根目录) |
| **HTTPS 强制** | 已开启(GitHub Pages 默认) |

### 页面访问地址

- 主仪表板: `https://<用户名>.github.io/csdn-dashboard/index.html`
- 班级分析: `https://<用户名>.github.io/csdn-dashboard/analytics.html`
- 学生详情: `https://<用户名>.github.io/csdn-dashboard/student.html`
- 数据 JSON: `https://<用户名>.github.io/csdn-dashboard/data.json`

## 🚀 部署步骤(供 Ma 执行)

### 1. 登录 GitHub

```bash
gh auth login
```

按提示选择:
- `GitHub.com`
- `HTTPS`
- `Login with a web browser`(推荐,免去粘贴 token)

### 2. 创建远端仓库并推送

回到 `D:\WebArchives\csdn-dashboard` 目录,执行:

```bash
cd D:\WebArchives\csdn-dashboard
gh repo create csdn-dashboard --public --source=. --remote=origin --push \
  --description "高职学生 CSDN 监控仪表板"
```

> 本地 git 已初始化并完成首次提交 (`bf9dcba Initial dashboard v8`),直接 push 即可。

### 3. 启用 GitHub Pages

**方式 A — CLI(推荐)**:

```bash
gh repo edit --enable-pages --branch=main --path=/
```

**方式 B — Web**:
1. 打开 `https://github.com/<用户名>/csdn-dashboard/settings/pages`
2. Source: `Deploy from a branch`
3. Branch: `main` / `(root)`
4. Save

### 4. 等待部署

GitHub Pages 首次部署约需 1-3 分钟。可在仓库的 **Actions** 标签页查看进度,
或在 **Settings → Pages** 看到"Your site is live at ..."。

### 5. 验证部署

依次访问:

```
https://<用户名>.github.io/csdn-dashboard/
https://<用户名>.github.io/csdn-dashboard/analytics.html
https://<用户名>.github.io/csdn-dashboard/student.html
https://<用户名>.github.io/csdn-dashboard/data.json
```

> ⚠️ **强制刷新**: GitHub Pages 有 CDN 缓存。如果改了文件没看到效果,
> 用 `Ctrl+Shift+R` / `Cmd+Shift+R` 强制刷新,或在 URL 后加 `?v=2` 绕过缓存。

## 🔄 未来更新流程

### 方式 1: 用部署脚本(推荐)

**Git Bash / WSL**:

```bash
cd /d/WebArchives/csdn-dashboard
./deploy.sh "修复数据加载"
```

**PowerShell**:

```powershell
cd D:\WebArchives\csdn-dashboard
.\deploy.ps1 -Message "修复数据加载"
```

脚本会自动:
1. `git add -A`
2. `git commit -m "<你的消息>"`
3. `git push origin main`
4. GitHub Pages 1-3 分钟内自动重新部署

### 方式 2: 手动

```bash
cd D:\WebArchives\csdn-dashboard
git add -A
git commit -m "你的说明"
git push origin main
```

## ⚠️ 不推送的文件(.gitignore)

| 路径 | 原因 |
| --- | --- |
| `history-test/` | 示例数据快照(脏数据) |
| `history-real/` | 真实历史快照(隐私) |
| `*.bak-*` | 备份文件 |
| `.openclaw/` | OpenClaw 凭证/缓存 |
| `credentials/` | 凭证 |
| `*.env` / `.env*` | 环境变量 |
| `__pycache__/` | Python 缓存 |

## 📁 已部署的文件清单(本次 commit)

```
.gitignore
README.md
analytics.html
data.json
index.html
student.html
dashboard-*.png  (11 张 v1-v8 截图)
```

**首 commit hash**: `bf9dcba`

## 🔧 故障排查

### Q: 访问 URL 显示 404
**A**: Pages 首次部署需要 1-3 分钟。等几分钟再刷。

### Q: 改了文件但页面没更新
**A**: GitHub Pages CDN 缓存。
- `Ctrl+Shift+R` 强制刷新
- 或 URL 加 `?v=N` 绕过缓存

### Q: data.json 加载失败
**A**: 检查浏览器开发者工具 Network 面板。
- 确认 `data.json` 状态码 200
- 确认 CORS 没被拦截(GitHub Pages 允许同源 fetch)

### Q: 想用自定义域名(比如 fuyeboke.com)
**A**:
1. 在仓库根目录创建 `CNAME` 文件,内容写 `fuyeboke.com`
2. 在域名服务商添加 CNAME 记录指向 `<用户名>.github.io`
3. 在 GitHub Pages 设置中勾选 `Enforce HTTPS`

---

**本次部署执行者**: Hermes Agent (subagent)
**部署状态**: ✅ 本地就绪 · ⏳ 远端待 Ma 登录 gh CLI 后完成
