#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Quality Engine（§19）—— 输出分数，不做删除。

互联网数据里大量：广告、抽奖、复制粘贴、机器人、引流、纯表情、无意义回复、SEO 垃圾。
但规格明确：**不要 `spam → delete`**，而是保存 `quality_score` 与 `spam_score`，
让第三层自己决定阈值（spam_score > 0.9 → ignore）。

原因：删了就无法复盘"为什么这条没进热点"，也无法调阈值重跑。
"""

from __future__ import annotations

from typing import Any, Dict, List

SPAM_PATTERNS = [
    "加微信", "加v", "加微", "私信我", "代练", "代打", "低价", "优惠券", "限时折扣",
    "点击链接", "领取", "福利", "关注领", "免费送", "扫码", "兼职", "刷单",
    "刷赞", "刷量", "涨粉", "互关", "点赞抽奖", "转发抽奖",
]
NOISE_PATTERNS = ["一楼", "沙发", "顶", "路过", "哈哈哈", "？？？", "。。。。", "666"]
PROMO_URL_HINT = ("t.cn/", "dwz.cn", "sourl", "aff", "utm_campaign")


def score(text: str, normalized_text: str, metrics: Dict[str, Any] = None,
          entities: List[Any] = None, emoji_count: int = 0,
          urls: List[str] = None, hashtags: List[str] = None) -> Dict[str, Any]:
    """返回 {quality_score, spam_score, reasons}。两个分数都在 0..1。"""
    m = metrics or {}
    urls = urls or []
    hashtags = hashtags or []
    ents = entities or []
    raw = text or ""
    norm = normalized_text or ""
    reasons: List[str] = []

    # ---------- spam ----------
    spam = 0.0
    low = raw.lower()
    hit_spam = [p for p in SPAM_PATTERNS if p in low]
    if hit_spam:
        spam += min(0.5, 0.25 * len(hit_spam))
        reasons.append(f"广告词:{','.join(hit_spam[:3])}")
    if len(urls) >= 3:
        spam += 0.2
        reasons.append(f"链接过多({len(urls)})")
    if any(h in low for h in PROMO_URL_HINT):
        spam += 0.15
        reasons.append("推广链接")
    # 纯表情 / 极短无意义
    if len(norm) <= 4 and emoji_count > 0:
        spam += 0.4
        reasons.append("纯表情/极短")
    if norm and any(p == norm for p in NOISE_PATTERNS):
        spam += 0.35
        reasons.append("无意义回复")
    # 重复字符占比过高（"啊啊啊啊啊啊"）
    if len(norm) >= 6:
        uniq = len(set(norm))
        if uniq / len(norm) < 0.25:
            spam += 0.2
            reasons.append("重复字符占比高")
    spam = min(1.0, round(spam, 3))

    # ---------- quality ----------
    q = 0.35                                    # 基线
    n = len(norm)
    if n >= 15:
        q += 0.15
    if n >= 40:
        q += 0.1
    if ents:
        q += min(0.2, 0.08 * len(ents))
    if hashtags:
        q += 0.05
    for k in ("views", "likes", "comments"):
        v = m.get(k)
        if isinstance(v, (int, float)) and v > 0:
            q += 0.06
    if emoji_count > 5:
        q -= 0.1                                # 情绪堆砌，信息量低
    if len(urls) >= 3:
        q -= 0.1
    q = max(0.0, min(1.0, round(q, 3)))

    return {"quality_score": q, "spam_score": spam, "reasons": reasons[:5]}
