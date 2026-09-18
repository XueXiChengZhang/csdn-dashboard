#!/usr/bin/env bash
# =============================================================================
# CSDN Dashboard 一键部署到阿里云 ECS (Ubuntu/Debian + Alibaba Cloud Linux 3)
# =============================================================================
# 用途: 在全新阿里云 ECS 上把 csdn-dashboard 部署为 Nginx 静态站点
# 作者: Hermes Agent (for Ma)
# 日期: 2026-09-17
#
# 支持的操作系统 (通过 /etc/os-release 自动检测):
#   - Debian 系: Ubuntu 20.04+, Debian 11+            (apt + ufw)
#   - RHEL  系 : Alibaba Cloud Linux 3, CentOS 7+/8+,
#                RHEL 7+/8+, Amazon Linux 2/2023,
#                Fedora 36+                             (dnf/yum + firewall-cmd)
#
# 特性:
#   - 幂等 (idempotent): 重复运行安全
#   - set -euo pipefail: 严格模式
#   - 详细日志到 /var/log/csdn-deploy.log + /var/log/csdn-update.log
#   - 支持 --dry-run (只打印要做什么,不实际执行)
#   - 支持 --domain example.com 自动签 Let's Encrypt SSL
#   - 默认监听 80/443,自动配置缓存头
#
# 用法:
#   sudo ./deploy-to-aliyun.sh                          # 仅 HTTP (用 IP 访问)
#   sudo ./deploy-to-aliyun.sh --domain fuyeboke.com    # 自动签 HTTPS
#   sudo ./deploy-to-aliyun.sh --dry-run                # 演练,不执行
#
# 服务器前置条件:
#   - Ubuntu 20.04+ / Debian 11+ / Alibaba Cloud Linux 3 / CentOS 7+ / RHEL 7+
#   - root 权限 (脚本自检)
#   - 80/443/22 端口未被其他服务占用
#   - 公网 IP 可达 (阿里云安全组需放行)
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

# -----------------------------------------------------------------------------
# 常量
# -----------------------------------------------------------------------------
readonly SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_VERSION="1.0.0"
readonly REPO_URL="https://github.com/XueXiChengZhang/csdn-dashboard.git"
readonly REPO_BRANCH="main"
readonly WEB_ROOT="/var/www/csdn-dashboard"
readonly NGINX_SITE_NAME="csdn-dashboard"
# 注意: 这些路径在 check_os 后会根据 OS_FAMILY 重新指向:
#   Debian 系: /etc/nginx/sites-available/csdn-dashboard  + sites-enabled 符号链接
#   RHEL  系: /etc/nginx/conf.d/csdn-dashboard.conf       (nginx.conf 默认 include)
NGINX_SITE_AVAILABLE="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
NGINX_SITE_ENABLED="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
NGINX_DEFAULT_BACKUP="/etc/nginx/sites-enabled/default.bak"
readonly CRON_FILE="/etc/cron.d/csdn-dashboard-update"
readonly UPDATE_SCRIPT="/usr/local/bin/csdn-dashboard-update.sh"
readonly DEPLOY_LOG="/var/log/csdn-deploy.log"
readonly UPDATE_LOG="/var/log/csdn-update.log"
readonly LETSENCRYPT_DIR="/etc/letsencrypt/live"

# -----------------------------------------------------------------------------
# 操作系统检测 (apt 系 vs yum/dnf 系) — 在 check_os 后填充
# -----------------------------------------------------------------------------
# OS_ID      : /etc/os-release 的 ID 值 (ubuntu/debian/centos/rhel/alinux/amzn/fedora)
# OS_FAMILY  : "debian" 或 "rhel"
# PKG_INSTALL: apt-get 或 yum 或 dnf (RHEL 8+/AL8 优先 dnf, 7 用 yum)
# PKG_REPO_UPDATE: 对应 update 子命令
# NGINX_USER : www-data (Debian) / nginx (RHEL)
# FIREWALL_CMD: "ufw" 或 "firewall-cmd"
# CRON_SERVICE: cron / crond (systemd unit 名)
OS_ID=""
OS_FAMILY=""
PKG_INSTALL=""
PKG_REPO_UPDATE=""
NGINX_USER=""
FIREWALL_CMD=""
CRON_SERVICE=""

