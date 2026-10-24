#!/usr/bin/env python3
"""
feishu_sender.py — 飞书消息推送 helper (Python 版)
- 用 Hermes .env 里的 FEISHU_APP_ID + FEISHU_APP_SECRET
- 复用现有 Hermes 飞书 OpenAPI 通道
- 复用现有 FEISHU_HOME_CHANNEL 作为默认接收
- 支持 文本 / 富文本 / Markdown
"""
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# Hermes .env 路径
# __file__ = D:\WebArchives\workspace_backup\scripts\feishu_sender.py
# Hermes .env = C:\Users\Administrator\WorkBuddy\2026-08-23-00-58-07\hermes-home\.env
HERMES_HOME = Path("C:/Users/Administrator/WorkBuddy/2026-08-23-00-58-07/hermes-home")
HERMES_ENV = HERMES_HOME / ".env"

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
HOME_CHANNEL = os.environ.get("FEISHU_HOME_CHANNEL", "oc_c290dff417b1be4dc5ec54b6acdc65ef")


def load_env():
    """从 Hermes .env 加载 APP_ID/SECRET (如果 env 里没有)"""
    global APP_ID, APP_SECRET, HOME_CHANNEL
    if APP_ID and APP_SECRET:
        return True
    if not HERMES_ENV.exists():
        print(f"[WARN] Hermes .env not found: {HERMES_ENV}", file=sys.stderr)
        return False
    with open(HERMES_ENV, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if v.startswith("${env:"):
                continue  # 引用其他 env var
            if k == "FEISHU_APP_ID":
                APP_ID = v
            elif k == "FEISHU_APP_SECRET":
                APP_SECRET = v
            elif k == "FEISHU_HOME_CHANNEL":
                HOME_CHANNEL = v
    return bool(APP_ID and APP_SECRET)


def get_tenant_access_token():
    """拿飞书 access token"""
    if not load_env():
        return None
    try:
        body = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                return data["tenant_access_token"]
            print(f"[ERROR] get token failed: {data}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[ERROR] get token: {e}", file=sys.stderr)
        return None


def send_text(text, receive_id=None, receive_id_type="chat_id"):
    """发文本消息"""
    token = get_tenant_access_token()
    if not token:
        return False
    if not receive_id:
        receive_id = HOME_CHANNEL
    body = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }
    try:
        req = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                print(f"[OK] Feishu sent to {receive_id}")
                return True
            else:
                print(f"[ERROR] feishu send: {data}", file=sys.stderr)
                return False
    except Exception as e:
        print(f"[ERROR] feishu send: {e}", file=sys.stderr)
        return False


def send_post_rich(content_lines, title="持仓报告", receive_id=None, receive_id_type="chat_id"):
    """
    发富文本消息 (post 格式)
    content_lines: list of [tag, text, *] - 每行是一段内容
        tag: text / a / at / img / ...
    参考: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/im-v1/message/create_json
    """
    token = get_tenant_access_token()
    if not token:
        return False
    if not receive_id:
        receive_id = HOME_CHANNEL
    body = {
        "receive_id": receive_id,
        "msg_type": "post",
        "content": json.dumps({
            "zh_cn": {
                "title": title,
                "content": content_lines  # 二维数组, 每个元素是一行内容
            }
        })
    }
    try:
        req = urllib.request.Request(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                print(f"[OK] Feishu rich post sent")
                return True
            else:
                print(f"[ERROR] feishu rich: {data}", file=sys.stderr)
                return False
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        print(f"[ERROR] feishu rich HTTP {e.code}: {body_text}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERROR] feishu rich: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # 测试
    print("Testing feishu_sender...")
    print(f"  APP_ID: {APP_ID[:10]}..." if APP_ID else "  APP_ID: MISSING")
    print(f"  HOME_CHANNEL: {HOME_CHANNEL}")
    msg = sys.argv[1] if len(sys.argv) > 1 else "🧪 Hermes crypto_monitor 飞书推送测试"
    print(f"\nSending: {msg}\n")
    ok = send_text(msg)
    print(f"\nResult: {'OK' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)