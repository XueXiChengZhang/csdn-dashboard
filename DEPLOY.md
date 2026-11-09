# CSDN Dashboard 部署记录

> 最后更新: 2026-11-09 · **单一部署(GitHub Pages)**；Vercel 已停用（国内不可达）

## 📦 仓库信息

| 项 | 值 |
| --- | --- |
| **GitHub 用户名** | `XueXiChengZhang` |
| **仓库名** | `csdn-dashboard` |
| **仓库 URL** | `https://github.com/XueXiChengZhang/csdn-dashboard` |
| **可见性** | `public` |
| **描述** | 高职学生 CSDN 监控仪表板 |
| **默认分支** | `main` |

## 🌐 部署地址一览

| 平台 | URL | 状态 |
| --- | --- | --- |
| **GitHub Pages（唯一线上站）** | `https://xuexichengzhang.github.io/csdn-dashboard/` | ✅ 运行中 |
| ~~Vercel~~ | ~~`https://csdn-dashboard.vercel.app`~~ | ❌ **已停用**（国内不通，2026-11-09） |

> **只用 GitHub Pages。** Vercel 已从部署链路移除，不要再配。原因见下方「⬛ Vercel（已停用）」。

### 线上数据怎么确认是最新的

```bash
curl -s https://xuexichengzhang.github.io/csdn-dashboard/data.json | \
  python -c "import sys,json;d=json.load(sys.stdin);print(d['last_update'])"
```

- 期望：`last_update` 等于**最近一次 cron 跑完的时间**（通常当天 05:30 或 00:45）
- 若停在几天前：说明 cron 的推送段没执行，看 `logs/gitee-push.log` / `logs/incremental-push.log`
- GitHub Pages 对 `data.json` 有 CDN 缓存，刚推完可能要等 1–10 分钟

---

## 🟦 GitHub Pages 部署

| 项 | 值 |
| --- | --- |
| **Pages URL** | `https://xuexichengzhang.github.io/csdn-dashboard/` |
| **源分支** | `main` |
| **源路径** | `/` (根目录) |
| **HTTPS 强制** | 已开启(GitHub Pages 默认) |

### 页面访问地址

- 主仪表板: `https://xuexichengzhang.github.io/csdn-dashboard/index.html`
- 班级分析: `https://xuexichengzhang.github.io/csdn-dashboard/analytics.html`
- 学生详情: `https://xuexichengzhang.github.io/csdn-dashboard/student.html`
- 数据 JSON: `https://xuexichengzhang.github.io/csdn-dashboard/data.json`

---

## ⬛ Vercel（已停用 · 2026-11-09）

> **结论：不要再用。** 本节保留只为解释历史，原配置记录已折叠在下方。

### 为什么停用

| 项 | 实测 |
| --- | --- |
| 域名解析 | `csdn-dashboard.vercel.app` → `108.160.167.167` / `2a03:2880:f136:...` |
| 国内连通性 | ❌ **443 连接超时**（curl 与浏览器两条通道都失败） |
| Ma 的决定 | 放弃 Vercel，统一用 GitHub Pages |

### 代码侧已做的处理

| 项 | 处置 |
| --- | --- |
| `.vercel/`（project.json + README，含 projectId/orgId） | **已删除**（本就在 `.gitignore`，未跟踪） |
| `scripts/deploy-csdn-dashboard.py` 的 vercel 步骤 | `--no-vercel` **默认开启** → 只 `git push`，不再调 `vercel deploy`。需显式传 `--with-vercel` 才会走旧路径（保留可回溯） |
| `vercel.json` / `.vercelignore` | **保留**——GitHub Pages 读不到，删了反而要同步改本文档；无害 |

### 如果将来要在国内用别的平台

优先级建议：**GitHub Pages（现用）→ 国内 OSS/COS 静态托管 → 自建 Nginx**。
不建议再回到任何需要连境外域名的 PaaS。

---

<details>
<summary>历史配置（仅供考古，勿照做）</summary>

### 为什么当初用 Vercel

- ✅ 全球 CDN 边缘缓存,加载速度优于 GitHub Pages
- ✅ 支持自定义 HTTP 头(`Cache-Control` 精细控制 `data.json`)
- ✅ 自动 HTTPS + HTTP/2
- ✅ 与 GitHub 集成: push 后自动部署
- ✅ 免费额度充足(100GB 流量/月,远超本项目需求)

- ✅ 全球 CDN 边缘缓存,加载速度优于 GitHub Pages
- ✅ 支持自定义 HTTP 头(`Cache-Control` 精细控制 `data.json`)
- ✅ 自动 HTTPS + HTTP/2
- ✅ 与 GitHub 集成: push 后自动部署
- ✅ 免费额度充足(100GB 流量/月,远超本项目需求)

### 当前配置

| 项 | 值 |
| --- | --- |
| **配置文件** | `vercel.json`(根目录) |
| **构建命令** | (无,纯静态) |
| **输出目录** | `.` |
| **Framework** | Other (static) |
| **Project name** | `csdn-dashboard` |