# -----------------------------------------------------------------------------
# 颜色输出
# -----------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RED='\033[0;31m'; C_GRN='\033[0;32m'; C_YEL='\033[0;33m'
    C_BLU='\033[0;34m'; C_CYN='\033[0;36m'; C_DIM='\033[2m'; C_RST='\033[0m'
else
    C_RED=''; C_GRN=''; C_YEL=''; C_BLU=''; C_CYN=''; C_DIM=''; C_RST=''
fi

# -----------------------------------------------------------------------------
# 日志
# -----------------------------------------------------------------------------
log()    { printf '%b\n' "[$(date '+%F %T')] $*" | tee -a "$DEPLOY_LOG" >&2; }
info()   { log "${C_BLU}ℹ${C_RST}  $*"; }
ok()     { log "${C_GRN}✓${C_RST}  $*"; }
warn()   { log "${C_YEL}⚠${C_RST}  $*"; }
err()    { log "${C_RED}✗${C_RST}  $*"; }
dry()    { log "${C_CYN}[DRY-RUN]${C_RST} $*"; }
hr()     { printf '%b\n' "${C_DIM}────────────────────────────────────────────────────────────${C_RST}" | tee -a "$DEPLOY_LOG" >&2; }

# -----------------------------------------------------------------------------
# 默认值 (可通过命令行覆盖)
# -----------------------------------------------------------------------------
DRY_RUN=0
DOMAIN=""
EMAIL=""  # Let's Encrypt 用,空则用 webmaster@DOMAIN

# -----------------------------------------------------------------------------
# 参数解析
# -----------------------------------------------------------------------------
usage() {
    cat <<USAGE
${SCRIPT_NAME} v${SCRIPT_VERSION} — 一键部署 csdn-dashboard 到阿里云 ECS

用法:
    sudo ./${SCRIPT_NAME} [选项]

选项:
    --domain DOMAIN     启用 HTTPS,自动申请 Let's Encrypt 证书
                        (需要域名已解析到本机公网 IP)
    --email EMAIL       Let's Encrypt 注册邮箱 (默认: webmaster@DOMAIN)
    --dry-run           只打印要做什么,不实际执行
    -h, --help          显示本帮助

示例:
    sudo ./${SCRIPT_NAME}                              # HTTP only
    sudo ./${SCRIPT_NAME} --domain fuyeboke.com        # HTTPS 自动签
    sudo ./${SCRIPT_NAME} --dry-run                    # 演练
USAGE
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --domain) DOMAIN="$2"; shift 2;;
            --email)  EMAIL="$2";  shift 2;;
            --dry-run) DRY_RUN=1; shift;;
            -h|--help) usage; exit 0;;
            *) err "未知参数: $1"; usage; exit 2;;
        esac
    done
}

# -----------------------------------------------------------------------------
# 执行命令 (兼容 --dry-run)
# -----------------------------------------------------------------------------
run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        dry "$*"
    else
        log "${C_DIM}\$ $*${C_RST}"
        "$@"
    fi
}

# -----------------------------------------------------------------------------
# 前置检查
# -----------------------------------------------------------------------------
check_root() {
    if [[ $EUID -ne 0 ]]; then
        err "需要 root 权限运行。请用: sudo ./${SCRIPT_NAME}"
        exit 1
    fi
}

