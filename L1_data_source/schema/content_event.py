#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 信号采集层 —— 统一内容事件（Content Event）定义。

这一层的职责只有一件事：把各平台形态各异的数据，归一化成**同一个结构**。
它属于 Data Engineering，**不放 Agent**：不做判断、不做打分、不做语义理解。

设计红线（与项目既有纪律一致）：
1. **缺失 = None，绝不填 0** —— 0 是"有这个数且为 0"，None 是"平台没给"。
   填 0 会让下游把它当成真实信号（本项目踩过：posts.supports 全 0 被误当传播度）。
2. **作者一律脱敏** —— 只收 hash，绝不明文用户名（PII 不进库，见 materials.py 同款约束）。
3. **可溯源** —— 每条事件必须能追回原始文件与行号（raw_ref），说不出来源的数据不算数。
4. **纯标准库** —— 本层不依赖 pandas/numpy。

字段说明（前 8 项是跨平台的公共面，后面是工程必需）：
    platform / title / content / author / published_at
    views / likes / comments / shares
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, Iterator, List, Optional

__all__ = [
    "ContentEvent",
    "PLATFORM_REGISTRY",
    "SOURCE_TYPES",
    "TZ_CN",
    "to_iso",
    "parse_int",
    "read_csv_rows",
    "hash_author",
    "validate_event",
    "events_to_jsonl",
]

# 北京时间：TapTap / B站 / 微博 的时间戳都按东八区解释
TZ_CN = timezone(timedelta(hours=8))

# 允许的事件类型（source_type）：同一平台可以有多种（TapTap 有评论/论坛/动态/评分/榜单）
SOURCE_TYPES = {
    "post",       # 图文/动态帖
    "moment",     # 社区动态（TapTap 动态流）
    "forum",      # 论坛帖
    "comment",    # 评论（挂在某个帖/视频下）
    "video",      # 视频
    "review",     # 游戏评论/打分
    "rating",     # 评分聚合
    "rank",       # 榜单
    "hashtag",    # 话题/词条（聚合对象，不是单条内容）
    "article",    # 新闻/媒体文章
    "index",      # 指数类（百度指数 / Google Trends）
}

# 平台注册表：用户给出的全网信号源清单。status 三种：
#   active  = 已有适配器 + 本地已有真实数据
#   planned = 已登记，尚无适配器（诚实标注，不假装接了）
PLATFORM_REGISTRY: Dict[str, Dict[str, Any]] = {
    "taptap":        {"name": "TapTap",       "status": "active",  "kinds": ["review", "forum", "moment", "rating", "rank"]},
    "bilibili":      {"name": "哔哩哔哩",      "status": "active",  "kinds": ["video", "comment"]},
    "douyin":        {"name": "抖音",          "status": "active",  "kinds": ["video", "comment"]},
    "weibo":         {"name": "微博",          "status": "active",  "kinds": ["post", "comment"]},
    "xiaohongshu":   {"name": "小红书",        "status": "planned", "kinds": ["post", "comment"]},
    "zhihu":         {"name": "知乎",          "status": "planned", "kinds": ["post", "comment"]},
    "baidu_index":   {"name": "百度指数",      "status": "planned", "kinds": ["index"]},
    "wechat":        {"name": "微信",          "status": "planned", "kinds": ["article"]},
    "news":          {"name": "新闻媒体",      "status": "planned", "kinds": ["article"]},
    "reddit":        {"name": "Reddit",        "status": "planned", "kinds": ["post", "comment"]},
    "x":             {"name": "X (Twitter)",   "status": "planned", "kinds": ["post"]},
    "youtube":       {"name": "YouTube",       "status": "planned", "kinds": ["video", "comment"]},
    "google_trends": {"name": "Google Trends", "status": "planned", "kinds": ["index"]},
    "steam":         {"name": "Steam",         "status": "planned", "kinds": ["review", "rating"]},
}

# 作者字段的合法形态：unknown / 采集侧哈希（hex）/ 本层生成的 h_ 前缀哈希。
# 反过来，凡不匹配这条的就是明文昵称或明文 UID —— 一律拦下。
# ★ 注意别写成"含中文才算明文"：英文昵称 'Bismarck' 同样是 PII。
_AUTHOR_OK_RE = re.compile(r"^(unknown|h_[0-9a-f]{8,64}|[0-9a-f]{8,64})$", re.I)