### 缓存策略(`vercel.json` 已配置)

| 资源 | Cache-Control | 原因 |
| --- | --- | --- |
| `data.json` | `no-cache, no-store, must-revalidate` | 每次必须拿最新数据 |
| `*.html` | `public, max-age=300, must-revalidate` | 5 分钟缓存,快速迭代 |
| `*.png` / `*.svg` | `public, max-age=31536000, immutable` | 1 年缓存,版本号破缓存 |
| `*.css` / `*.js` | `public, max-age=86400, must-revalidate` | 1 天缓存 |

### 部署步骤

**方式 A — 用 Vercel CLI(本次使用)**

1. **安装 Vercel CLI**

    ```bash
    npm install -g vercel
    ```

2. **获取 token**

    Ma 需要在 Vercel Dashboard 创建 token:
    - 打开 <https://vercel.com/account/tokens>
    - Name: `csdn-dashboard-deploy`
    - Scope: Full Account
    - Expiration: 1 year
    - Create Token → 复制 token(`vcp_csdn-dashboardxxx...`)

    > ⚠️ Vercel 的 GitHub OAuth 登录不能直接给 token 用于 CLI,
    > 必须去 dashboard 创建 Personal Access Token。

3. **在项目目录登录**

    ```bash
    cd D:\WebArchives\csdn-dashboard
    $env:VERCEL_TOKEN="<paste-token-here>"   # PowerShell
    # 或 export VERCEL_TOKEN="..."         # Git Bash
    vercel link --yes
    ```

4. **生产部署**

    ```bash
    vercel deploy --prod --yes
    ```

    输出示例:

    ```
    ✅ Production: https://csdn-dashboard.vercel.app [copied to clipboard]
    ```

5. **绑定自定义域(可选)**

    ```bash
    vercel domains add csdn.fuyeboke.com
    ```

    按提示去域名服务商添加 CNAME 记录即可。

**方式 B — 接入 GitHub 自动部署(推荐长期方案)**

1. 去 <https://vercel.com/new>
2. Import `XueXiChengZhang/csdn-dashboard`
3. Framework Preset: **Other**
4. Build Command: 留空 · Output Directory: `.`
5. Deploy
6. 之后 push 到 `main` → Vercel 自动部署

### 验证部署

部署完成后,访问:

```
https://csdn-dashboard.vercel.app/
https://csdn-dashboard.vercel.app/analytics.html
https://csdn-dashboard.vercel.app/student.html
https://csdn-dashboard.vercel.app/data.json
```

并检查响应头:

```bash
curl -I https://csdn-dashboard.vercel.app/data.json
# 期望: Cache-Control: no-cache, no-store, must-revalidate
```

### 更新流程

**Git Bash / PowerShell 同理**:

```bash
cd /d/WebArchives/csdn-dashboard   # 或 cd D:\WebArchives\csdn-dashboard
vercel deploy --prod --yes
```

或 push 到 GitHub,Vercel 会自动部署(若已接入 GitHub)。

</details>

---

## 🆚 平台对比（供以后选型参考）

| 维度 | **GitHub Pages（现用）** | Vercel（已停用） | 国内 OSS/COS |
| --- | --- | --- | --- |
| **国内可达** | ✅ 可用 | ❌ **443 超时** | ✅ 可用 |
| **URL** | `xuexichengzhang.github.io/csdn-dashboard` | `csdn-dashboard-*.vercel.app` | 自定义域名 |
| **HTTPS** | ✅ 自动 | ✅ 自动 | ✅ 需配 |
| **HTTP 头控制** | ❌ 不支持 | ✅ 支持 | ✅ 支持 |
| **`data.json` 缓存** | 难控（用 `?v=N` 破缓存） | `no-cache` 直生效 | ✅ 可配 |
| **自动部署** | push 触发 | push 触发 | 需 CI |
| **费用** | 免费 | 免费额度 | 按量 |

**结论：只用 GitHub Pages。** 它的唯一痛点是 `data.json` 缓存难控——
本项目用 **URL 加版本号**（`data.json?v=<时间戳>`）绕过，已在 `index.html` 里实现。
Vercel 虽然头控制更好，但**国内连不上，等于不可用**，不参与比较。

---

## 🔒 访问保护决策

### 选项对比

| 方案 | 平台 | 成本 | 适合场景 |
| --- | --- | --- | --- |
| 公开 URL | 任一 | 免费 | 数据不敏感,愿公开 |
| GitHub 私有仓库 | GitHub Pages | 免费 | 仅限协作者 |
| **Vercel Password Protection** | Vercel | **Pro $20/月** | 想公开但限密码访问 |

### 本次决策

**✅ 默认公开 URL**(同 GitHub Pages 一样公开)

**理由**:
1. 数据是公开的 CSDN 用户数据,无敏感隐私
2. 节省 Vercel Pro 费用
3. 如果未来想加保护,再升级 Pro