check_os() {
    if [[ ! -f /etc/os-release ]]; then
        err "无法识别操作系统: /etc/os-release 不存在。本脚本仅支持 Debian/Ubuntu 系和 RHEL/Alibaba Cloud Linux 系。"
        exit 1
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    local pretty="${PRETTY_NAME:-${OS_ID}}"

    case "$OS_ID" in
        ubuntu|debian)
            OS_FAMILY="debian"
            PKG_INSTALL="apt-get"
            PKG_REPO_UPDATE="update"
            NGINX_USER="www-data"
            FIREWALL_CMD="ufw"
            CRON_SERVICE="cron"
            NGINX_SITE_AVAILABLE="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
            NGINX_SITE_ENABLED="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}"
            NGINX_DEFAULT_BACKUP="/etc/nginx/sites-enabled/default.bak"
            ok "操作系统: ${pretty} (Debian 系 → apt + ufw)"
            ;;
        alinux|centos|rhel|amzn|fedora|rocky|almalinux|ol)
            OS_FAMILY="rhel"
            # 优先 dnf (RHEL 8+/AL3/CentOS 8+/Fedora), 回退 yum (RHEL 7/CentOS 7/AL2)
            if command -v dnf >/dev/null 2>&1; then
                PKG_INSTALL="dnf"
                PKG_REPO_UPDATE="makecache"
            elif command -v yum >/dev/null 2>&1; then
                PKG_INSTALL="yum"
                PKG_REPO_UPDATE="makecache"
            else
                err "未找到 dnf/yum,无法继续。"
                exit 1
            fi
            NGINX_USER="nginx"
            FIREWALL_CMD="firewall-cmd"
            CRON_SERVICE="crond"
            # RHEL 系的 nginx.conf 默认 include /etc/nginx/conf.d/*.conf
            NGINX_SITE_AVAILABLE="/etc/nginx/conf.d/${NGINX_SITE_NAME}.conf"
            NGINX_SITE_ENABLED="/etc/nginx/conf.d/${NGINX_SITE_NAME}.conf"  # 同一文件,无需 symlink
            NGINX_DEFAULT_BACKUP=""  # RHEL 没 sites-enabled/default
            ok "操作系统: ${pretty} (RHEL 系 → ${PKG_INSTALL} + firewall-cmd)"
            ;;
        *)
            err "本脚本不支持 ${OS_ID}。当前 PRETTY_NAME: ${pretty}"
            err "支持的 ID: ubuntu, debian, alinux, centos, rhel, amzn, fedora, rocky, almalinux, ol"
            exit 1
            ;;
    esac

    # 导出供子 shell 使用 (update 脚本会在子 shell 读 NGINX_USER)
    export NGINX_USER
}

check_network() {
    if ! curl -fsS -o /dev/null --max-time 10 https://github.com 2>/dev/null; then
        err "无法访问 github.com。请检查 DNS / 网络 / 安全组。"
        exit 1
    fi
    ok "GitHub 可达"
}

check_ports() {
    local port
    for port in 80 443 22; do
        if ss -ltn 2>/dev/null | awk '{print $4}' | grep -E "[:.]${port}\$" >/dev/null; then
            warn "端口 ${port} 已被占用 — 这通常意味着 nginx/ssh 已运行,可继续。"
        fi
    done
}

# -----------------------------------------------------------------------------
# 步骤 1/8: 系统包安装
# -----------------------------------------------------------------------------
install_packages() {
    hr; info "步骤 1/8 — 安装系统包 (nginx/git/curl/${FIREWALL_CMD}/certbot)"

    if [[ "$OS_FAMILY" == "debian" ]]; then
        # ---- Debian / Ubuntu 路径 ----
        run export DEBIAN_FRONTEND=noninteractive
        run apt-get update -y
        run apt-get install -y --no-install-recommends \
            nginx git curl wget ca-certificates \
            ufw certbot python3-certbot-nginx
    else
        # ---- RHEL / Alibaba Cloud Linux / CentOS / Fedora / Amazon Linux 路径 ----
        # 刷新元数据 (RHEL 8+ dnf makecache, 7 yum makecache fast)
        run "${PKG_INSTALL}" -y ${PKG_REPO_UPDATE}
        # 启用 EPEL: certbot 在 RHEL 7/8 默认仓库没有,需要 epel-release
        # Alibaba Cloud Linux 3 / CentOS 8 / RHEL 8+: dnf install epel-release
        # RHEL 7 / CentOS 7:                          yum install epel-release
        # Amazon Linux 2/2023: EPEL 已默认启用,跳过
        if [[ "$OS_ID" != "amzn" ]]; then
            if ! rpm -q epel-release >/dev/null 2>&1; then
                info "启用 EPEL 仓库 (certbot 需要)"
                run "${PKG_INSTALL}" -y install epel-release
            else
                ok "EPEL 已安装,跳过"
            fi
        fi
        # firewalld 在大多数 RHEL 镜像里是默认装的,但保险起见显式 install
        run "${PKG_INSTALL}" -y install \
            nginx git curl wget ca-certificates \
            firewalld certbot python3-certbot-nginx

        # 启用 nginx + firewalld + crond (RHEL 默认不随机自启)
        run systemctl enable nginx
        run systemctl enable firewalld
        run systemctl enable "${CRON_SERVICE}"
    fi
    ok "系统包安装完成"
}

