#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""01爬虫/pii_hash.py — 用户标识脱敏（PII 安全底座）。

为什么单独成模块：
  三个爬虫（评分区 / 社区 / 平台发现流）都要给「同一个用户」生成同一个哈希，
  否则跨通道关联（评分区时长 × 社区流向）根本做不了。所以盐和算法必须唯一收敛在这里。

为什么必须加盐：
  老实现是 `sha256(str(user_id))[:16]` —— 无盐。TapTap user_id 是 9 位数字（空间 ~10^9），
  彩虹表秒级可反推，那不叫脱敏，只叫遮住肉眼。本模块改为 HMAC-SHA256(salt, uid)。

分层约定（与 .gitignore 配套）：
  原始数据目录（02数据_*、02数据_platform，已 gitignore）  允许存明文 user_id
  —— 因为要做「按 id 回查 by-user 流」，哈希不可逆，没有明文就查不了。
  分析与产出（outputs/、reports/、看板，入库）            只存哈希，且只出聚合
  —— 任何公开产出里都不允许出现单用户轨迹。

盐：读 01爬虫/.env 的 TAPTAP_HASH_SALT。缺失时**自动生成并写回 .env**（不静默降级为无盐），
并打印醒目提示。盐与 .env 一样不入库（01爬虫/.gitignore 已排除 .env）。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path

CRAWLER_DIR = Path(__file__).resolve().parent
ENV_PATH = CRAWLER_DIR / ".env"
SALT_KEY = "TAPTAP_HASH_SALT"

_warned = False


def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip():
                env[k.strip()] = v.strip()
    return env


def _append_env(key: str, value: str) -> None:
    line = f"{key}={value}\n"
    with ENV_PATH.open("a", encoding="utf-8") as f:
        if ENV_PATH.stat().st_size and not ENV_PATH.read_text(encoding="utf-8").endswith("\n"):
            f.write("\n")
        f.write(line)


def load_salt() -> str:
    """取盐；缺失则生成并写回 .env。绝不返回空串（空串 = 退化成无盐）。"""
    global _warned
    salt = os.environ.get(SALT_KEY, "").strip()
    if not salt:
        salt = _read_env().get(SALT_KEY, "").strip()
    if not salt:
        salt = secrets.token_hex(16)
        try:
            _append_env(SALT_KEY, salt)
        except OSError as e:
            print(f"[pii] 盐写入 .env 失败（{e}）；本次使用进程内临时盐，跨进程哈希不一致", file=sys.stderr)
        if not _warned:
            print(f"[pii] 未配置 {SALT_KEY}，已生成随机盐写入 {ENV_PATH}（不入库）。"
                  f"注意：换盐后历史哈希全部失效，需重算。", file=sys.stderr)
            _warned = True
    os.environ.setdefault(SALT_KEY, salt)
    return salt


def hash_user_id(user_id: object, salt: str | None = None) -> str:
    """用户 id → 不可逆哈希（HMAC-SHA256 截断 16 位十六进制）。

    id 为空/None 时返回空串（不要用空串去哈希出一个恒定值，那会把所有匿名用户并成一个）。
    """
    raw = str(user_id or "").strip()
    if not raw:
        return ""
    return hmac.new(
        (salt or load_salt()).encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def salt_status() -> dict[str, object]:
    """给报告/自检用：只说明盐是否就位，绝不输出盐本身。"""
    env_salt = _read_env().get(SALT_KEY, "").strip()
    return {
        "salting": "HMAC-SHA256(salt, user_id) 截断 16 位",
        "salt_configured": bool(env_salt),
        "salt_in_git": False,  # .env 已被 01爬虫/.gitignore 排除
        "note": "无盐 sha256 可被彩虹表反推（user_id 为 9 位数字），故必须加盐；盐本身不入库、不进报告",
    }


if __name__ == "__main__":
    print(json_status := salt_status())  # noqa: F841
    demo = hash_user_id("418132051")
    print("demo hash:", demo, "| 长度", len(demo))
