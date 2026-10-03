# -*- coding: utf-8 -*-
"""webapp 认证：角色登录 + HMAC 令牌（骨架版）。

★ 设计 §45 的终态是「公司 SSO / OAuth」；骨架先落**角色模型本身**（§43 矩阵），
  令牌只证明"你是哪个角色"，写路径的真正闸门在 execution.audit.require_role ——
  就算令牌被伪造，agent 角色也拿不到 approve/publish/kill_switch。

★ 用户文件 auth_users.json：演示用途（密码 sha256，不含明文）；
  接 SSO 时整体替换 login() 即可，下游零改动。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from execution.audit import ROLES  # 同一张角色表（§43）

WEBAPP_DIR = Path(__file__).resolve().parent
USERS_FILE = WEBAPP_DIR / "auth_users.json"

TOKEN_TTL_SEC = 12 * 3600


def _secret_path() -> Path:
    env = os.environ.get("L6_EXECUTION_DB")
    base = Path(env).parent if env else Path(os.getcwd()) / "data" / "state"
    base.mkdir(parents=True, exist_ok=True)
    return base / "webapp_secret.key"


def _secret() -> bytes:
    p = _secret_path()
    if not p.exists():
        import secrets
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(secrets.token_bytes(32))
    return p.read_bytes()


def _load_users() -> Dict[str, Dict[str, Any]]:
    with open(USERS_FILE, encoding="utf-8") as f:
        users = json.load(f)
    return {u["actor"]: u for u in users}


def verify_login(actor: str, password: str) -> Optional[Dict[str, Any]]:
    """登录校验。成功返回 {"actor","role"}，失败 None（不区分账号/密码错误）。"""
    user = _load_users().get(actor)
    if not user or user.get("disabled"):
        return None
    digest = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, user.get("sha256", "")):
        return None
    role = user.get("role")
    if role not in ROLES:
        return None
    return {"actor": actor, "role": role}


def issue_token(actor: str, role: str) -> str:
    """令牌 = base64(payload) + "." + HMAC 签名。

    ★ base64 的原因：actor 是中文，HTTP 头只允许 ASCII——
      这个坑在测试里以 UnicodeEncodeError 的形式现形，不是猜的。
    """
    exp = int(time.time()) + TOKEN_TTL_SEC
    payload = f"{actor}|{role}|{exp}"
    raw = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def parse_token(token: str) -> Optional[Dict[str, Any]]:
    """令牌 → {actor, role}；过期/伪造 → None。"""
    try:
        raw, sig = token.split(".")
        payload = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        expect = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        actor, role, exp = payload.split("|")
        if int(exp) < int(time.time()):
            return None
        if role not in ROLES:
            return None
        return {"actor": actor, "role": role}
    except (ValueError, TypeError):
        return None