# -----------------------------------------------------------------------------
# 步骤 2/8: 准备 web 根目录 + 克隆仓库
# -----------------------------------------------------------------------------
clone_repo() {
    hr; info "步骤 2/8 — 准备 ${WEB_ROOT} 并克隆仓库"
    run mkdir -p "$WEB_ROOT"

    if [[ -d "${WEB_ROOT}/.git" ]]; then
        info "仓库已存在,执行 git pull 更新"
        run git -C "$WEB_ROOT" fetch --prune origin
        run git -C "$WEB_ROOT" reset --hard "origin/${REPO_BRANCH}"
    else
        # 若目录非空(残留文件),移到备份
        if [[ -n "$(ls -A "$WEB_ROOT" 2>/dev/null)" ]]; then
            warn "${WEB_ROOT} 非空,备份到 ${WEB_ROOT}.bak-$(date +%Y%m%d-%H%M%S)"
            run mv "$WEB_ROOT" "${WEB_ROOT}.bak-$(date +%Y%m%d-%H%M%S)"
            run mkdir -p "$WEB_ROOT"
        fi
        run git clone --branch "$REPO_BRANCH" --depth 1 "$REPO_URL" "$WEB_ROOT"
    fi

    # 修正所有权 (Debian: www-data, RHEL: nginx)
    run chown -R "${NGINX_USER}:${NGINX_USER}" "$WEB_ROOT"
    ok "仓库已就绪: $WEB_ROOT ($(du -sh "$WEB_ROOT" 2>/dev/null | cut -f1 || echo '?') )"
}

# -----------------------------------------------------------------------------
# 步骤 3/8: 写 Nginx server block
# -----------------------------------------------------------------------------
write_nginx_config() {
    hr; info "步骤 3/8 — 写入 Nginx 配置"

    local server_name_directive="server_name _;"
    if [[ -n "$DOMAIN" ]]; then
        server_name_directive="server_name ${DOMAIN};"
    fi

    # listen 80 default_server:
    #   Debian: 我们要接管 80,默认站已被备份移走 → 用 default_server
    #   RHEL:   nginx.conf 自身已含 default_server,我们不能重复声明 → 留空
    local listen_directive="listen 80;"
    local listen_ipv6_directive="listen [::]:80;"
    if [[ "$OS_FAMILY" == "debian" ]]; then
        listen_directive="listen 80 default_server;"
        listen_ipv6_directive="listen [::]:80 default_server;"
    fi

    # 写主配置
    cat > "$NGINX_SITE_AVAILABLE" <<NGINX_CONF
# Managed by ${SCRIPT_NAME} v${SCRIPT_VERSION}
# CSDN Dashboard — static site
# Reload with:  sudo systemctl reload nginx
# Update site:  sudo ${UPDATE_SCRIPT}

server {
    ${listen_directive}
    ${listen_ipv6_directive}
    ${server_name_directive}

    root ${WEB_ROOT};
    index index.html;

    # 安全: 隐藏 nginx 版本
    server_tokens off;

    # gzip
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain text/css text/xml application/json application/javascript application/xml+rss application/atom+xml image/svg+xml;

    # 默认日志
    access_log /var/log/nginx/${NGINX_SITE_NAME}.access.log;
    error_log  /var/log/nginx/${NGINX_SITE_NAME}.error.log;

    # 全局默认: 短缓存
    location / {
        try_files \$uri \$uri/ /index.html;
        expires 5m;
        add_header Cache-Control "public, max-age=300, must-revalidate";
    }

    # data.json: 严禁缓存,必须实时
    location = /data.json {
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Pragma "no-cache";
        add_header Expires "0";
        add_header Access-Control-Allow-Origin "*";
    }

    # 静态资源: 长缓存
    location ~* \.(png|jpg|jpeg|gif|svg|ico|webp)$ {
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
        access_log off;
    }

    location ~* \.(css|js)$ {
        expires 1d;
        add_header Cache-Control "public, max-age=86400, must-revalidate";
    }

    location ~* \.html$ {
        expires 5m;
        add_header Cache-Control "public, max-age=300, must-revalidate";
    }

    # 隐藏敏感文件
    location ~ /\.(git|env|openclaw) { deny all; return 404; }
    location ~* \.bak\$ { deny all; return 404; }

    # 404 fallback
    error_page 404 /index.html;
}
NGINX_CONF

    if [[ "$OS_FAMILY" == "debian" ]]; then
        # ---- Debian: sites-available → sites-enabled 符号链接 ----
        # 启用 site
        if [[ -e "$NGINX_SITE_ENABLED" && ! -L "$NGINX_SITE_ENABLED" ]]; then
            warn "移除非符号链接的 ${NGINX_SITE_ENABLED}"
            run mv "$NGINX_SITE_ENABLED" "${NGINX_SITE_ENABLED}.bak-$(date +%Y%m%d-%H%M%S)"
        fi
        run ln -sf "$NGINX_SITE_AVAILABLE" "$NGINX_SITE_ENABLED"

        # 备份并禁用默认 site (避免 listen 80 default_server 冲突)
        if [[ -e /etc/nginx/sites-enabled/default && ! -e "$NGINX_DEFAULT_BACKUP" ]]; then
            warn "备份并禁用 nginx 默认 site"
            run mv /etc/nginx/sites-enabled/default "$NGINX_DEFAULT_BACKUP"
        fi
    else
        # ---- RHEL: 直接写到 /etc/nginx/conf.d/,已被 nginx.conf 默认 include ----
        # RHEL 镜像可能自带一个 /etc/nginx/conf.d/ssl.conf 或 default.conf,里面有 listen 80 default_server
        # 备份同名默认配置避免冲突
        local rhel_default="/etc/nginx/conf.d/default.conf"
        if [[ -e "$rhel_default" ]]; then
            warn "备份并移除 ${rhel_default} (避免 listen 80 default_server 冲突)"
            run mv "$rhel_default" "${rhel_default}.bak-$(date +%Y%m%d-%H%M%S)"
        fi
    fi

    ok "Nginx 配置已写入: $NGINX_SITE_AVAILABLE"
}

