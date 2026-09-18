# 阿里云 ECS 一键部署 CSDN Dashboard

> **最后更新**: 2026-09-17
> **适用**: 阿里云 ECS (Ubuntu 20.04+ / Debian 11+ / **Alibaba Cloud Linux 3** / CentOS 7+ / RHEL 7+ / Amazon Linux 2/2023 / Fedora 36+)
> **前置脚本**: `deploy-to-aliyun.sh` (项目根目录,支持 OS 自动检测)

---

## 📖 为什么需要阿里云 ECS

| 平台 | 在中国境内访问 | 备注 |
| --- | --- | --- |
| **Vercel** (`103.252.115.169`) | ❌ 被 GFW 拦截 | 边缘 IP 不稳 |
| **GitHub Pages** (`185.199.110.153`) | ✅ 可访问 | 慢,缓存控制弱 |
| **阿里云 ECS** (本方案) | ✅✅ 最快 + 稳定 | 中国境内机房,域名/IP 都不被拦 |

本方案在阿里云 ECS 上跑 **nginx + 静态文件**,通过 cron 自动从 GitHub 拉取最新 commit,实现:
- ✅ 公网 IP 访问 (`http://<公网IP>/`)
- ✅ 可选:绑定已备案域名 + 自动 HTTPS (Let's Encrypt)
- ✅ `data.json` 强制 `no-cache`,数据实时刷新
- ✅ 每日 03:00 自动 `git pull`,无需人工介入

---

## 🚀 三步部署

### Step 0 — 选择镜像 (影响脚本走 apt 还是 yum/dnf)

`deploy-to-aliyun.sh` 会读 `/etc/os-release` 自动选择包管理器。下表是各镜像的对应分支:

| OS (镜像 ID) | 包管理器 | 防火墙 | Nginx 用户 | 默认 site 配置目录 |
| --- | --- | --- | --- | --- |
| Ubuntu / Debian | `apt-get` | `ufw` | `www-data` | `/etc/nginx/sites-available` + `sites-enabled` 符号链接 |
| **Alibaba Cloud Linux 3** (`alinux`) | `dnf` (优先) | `firewall-cmd` | `nginx` | `/etc/nginx/conf.d/*.conf` (被 `nginx.conf` 默认 include) |
| CentOS 7 | `yum` | `firewall-cmd` | `nginx` | `/etc/nginx/conf.d/*.conf` |
| CentOS 8+ / RHEL 8+ / Rocky / Alma / OL | `dnf` | `firewall-cmd` | `nginx` | `/etc/nginx/conf.d/*.conf` |
| Amazon Linux 2 / 2023 (`amzn`) | `yum` (AL2) / `dnf` (AL2023) | `firewall-cmd` | `nginx` | `/etc/nginx/conf.d/*.conf` |
| Fedora 36+ | `dnf` | `firewall-cmd` | `nginx` | `/etc/nginx/conf.d/*.conf` |

脚本里手动指定包管理器是不必要的 — 它会自适应。但如果你要在脚本跑之前手动验证环境,可以用 `cat /etc/os-release` 看 `ID=` 那一行。

> **Ma 的 ECS 镜像**: Alibaba Cloud Linux 3.2104 LTS (`ID=alinux`),走 dnf + firewall-cmd 分支。脚本会自动安装 `epel-release`(certbot 的依赖)。

---

### Step 1 — 准备阿里云 ECS

#### 1.1 购买 ECS

| 项 | 推荐配置 |
| --- | --- |
| 镜像 | Ubuntu 22.04 LTS / **Alibaba Cloud Linux 3.2104 LTS** (64位) 任选 |
| 规格 | 1 vCPU / 1 GiB / 40 GiB SSD 即可 (本项目静态站几乎不吃资源) |
| 带宽 | 按量付费 / 5Mbps 固定带宽 (够用) |
| 地域 | 离用户最近的:华北 2 (北京) / 华东 1 (杭州) / 华南 1 (深圳) |
| 公网 IP | 必须分配 |

#### 1.2 安全组 (关键!)

阿里云控制台 → ECS → 实例 → 安全组 → 配置规则:

| 方向 | 协议 | 端口 | 来源 | 说明 |
| --- | --- | --- | --- | --- |
| 入方向 | SSH (TCP 22) | 22 | `0.0.0.0/0` 或你的 IP | SSH 登录 |
| 入方向 | HTTP (TCP 80) | 80 | `0.0.0.0/0` | HTTP 访问 |
| 入方向 | HTTPS (TCP 443) | 443 | `0.0.0.0/0` | HTTPS 访问 (可选) |
| 出方向 | ALL | - | `0.0.0.0/0` | 默认就够 |

> ⚠️ **不要**在安全组只放行 22! 否则你配 nginx 后 80/443 还是不通。

#### 1.3 SSH 登录

在本地 Windows PowerShell / Git Bash:

```bash
ssh root@<你的ECS公网IP>
# 或: ssh -i ~/.ssh/aliyun-key.pem root@<公网IP>  (密钥登录)
```

#### 1.4 验证网络

```bash
# 国内源测速
curl -fsS https://github.com -o /dev/null && echo "GitHub OK"
curl -fsS https://mirrors.aliyun.com -o /dev/null && echo "Aliyun mirror OK"
```

---

### Step 2 — 上传并运行部署脚本

#### 2.1 上传脚本

**方式 A — scp (推荐)**

```bash
# 本地 PowerShell / Git Bash
scp D:/WebArchives/csdn-dashboard/deploy-to-aliyun.sh root@<ECS_IP>:/root/
```

**方式 B — 服务器上直接写**

```bash
ssh root@<ECS_IP>
nano /root/deploy-to-aliyun.sh
# 粘贴整个脚本内容,Ctrl+O 保存,Ctrl+X 退出
```

#### 2.2 先 dry-run 看一遍

```bash
chmod +x /root/deploy-to-aliyun.sh
/root/deploy-to-aliyun.sh --dry-run
```

这会打印出脚本将做的所有事,但不实际执行。看完确认无误再继续。

#### 2.3 正式部署 (HTTP only)

```bash
/root/deploy-to-aliyun.sh
```

输出末段会显示:
```
╔══════════════════════════════════════════════════════════════╗
║           🎉  CSDN Dashboard 部署完成!                       ║
╚══════════════════════════════════════════════════════════════╝

📍 访问地址
   • IP:    http://<ECS公网IP>/

🔧 管理命令
   • 查看状态:    systemctl status nginx
   • 重启 nginx:  systemctl restart nginx
   ...
```

浏览器访问 `http://<ECS公网IP>/` 应能看到仪表板。

#### 2.4 (可选) 启用 HTTPS

**前置条件**:
1. 你有一个已备案的域名 (阿里云备案约 7-15 天)
2. 域名 A 记录已解析到 ECS 公网 IP (生效需要几分钟到几小时)

**验证域名解析**:

```bash
dig +short fuyeboke.com
# 应输出 ECS 公网 IP
```

**运行脚本**:

```bash
/root/deploy-to-aliyun.sh --domain fuyeboke.com --email your@email.com
```

脚本会自动:
1. 用 certbot 申请 Let's Encrypt 证书
2. 修改 nginx 配置启用 443 + 强制 HTTPS 跳转
3. 写自动续期 cron

**验证 HTTPS**:

```bash
curl -I https://fuyeboke.com/data.json
# 应返回: HTTP/2 200, Cache-Control: no-cache, no-store, must-revalidate
```

---

### Step 3 — 验证 + 日常运维

#### 3.1 验证清单

```bash
# 1. nginx 状态
systemctl status nginx

# 2. nginx 配置语法
nginx -t

# 3. HTTP 响应
curl -I http://<ECS_IP>/index.html
curl -I http://<ECS_IP>/data.json
curl -I http://<ECS_IP>/analytics.html
curl -I http://<ECS_IP>/student.html

# 4. 站点文件
ls -la /var/www/csdn-dashboard/

# 5. Cron 任务
cat /etc/cron.d/csdn-dashboard-update
crontab -l | grep csdn

# 6. ufw 防火墙
ufw status
```

#### 3.2 日常更新

**自动**: 每天 03:00 自动 `git pull` + `nginx reload`,无需操作。

**手动** (立即拉取最新代码):

```bash
sudo /usr/local/bin/csdn-dashboard-update.sh
tail -f /var/log/csdn-update.log
```

#### 3.3 重新运行整个脚本 (幂等)

脚本是幂等的,可以重复运行:

```bash
sudo /root/deploy-to-aliyun.sh --domain fuyeboke.com
```

会跳过已完成的步骤,只补做缺失的部分。

---

## 🔧 故障排查

### Q1: 访问 `http://<ECS_IP>` 还是 502 / 拒绝连接

**A**: 按顺序检查:

```bash
# 1. nginx 启动了吗?
systemctl status nginx
# 若未启动:  systemctl restart nginx && journalctl -xeu nginx

# 2. 80 端口在监听吗?
ss -ltn | grep ':80 '
# 若没有: 检查 nginx 配置 - nginx -t

# 3. 防火墙放行了吗? (Debian/Ubuntu 用 ufw,RHEL/AL3 用 firewall-cmd)
# Debian/Ubuntu:
ufw status | grep 80
#   若 inactive: ufw enable && ufw allow 80/tcp
# Alibaba Cloud Linux 3 / CentOS / RHEL:
firewall-cmd --list-all | grep -E '(http|https|ssh|80|443)'
#   若没放行: firewall-cmd --permanent --add-service=http --add-service=https && firewall-cmd --reload

# 4. 阿里云安全组放行了吗? (90% 的坑在这里!)
#   控制台 → ECS → 实例 → 安全组 → 入方向 → 手动添加 TCP:80 0.0.0.0/0
```

### Q2: HTTP 通了,但 `data.json` 返回 404

**A**: 检查文件是否被 git clone 进来:

```bash
ls -la /var/www/csdn-dashboard/data.json
# 若不存在: cd /var/www/csdn-dashboard && sudo git pull
```

### Q3: `data.json` 返回旧数据 (缓存)

**A**: 验证响应头:

```bash
curl -I http://<ECS_IP>/data.json | grep -i cache
# 期望: Cache-Control: no-cache, no-store, must-revalidate
```

若缓存头缺失,检查:

```bash
cat /etc/nginx/sites-enabled/csdn-dashboard | grep -A 2 data.json
```

若无 `location = /data.json` 块,重新跑 `deploy-to-aliyun.sh`。

### Q4: HTTPS certbot 失败 (`Domain does not exist`)

**A**:

```bash
# 1. 确认 A 记录
dig +short fuyeboke.com
# 须返回 ECS 公网 IP

# 2. 确认安全组放行 80 (certbot http-01 challenge 需要 80 端口)

# 3. 确认域名未备案也能用 (Let's Encrypt 不查备案)
#    备案是国内法规要求,但 Let's Encrypt 只查 DNS 指向

# 4. 重试
sudo certbot --nginx -d fuyeboke.com
```

### Q5: 自动更新 cron 没跑

**A**:

```bash
# 1. cron 服务在跑吗?
# Debian/Ubuntu:
systemctl status cron
# Alibaba Cloud Linux 3 / CentOS / RHEL:
systemctl status crond

# 2. cron 文件存在吗?
ls -la /etc/cron.d/csdn-dashboard-update
cat /etc/cron.d/csdn-dashboard-update

# 3. 手动跑一次
sudo /usr/local/bin/csdn-dashboard-update.sh

# 4. 查看 cron 日志
# Debian/Ubuntu:
grep CRON /var/log/syslog | tail -20
# RHEL 系:
grep CRON /var/log/cron | tail -20
```

### Q6: 想换域名

```bash
# 1. 解析新域名到 ECS IP
# 2. 重跑脚本
sudo /root/deploy-to-aliyun.sh --domain new-domain.com

# 3. 若要删旧证书
sudo certbot delete --cert-name old-domain.com
```

### Q7: nginx 默认页出现,而不是 csdn-dashboard

**A**: 默认 site 没禁用:

```bash
ls -la /etc/nginx/sites-enabled/
# 若有 default,移走:
sudo mv /etc/nginx/sites-enabled/default /etc/nginx/sites-enabled/default.bak
sudo systemctl reload nginx
```

`deploy-to-aliyun.sh` 会自动处理这个,但如果你手动改过 nginx 配置,可能需要再跑一次。

### Q9: 用了 Alibaba Cloud Linux 3 / RHEL 系镜像,跑脚本时遇到问题

**A**: 几个常见情况:

```bash
# 1. 验证 OS 自动检测到了 alinux / centos / rhel
cat /etc/os-release | grep ^ID=
#   期望: ID=alinux  (或 centos / rhel / amzn)

# 2. EPEL 装了吗? (certbot 需要)
rpm -q epel-release
# 若没装: dnf install -y epel-release    (AL3/CentOS 8+/RHEL 8+)
#         yum install -y epel-release    (CentOS 7/AL2)

# 3. firewalld 跑了吗? (RHEL 系默认防火墙)
systemctl status firewalld
# 若没跑: systemctl enable --now firewalld
#        firewall-cmd --permanent --add-service={ssh,http,https}
#        firewall-cmd --reload

# 4. crond 跑了吗? (脚本自动写 /etc/cron.d/...,但服务要 up 才会读)
systemctl status crond
# 若没跑: systemctl enable --now crond

# 5. nginx 配置路径不是 sites-enabled,而是 conf.d
ls /etc/nginx/conf.d/
#   应该看到 csdn-dashboard.conf
#   (脚本会自动写到 /etc/nginx/conf.d/csdn-dashboard.conf)

# 6. nginx -t 报 "duplicate default server"
#    说明 RHEL 自带的 /etc/nginx/conf.d/default.conf 还在,脚本通常会备份它
ls /etc/nginx/conf.d/default.conf
#   若还在,手移走: mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.bak
#   然后: nginx -t && systemctl restart nginx
```

**重新部署最干净的姿势**: 在 AL3 / RHEL 上重新跑脚本是幂等的,但建议先清掉旧产物:

```bash
sudo dnf remove -y nginx certbot python3-certbot-nginx firewalld 2>/dev/null || true
sudo rm -rf /etc/nginx/conf.d/csdn-dashboard.conf /etc/nginx/conf.d/default.conf.bak
sudo rm -f /etc/cron.d/csdn-dashboard-update /usr/local/bin/csdn-dashboard-update.sh
sudo reboot   # 重启让包管理器清理干净 (可选)
```

然后重新 scp + 跑 `sudo ./deploy-to-aliyun.sh`。

### Q10: 想用阿里云 OSS + CDN 替代 ECS

这是另一个方案 — OSS 静态托管 + CDN 加速,成本更低 (几块钱/月),但配置更复杂。
本脚本只覆盖 ECS 自建 nginx 场景。

---

## ⚡ 性能优化建议

### 1. 启用 nginx gzip (脚本已默认开启)

`/etc/nginx/nginx.conf` 中:

```nginx
gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_types text/plain text/css application/json application/javascript text/xml application/xml image/svg+xml;
```

### 2. HTTP/2 (HTTPS 后自动启用)

```nginx
listen 443 ssl http2;
```

现代浏览器会优先用 HTTP/2 多路复用。脚本中 nginx 已默认 `listen 443 ssl http2` (在 certbot 注入后)。

### 3. 浏览器缓存 (脚本已默认开启)

| 资源 | 缓存时间 |
| --- | --- |
| `data.json` | `no-cache` (每次都拉新) |
| `*.html` | 5 分钟 |
| `*.css` / `*.js` | 1 天 |
| `*.png` / `*.svg` | 1 年 |

### 4. 阿里云 CDN 加速 (高级)

ECS 单机带宽有限 (1-5Mbps),若用户量大,在 ECS 前挂一层阿里云 CDN:

1. 阿里云控制台 → CDN → 添加域名
2. 源站信息: ECS 公网 IP (或 OSS bucket)
3. 缓存策略: 同 nginx 配置
4. HTTPS: 上传证书或用阿里云免费证书
5. 把域名 CNAME 到 CDN 域名

### 5. 阿里云 OSS 静态托管 (更省钱)

| | ECS 自建 nginx | OSS 静态托管 + CDN |
| --- | --- | --- |
| 月成本 (小流量) | ¥30+ (ECS) | ¥3 (OSS) + ¥0 (CDN 0-20GB 免费) |
| HTTPS | certbot 续期 | 阿里云免费证书 |
| 更新方式 | `git pull` + nginx reload | `ossutil cp` / git-oss-sync |
| 适用 | 学习、灵活 | 生产、低成本 |

### 6. nginx worker 调优

```bash
# 查看 CPU 核心数
nproc
# 一般 1 核就够了,worker_processes auto;
```

编辑 `/etc/nginx/nginx.conf`:

```nginx
worker_processes auto;
worker_connections 1024;
```

### 7. 关闭 access log (隐私 + 性能)

```bash
# 仅对静态资源,保留主访问日志
sed -i 's|access_log /var/log/nginx/csdn-dashboard.access.log;|# access_log off;|' /etc/nginx/sites-available/csdn-dashboard
systemctl reload nginx
```

### 8. 配置 swap (小内存 ECS 推荐)

```bash
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### 9. 用 systemd-timer 替代 cron (更现代)

cron 偶尔会漏跑,systemd-timer 更可靠:

```ini
# /etc/systemd/system/csdn-dashboard-update.timer
[Unit]
Description=Daily update csdn-dashboard

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

(本脚本默认用 cron,简单稳定。)

---

## 📂 部署后的关键路径

```
/var/www/csdn-dashboard/        # 站点根目录 (git clone 在此)
/etc/nginx/sites-available/csdn-dashboard   # nginx 主配置
/etc/nginx/sites-enabled/csdn-dashboard     # (符号链接)
/etc/cron.d/csdn-dashboard-update           # cron 自动更新
/usr/local/bin/csdn-dashboard-update.sh     # 更新脚本本体
/var/log/csdn-deploy.log                    # 部署日志
/var/log/csdn-update.log                    # 自动更新日志
/var/log/nginx/csdn-dashboard.access.log    # nginx 访问日志
/var/log/nginx/csdn-dashboard.error.log     # nginx 错误日志
```

---

## 🔐 安全建议

1. **改 SSH 端口 + 禁用密码登录**
   ```bash
   sed -i 's/#Port 22/Port 2222/' /etc/ssh/sshd_config
   sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
   systemctl restart sshd
   ```
   然后阿里云安全组放行 TCP 2222,放掉 22。

2. **fail2ban 防 SSH 爆破**
   ```bash
   apt install -y fail2ban
   systemctl enable fail2ban
   ```

3. **定期安全更新** (apt 系:apt upgrade / RHEL 系:dnf update)
   ```bash
   # Debian/Ubuntu:
   cat > /etc/cron.daily/apt-security-update <<'EOF'
   #!/bin/bash
   apt-get update -qq && apt-get upgrade -y -qq
   EOF
   chmod +x /etc/cron.daily/apt-security-update

   # Alibaba Cloud Linux 3 / CentOS / RHEL:
   cat > /etc/cron.daily/dnf-security-update <<'EOF'
   #!/bin/bash
   dnf -y update --security
   EOF
   chmod +x /etc/cron.daily/dnf-security-update
   ```

4. **数据备份 (csdn-dashboard 已在 GitHub,无需额外备份仓库;但 data.json.bak-* 在 .gitignore 中)** — 可选:每天 cron tar 到 OSS。

---

## 🔄 升级 / 迁移路径

### 从 ECS 迁移到 OSS

如果未来想省成本,迁移路径:

1. 创建 OSS bucket + 静态托管
2. 写 `ossutil sync` 同步脚本
3. CDN 指向 OSS bucket
4. 释放 ECS

`ossutil` 配置示例:

```bash
ossutil config -e oss-cn-hangzhou.aliyuncs.com -k <AccessKey> -i <SecretKey>
ossutil cp -r /var/www/csdn-dashboard/ oss://csdn-dashboard/
```

### 从 GitHub Pages 切到阿里云

在 README 中更新访问链接即可。GitHub Pages 保留作为冗余。

---

## 📝 脚本执行逻辑摘要 (10 步)

| 步骤 | 动作 |
| --- | --- |
| 1 | 前置检查 (root / **OS 自动检测** / 网络 / 端口) |
| 2 | **apt 系**:`apt-get install nginx git curl ufw certbot python3-certbot-nginx`<br>**RHEL 系**:`dnf install epel-release + nginx git curl firewalld certbot python3-certbot-nginx` + `systemctl enable nginx/firewalld/crond` |
| 3 | 创建 `/var/www/csdn-dashboard/`,`git clone` 仓库 (已存在则 reset --hard),`chown -R www-data:www-data` 或 `nginx:nginx` |
| 4 | 写 nginx server block<br>**Debian**:`/etc/nginx/sites-available/csdn-dashboard` + `sites-enabled` 符号链接,`listen 80 default_server`<br>**RHEL**:`/etc/nginx/conf.d/csdn-dashboard.conf`,`listen 80` (不重复 default_server) |
| 5 | 写 `/etc/cron.d/csdn-dashboard-update` + `/usr/local/bin/csdn-dashboard-update.sh` (每天 03:00 git pull + nginx reload),**RHEL 额外 `systemctl start crond`** |
| 6 | (可选) certbot 申请 Let's Encrypt 证书并自动 reload |
| 7 | **Debian**:`ufw allow 22/80/443` + `ufw --force enable`<br>**RHEL**:`firewall-cmd --permanent --add-service={ssh,http,https}` + `--reload` |
| 8 | `nginx -t` + `systemctl restart nginx` |
| 9 | 自检 HTTP 状态码 + data.json Cache-Control 头 |
| 10 | 打印最终 URL、管理命令、关键路径 |

---

**部署执行者**: Hermes Agent (subagent)
**测试结果**: `bash -n` 语法通过 ✅ (适配 Alibaba Cloud Linux 3)
**部署状态**: ⏳ 待 Ma 在 ECS 上执行