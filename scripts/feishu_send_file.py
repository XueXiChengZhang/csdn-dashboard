#!/usr/bin/env python3
"""
feishu_send_file.py — 飞书文件/附件发送 helper

背景
----
csdn-monitor-v2.py 的 send_feishu() 里写着:

    from feishu_send_file import upload_file, send_file

但仓库里从来没有这个模块 —— 每次带附件都抛 ModuleNotFoundError，
被 except 吞掉后只留一行 "[feishu] 附件发送失败"，文字照发、附件永远发不出去。
日报末尾那句 "📎 完整报告见附件" 因此长期是空头支票。

接口
----
    fk = upload_file("/path/to/report.md")   -> file_key 或 None
    send_file(fk, file_name="x.md")          -> True / False

也提供一步到位的 send_attachment(path)。

飞书 OpenAPI 流程 (两步, 必须依次做):
    1. POST /open-apis/im/v1/files           (multipart/form-data)  -> file_key
    2. POST /open-apis/im/v1/messages        msg_type=file, content={"file_key": ...}

依赖: 仅 stdlib. 复用 feishu_sender.py 的凭据加载与 token 获取, 不重复实现。
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import urllib.request
import urllib.error
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import feishu_sender as fs  # noqa: E402

# 飞书文件上传上限 (官方: 单文件 30MB, 文件名 <= 250 字符)
MAX_FILE_BYTES = 30 * 1024 * 1024
UPLOAD_URL = "https://open.feishu.cn/open-apis/im/v1/files"


def _build_multipart(fields: dict, file_field: str, file_path: Path):
    """手工拼 multipart/form-data。stdlib 没有现成的 multipart 编码器。"""
    boundary = "----HermesBoundary" + uuid.uuid4().hex
    lines = []
    for k, v in fields.items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
        lines.append(b"")
        lines.append(str(v).encode("utf-8"))
    lines.append(f"--{boundary}".encode())
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{file_path.name}"'.encode("utf-8")
    )
    lines.append(f"Content-Type: {ctype}".encode())
    lines.append(b"")
    lines.append(file_path.read_bytes())
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


def upload_file(path, file_type="stream"):
    """上传文件到飞书, 返回 file_key。失败返回 None。

    file_type: 飞书按扩展名自己判断, 一般用 'stream' 即可。
               (opus/mp4/pdf/doc/xls/ppt 等有专用值, 非必需)
    """
    p = Path(path)
    if not p.is_file():
        print(f"[ERROR] 附件不存在: {p}", file=sys.stderr)
        return None
    size = p.stat().st_size
    if size == 0:
        print(f"[ERROR] 附件是空文件: {p}", file=sys.stderr)
        return None
    if size > MAX_FILE_BYTES:
        print(f"[ERROR] 附件 {size / 1048576:.1f}MB 超过飞书 30MB 上限", file=sys.stderr)
        return None

    token = fs.get_tenant_access_token()
    if not token:
        return None

    body, ctype = _build_multipart(
        {"file_type": file_type, "file_name": p.name}, "file", p
    )
    req = urllib.request.Request(
        UPLOAD_URL,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") == 0:
            fk = (data.get("data") or {}).get("file_key")
            print(f"[OK] 上传成功: {p.name} -> {fk}")
            return fk
        print(f"[ERROR] 上传失败: {data}", file=sys.stderr)
        return None
    except urllib.error.HTTPError as e:
        print(f"[ERROR] 上传 HTTP {e.code}: "
              f"{e.read().decode('utf-8', errors='replace')[:300]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ERROR] 上传异常: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def send_file(file_key, receive_id=None, receive_id_type="chat_id"):
    """发已上传的文件消息。"""
    if not file_key:
        return False
    token = fs.get_tenant_access_token()
    if not token:
        return False
    if not receive_id:
        receive_id = fs.HOME_CHANNEL
    body = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }
    req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/im/v1/messages"
        f"?receive_id_type={receive_id_type}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") == 0:
            print("[OK] 文件消息已发送")
            return True
        print(f"[ERROR] 发文件失败: {data}", file=sys.stderr)
        return False
    except urllib.error.HTTPError as e:
        print(f"[ERROR] 发文件 HTTP {e.code}: "
              f"{e.read().decode('utf-8', errors='replace')[:300]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERROR] 发文件异常: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def send_attachment(path, receive_id=None):
    """一步到位: 上传 + 发送。返回 True/False，失败原因已打到 stderr。"""
    fk = upload_file(path)
    if not fk:
        return False
    return send_file(fk, receive_id=receive_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python feishu_send_file.py <文件路径> [--dry]")
        sys.exit(2)
    target = sys.argv[1]
    if "--dry" in sys.argv:
        p = Path(target)
        print(f"[dry] exists={p.is_file()} "
              f"size={p.stat().st_size if p.is_file() else 0} "
              f"name={p.name}")
        sys.exit(0)
    ok = send_attachment(target)
    print(f"Result: {'OK' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