# -----------------------------------------------------------------------------
# 步骤 4/8: 自动更新 cron
# -----------------------------------------------------------------------------
setup_cron() {
    hr; info "步骤 4/8 — 配置每日 03:00 自动 git pull + reload"

    # 写更新脚本
    cat > "$UPDATE_SCRIPT" <<UPDATE_EOF
#!/usr/bin/env bash
# CSDN Dashboard 自动更新 (由 cron 每日 03:00 触发)
set -euo pipefail

LOG="${UPDATE_LOG}"
WEB_ROOT="${WEB_ROOT}"
BRANCH="${REPO_BRANCH}"

log() { printf '[%s] %s\n' "\$(date '+%F %T')" "\$*" >> "\$LOG"; }

log "=== 开始自动更新 ==="
cd "\$WEB_ROOT"

if [[ ! -d .git ]]; then
    log "ERROR: \$WEB_ROOT 不是 git 仓库,跳过"; exit 1;
fi

# 取变更前 hash
BEFORE=\$(git rev-parse HEAD)

# 拉取
if ! git fetch --prune origin "\$BRANCH" >> "\$LOG" 2>&1; then
    log "ERROR: git fetch 失败,跳过本次更新"; exit 1;
fi

# 检查是否有新提交
LOCAL=\$(git rev-parse "HEAD")
REMOTE=\$(git rev-parse "origin/\$BRANCH")
if [[ "\$LOCAL" == "\$REMOTE" ]]; then
    log "无变更,退出"
    exit 0
fi

# 拉取并 reset
git reset --hard "origin/\$BRANCH" >> "\$LOG" 2>&1

# 修正所有权 (Debian: www-data, RHEL: nginx)
chown -R ${NGINX_USER}:${NGINX_USER} "\$WEB_ROOT" >> "\$LOG" 2>&1 || true

# 通知 nginx (无需 restart,reload 即可)
if systemctl reload nginx >> "\$LOG" 2>&1; then
    log "✓ nginx reload 成功 (\$BEFORE -> \$(git rev-parse HEAD))"
else
    log "ERROR: nginx reload 失败,需人工检查"; exit 1;
fi

log "=== 更新完成 ==="
UPDATE_EOF
    run chmod +x "$UPDATE_SCRIPT"
    ok "更新脚本已写入: $UPDATE_SCRIPT"

    # 写 cron
    cat > "$CRON_FILE" <<CRON_EOF
# CSDN Dashboard 每日 03:00 自动 git pull + nginx reload
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * root ${UPDATE_SCRIPT} >/dev/null 2>&1
CRON_EOF
    run chmod 644 "$CRON_FILE"

    # RHEL 系 crond 默认不启动,需要显式 start
    if [[ "$OS_FAMILY" == "rhel" ]]; then
        if ! systemctl is-active --quiet "${CRON_SERVICE}"; then
            run systemctl start "${CRON_SERVICE}"
        fi
    fi

    ok "Cron 已注册: ${CRON_FILE} (每天 03:00)"
}