def to_iso(value: Any) -> Optional[str]:
    """把各种时间表示统一成 ISO8601（东八区）。无法解析返回 None，绝不猜。

    支持：unix 秒（int/数字字符串）、ISO 字符串、'YYYY-MM-DD HH:MM:SS'。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 纯数字 → 当作 unix 秒
    if re.fullmatch(r"\d{9,13}", s):
        ts = int(s)
        if ts > 10_000_000_000:  # 毫秒
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts, TZ_CN).isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    s2 = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s2)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CN)
    return dt.astimezone(TZ_CN).isoformat()


def parse_int(value: Any) -> Optional[int]:
    """字符串 → int。空/非法/负数一律 None（不把 '' 当 0，不编数字）。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        n = int(float(s))
    except ValueError:
        return None
    return n if n >= 0 else None


def hash_author(raw: Any) -> str:
    """作者统一脱敏：已经是 hash 就原样用，否则 sha1 前 16 位。空 → 'unknown'。"""
    if raw is None:
        return "unknown"
    s = str(raw).strip()
    if not s:
        return "unknown"
    if re.fullmatch(r"[0-9a-f]{8,64}", s.lower()):
        return s.lower()          # 采集侧已经哈希过，不重复哈希
    return "h_" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def read_csv_rows(path: str, encoding: str = "utf-8-sig") -> Iterator[tuple]:
    """读 CSV 并附带行号，用于溯源（raw_ref = 文件:行号）。

    行号从 2 开始（1 是表头），与在 Excel 里看到的实际行号一致。
    """
    with open(path, "r", encoding=encoding, errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=2):
            yield idx, row


@dataclass
class ContentEvent:
    """全网统一的内容事件。所有平台适配器都必须产出这个结构。"""

    # —— 跨平台公共面（用户定义的 8 项）——
    platform: str
    title: str = ""
    content: str = ""
    author: str = "unknown"
    published_at: Optional[str] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None     # 评论数（数字），不是评论文本
    shares: Optional[int] = None

    # —— 工程字段 ——
    event_id: str = ""
    source_type: str = "post"
    url: Optional[str] = None
    game: Optional[str] = None
    parent_id: Optional[str] = None    # 评论所属帖/视频的原生 id
    native_id: Optional[str] = None    # 平台原生 id
    collected_at: Optional[str] = None
    raw_ref: str = ""                  # 溯源：相对路径:行号
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def make_event_id(platform: str, source_type: str, native_id: Optional[str], raw_ref: str) -> str:
    """稳定 id：能不依赖行号就不要依赖（行号会因重采而漂移）。"""
    if native_id:
        return f"{platform}:{source_type}:{native_id}"
    return f"{platform}:{source_type}:row:{hashlib.sha1(raw_ref.encode('utf-8')).hexdigest()[:16]}"


def validate_event(ev: ContentEvent) -> List[str]:
    """返回问题列表；空列表 = 合法。故意做成"收集全部问题"而不是首错即抛，
    这样跑批时能一次性看到某平台数据到底坏在哪几处。"""
    problems: List[str] = []
    if ev.platform not in PLATFORM_REGISTRY:
        problems.append(f"未登记的平台: {ev.platform}")
    if ev.source_type not in SOURCE_TYPES:
        problems.append(f"未登记的 source_type: {ev.source_type}")
    if not ev.event_id:
        problems.append("event_id 为空")
    if not (ev.title or ev.content):
        problems.append("title 与 content 全空（无内容可分析）")
    for f in ("views", "likes", "comments", "shares"):
        v = getattr(ev, f)
        if v is not None and (not isinstance(v, int) or v < 0):
            problems.append(f"{f} 非法: {v!r}")
    # PII 闸门：作者必须是 hash 或 unknown，不能是明文昵称/明文 UID
    if not _AUTHOR_OK_RE.match(ev.author or ""):
        problems.append(f"作者疑似明文未脱敏: {ev.author!r}")
    if not ev.raw_ref:
        problems.append("raw_ref 为空（不可溯源）")
    return problems


def events_to_jsonl(events: Iterable[ContentEvent], path: str) -> int:
    """写 JSONL，返回条数。"""
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for ev in events:
            fh.write(ev.to_json() + "\n")
            n += 1
    return n