如果以后要改:
1. Vercel Dashboard → Project → Settings → Password Protection
2. 升级 Pro 计划
3. 设置密码
4. 生效

---

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

> 本地 git 已初始化并完成首次提交,直接 push 即可。

### 3. 启用 GitHub Pages

**方式 A — CLI(推荐)**:

```bash
gh repo edit --enable-pages --branch=main --path=/
```

**方式 B — Web**:
1. 打开 `https://github.com/XueXiChengZhang/csdn-dashboard/settings/pages`
2. Source: `Deploy from a branch`
3. Branch: `main` / `(root)`
4. Save

### 4. 等待部署

GitHub Pages 首次部署约需 1-3 分钟。可在仓库的 **Actions** 标签页查看进度,
或在 **Settings → Pages** 看到"Your site is live at ..."。

### 5. 验证部署

依次访问:

```
https://xuexichengzhang.github.io/csdn-dashboard/
https://xuexichengzhang.github.io/csdn-dashboard/analytics.html
https://xuexichengzhang.github.io/csdn-dashboard/student.html
https://xuexichengzhang.github.io/csdn-dashboard/data.json
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

### 方式 3: 计划任务自动跑（**这才是日常真正的入口**）

**平时不需要手动推**，三个 Windows 计划任务跑完后自己提交并推送：

| 计划任务 | 时间 | 做什么 |
| --- | --- | --- |
| 增量采集 | 每天 00:00 | 抓 CSDN 新文章 + 轮转补爬 |
| Gitee 刷新 | 每天 05:30 | 只刷 Gitee，不动 CSDN |
| 全量兜底 | 每周日 03:00 | 全量重抓（防漂移/漏抓） |

**推送逻辑（2026-11-09 修）**：

```
git push origin HEAD:<当前分支>
```

- ❌ 旧写法 `push origin main` 在 HEAD 不在 main 时**静默推不上去**（曾导致线上数据冻结 4 天、11 个提交卡在本地）
- ✅ 新写法推**当前 HEAD 所在分支**，HEAD 在哪个分支就推哪个
- 推送与同步检查的输出**落日志**：`logs/gitee-push.log` / `logs/incremental-push.log`
- `git add` 改为**逐条执行**——原来一次列 4 个路径，任一个不存在就整体失败、跳过 commit/push

**每天该看什么（3 秒判断线上是否正常）**：

```bash
# 1) 最新数据时间
curl -s https://xuexichengzhang.github.io/csdn-dashboard/data.json | \
  python -c "import sys,json;print(json.load(sys.stdin)['last_update'])"

# 2) 推送段有没有跑过
tail -5 logs/gitee-push.log

# 3) 本地与远端是否对齐
git rev-parse HEAD
git rev-parse origin/main        # 两个应相等
```

> ⚠️ **没有新文章时不会产生提交**（数据没变），此时"线上时间没变"是正常的，不是故障。
> 判据看 `logs/incremental-*.log` 里的 `🆕新N` 是否为 0。

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
| `.vercel/` | Vercel CLI 本地状态(自动生成) |

## 📁 已部署的文件清单

```
.gitignore
.vercelignore
README.md
analytics.html
data.json
index.html
student.html
vercel.json
dashboard-*.png  (历史版本截图)
```

## 🔧 故障排查

### Q: Vercel 部署后访问 404
**A**: 首次部署需要 30-60 秒。`vercel deploy --prod` 输出 URL 后稍等再访问。

### Q: Vercel data.json 还是缓存旧版
**A**: 检查 `vercel.json` 是否提交。Vercel 不会自动用本地的 `vercel.json`,它读 git 仓库根目录的。push 后重试。

### Q: Vercel CLI 报 `No existing credentials found`
**A**: 没设 `VERCEL_TOKEN` 或没跑 `vercel login`。见上文"获取 token"步骤。

### Q: 改了文件但 GitHub Pages 没更新
**A**: GitHub Pages CDN 缓存。
- `Ctrl+Shift+R` 强制刷新
- 或 URL 加 `?v=N` 绕过缓存

### Q: data.json 加载失败
**A**: 检查浏览器开发者工具 Network 面板。
- 确认 `data.json` 状态码 200
- 确认 CORS 没被拦截(Vercel 允许同源 fetch)

### Q: 想用自定义域名(比如 fuyeboke.com)
**A**:

GitHub Pages 路径:
1. 在仓库根目录创建 `CNAME` 文件,内容写 `fuyeboke.com`
2. 在域名服务商添加 CNAME 记录指向 `xuexichengzhang.github.io`
3. 在 GitHub Pages 设置中勾选 `Enforce HTTPS`

Vercel 路径:
1. `vercel domains add fuyeboke.com`
2. 在域名服务商按提示添加 A/CNAME 记录
3. Vercel 自动签 SSL

---

**本次部署执行者**: Hermes Agent (subagent)
**部署状态**: ✅ vercel.json 就绪 · ⏳ Vercel 部署待 Ma 提供 VERCEL_TOKEN