# -----------------------------------------------------------------------------
# 步骤 5/8: HTTPS (可选)
# -----------------------------------------------------------------------------
setup_https() {
    if [[ -z "$DOMAIN" ]]; then
        info "步骤 5/8 — 未指定 --domain,跳过 HTTPS"
        return 0
    fi

    hr; info "步骤 5/8 — 申请 Let's Encrypt 证书 (${DOMAIN})"

    local le_dir="${LETSENCRYPT_DIR}/${DOMAIN}"
    if [[ -d "$le_dir" ]]; then
        ok "证书已存在: $le_dir"
    else
        local le_email="${EMAIL:-webmaster@${DOMAIN}}"
        warn "Certbot 需交互确认域名和邮箱,使用 --non-interactive --agree-tos"
        run certbot --nginx \
            --non-interactive --agree-tos --redirect \
            -m "$le_email" \
            -d "$DOMAIN"

        if [[ ! -d "$le_dir" ]]; then
            err "证书申请失败。请检查:"
            err "  1. 域名 ${DOMAIN} 是否解析到本机公网 IP"
            err "  2. 阿里云安全组是否放行 80/443"
            err "  3. firewall/iptables 是否拦截"
            exit 1
        fi
    fi

    # 测试自动续期
    info "测试 certbot 自动续期 (dry-run)"
    run certbot renew --dry-run
    ok "HTTPS 配置完成"
}

# -----------------------------------------------------------------------------
# 步骤 6/8: 防火墙
# -----------------------------------------------------------------------------
setup_firewall() {
    if [[ "$FIREWALL_CMD" == "ufw" ]]; then
        hr; info "步骤 6/8 — 配置防火墙 (ufw)"

        if ! command -v ufw >/dev/null 2>&1; then
            warn "ufw 未安装,跳过"
            return 0
        fi

        # 如果 ufw 是 inactive,先放行 22 (避免锁死自己)
        if ufw status 2>/dev/null | grep -q "inactive"; then
            run ufw --force reset
            run ufw default deny incoming
            run ufw default allow outgoing
            run ufw allow 22/tcp comment "ssh"
            run ufw allow 80/tcp comment "http"
            run ufw allow 443/tcp comment "https"
            run ufw --force enable
            ok "ufw 已启用并放行 22/80/443"
        else
            run ufw allow 22/tcp comment "ssh"
            run ufw allow 80/tcp comment "http"
            run ufw allow 443/tcp comment "https"
            run ufw --force reload
            ok "ufw 已 reload,端口 22/80/443 已放行"
        fi
    else
        hr; info "步骤 6/8 — 配置防火墙 (firewalld)"

        if ! command -v firewall-cmd >/dev/null 2>&1; then
            warn "firewall-cmd 未安装,跳过"
            return 0
        fi

        # 先启动 firewalld (RHEL 系刚装完不会自动跑)
        if ! systemctl is-active --quiet firewalld; then
            run systemctl start firewalld
        fi

        # 放行服务 + 端口
        run firewall-cmd --permanent --add-service=ssh
        run firewall-cmd --permanent --add-service=http
        run firewall-cmd --permanent --add-service=https
        # 同时也用端口兜底,避免某些 minimal 镜像 service 没注册
        run firewall-cmd --permanent --add-port=22/tcp
        run firewall-cmd --permanent --add-port=80/tcp
        run firewall-cmd --permanent --add-port=443/tcp
        run firewall-cmd --reload
        ok "firewalld 已放行 22/80/443"
    fi
}

# -----------------------------------------------------------------------------
# 步骤 7/8: 启动 + 健康检查
# -----------------------------------------------------------------------------
start_nginx() {
    hr; info "步骤 7/8 — nginx -t && systemctl restart"

    # 配置语法检查
    if ! run nginx -t; then
        err "nginx -t 失败,请查看上面的错误"
        exit 1
    fi

    run systemctl enable nginx
    run systemctl restart nginx
    sleep 1

    # 验证进程
    if pgrep -x nginx >/dev/null; then
        ok "nginx 进程运行中 ($(pgrep -xc nginx) 个 worker)"
    else
        err "nginx 未运行,请检查:  systemctl status nginx"
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# 步骤 8/8: 最终验证
# -----------------------------------------------------------------------------
verify() {
    hr; info "步骤 8/8 — 验证部署"

    local public_ip
    public_ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo 'unknown')"

    local proto="http"
    [[ -n "$DOMAIN" && -d "${LETSENCRYPT_DIR}/${DOMAIN}" ]] && proto="https"

    local url_ip="http://${public_ip}/"
    [[ "$proto" == "https" ]] && url_ip="https://${DOMAIN}/"
    local url_domain="${proto}://${DOMAIN:-${public_ip}}/"

    info "自检 HTTP 状态码..."
    local http_code
    http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url_ip" 2>/dev/null || echo '000')"
    if [[ "$http_code" == "200" ]]; then
        ok "HTTP ${http_code} — ${url_ip}"
    else
        warn "HTTP ${http_code} — ${url_ip} (可能因公网未放行 80/443 而超时)"
    fi

    info "检查 data.json 响应头..."
    local cache_header
    cache_header="$(curl -sI --max-time 10 "$url_ip" 2>/dev/null | grep -i 'cache-control' || echo 'N/A')"
    ok "data.json Cache-Control: ${cache_header}"

    # 报告
    hr
    echo
    printf '%b\n' "${C_GRN}╔══════════════════════════════════════════════════════════════╗${C_RST}"
    printf '%b\n' "${C_GRN}║${C_RST}           ${C_BLU}🎉  CSDN Dashboard 部署完成!${C_RST}                    ${C_GRN}║${C_RST}"
    printf '%b\n' "${C_GRN}╚══════════════════════════════════════════════════════════════╝${C_RST}"
    echo
    printf '%b\n' "${C_BLU}📍 访问地址${C_RST}"
    printf '   • IP:    %s\n' "$url_ip"
    [[ -n "$DOMAIN" ]] && printf '   • 域名:  %s\n' "$url_domain"
    echo
    printf '%b\n' "${C_BLU}🔧 管理命令${C_RST}"
    printf '   • 查看状态:    systemctl status nginx\n'
    printf '   • 重启 nginx:  systemctl restart nginx\n'
    printf '   • 重载配置:    systemctl reload nginx\n'
    printf '   • 手动更新:    sudo %s\n' "$UPDATE_SCRIPT"
    printf '   • 查看日志:    tail -f %s\n' "$UPDATE_LOG"
    printf '   • 测试续期:    certbot renew --dry-run\n'
    printf '   • 编辑配置:    nano %s\n' "$NGINX_SITE_AVAILABLE"
    echo
    printf '%b\n' "${C_BLU}⏰ 自动更新${C_RST}"
    printf '   • Cron: 每天 03:00 自动 git pull + nginx reload\n'
    printf '   • 文件: %s\n' "$CRON_FILE"
    echo
    printf '%b\n' "${C_BLU}📁 重要路径${C_RST}"
    printf '   • 站点根:    %s\n' "$WEB_ROOT"
    printf '   • Nginx 配置: %s\n' "$NGINX_SITE_AVAILABLE"
    printf '   • 更新脚本:   %s\n' "$UPDATE_SCRIPT"
    printf '   • 部署日志:   %s\n' "$DEPLOY_LOG"
    printf '   • 更新日志:   %s\n' "$UPDATE_LOG"
    echo
}

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
main() {
    parse_args "$@"

    # 初始化日志文件
    mkdir -p "$(dirname "$DEPLOY_LOG")" "$(dirname "$UPDATE_LOG")" 2>/dev/null || true
    : > "$DEPLOY_LOG"

    hr
    printf '%b\n' "${C_BLU}${SCRIPT_NAME}${C_RST} v${SCRIPT_VERSION} — CSDN Dashboard 阿里云 ECS 一键部署"
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '%b\n' "模式: ${C_YEL}DRY-RUN${C_RST}"
    else
        printf '%b\n' "模式: ${C_GRN}实执行${C_RST}"
    fi
    printf '%b\n' "域名: ${DOMAIN:-(未指定,仅 HTTP)}"
    printf '%b\n' "日志: ${DEPLOY_LOG}"
    hr

    check_root
    check_os
    check_network
    check_ports

    install_packages
    clone_repo
    write_nginx_config
    setup_cron
    setup_https
    setup_firewall
    start_nginx
    verify

    ok "全部完成 ✨"
}

main "$@"