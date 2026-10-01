#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1 信号采集层 —— RawContentEvent 协议 v1.0（严格协议，第一天就带版本号）。

设计来源：用户给的 L1 完整版架构（2026-10-01）。相对上一版的三处关键升级：

1. **三个时间必须分开**（算热点延迟的生命线）：
   - `published_at` 内容什么时候发布（平台给的）
   - `observed_at`  系统什么时候观察到这个状态（采集到的指标所属时刻）
   - `crawled_at`   这次采集什么时候发生
   混成一个字段，就算不出「热点延迟」，也算不准 velocity。
2. **Data Lineage 全字段**：`source_id / request_id / crawl_run_id / raw_ref /
   parser_version / schema_version` —— 以后「昨天这个热点数据为什么错了」能精确追到
   那一次请求和那一版 parser。
3. **不可变 + 可重放**：事件本身不改，只追加；parser 出 bug 就换新版重放 raw。

★ 与项目既有纪律的一处冲突及取舍（必须写明）：
   用户规格里有 `author_name` 字段，但本项目的 PII 纪律是「明文不进产物」。
   处理：`author_name` **保留在协议里但默认 None**，由 `compliance_config.contains_pii`
   显式开关；开启时明文只落 **raw lake（不入 git）**，不进事件流。
   这样既不丢规格，也不破纪律。

外部互联网数据一定会 drift，所以 schema_version 是必填 —— 永远不要默认「schema 不会变」。
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
    "RawContentEvent", "ContentEvent", "MetricSnapshot",
    "SCHEMA_VERSION", "PARSER_VERSION",
    "PLATFORM_REGISTRY", "SOURCE_TYPES", "SIGNAL_TYPES", "TZ_CN",
    "to_iso", "parse_int", "read_csv_rows", "hash_author",
    "validate_event", "events_to_jsonl", "make_event_id", "content_key", "snapshot_id",
]

SCHEMA_VERSION = "1.0"        # RawContentEvent 协议版本；破坏性变更必须 +1
PARSER_VERSION = "taptap-1.0"  # 由各 adapter 覆盖；进 raw lake 与 lineage，parser 出 bug 可按版重放

TZ_CN = timezone(timedelta(hours=8))

# 事件类型（同一平台可有多种）
SOURCE_TYPES = {
    "post", "moment", "forum", "comment", "video", "review",
    "rating", "rank", "hashtag", "article", "index",
}

# ★ 信号类型：按「信号价值」分类，而不是按平台分类。
# 第三层靠它识别传播路径：Search ↑ → Social ↑ → Content ↑ → Community ↑（热点生命周期）
SIGNAL_TYPES = {
    "search":     "用户开始主动寻找（百度指数 / Google Trends / 站内搜索）",
    "social":     "用户开始讨论（微博 / X / 小红书）",
    "content":    "创作者开始生产（B站 / YouTube / 抖音）",
    "community":  "核心用户深度讨论（TapTap / Reddit / Steam 评论）",
    "news":       "信息进入大众传播（新闻 / 行业媒体）",
    "official":   "官方事件发生（游戏官网 / 开发者账号 / 公告）",
    "market":     "商业表现变化（Steam 销量 / 榜单 / 畅销排名）",
    "internal":   "平台内部行为（搜索 / 浏览 / 评论 / 关注）",
}

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
    "steam":         {"name": "Steam",         "status": "planned", "kinds": ["review", "rating", "rank"]},
}

# 作者合法形态白名单：unknown / 采集侧哈希 / 本层 h_ 前缀哈希
_AUTHOR_OK_RE = re.compile(r"^(unknown|h_[0-9a-f]{8,64}|[0-9a-f]{8,64})$", re.I)


def now_cn() -> datetime:
    return datetime.now(TZ_CN)


def to_iso(value: Any) -> Optional[str]:
    """统一成 ISO8601（东八区）。无法解析返回 None，绝不猜。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=TZ_CN)
        return dt.astimezone(TZ_CN).isoformat()
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{9,13}", s):
        ts = int(s)
        if ts > 10_000_000_000:
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
    """空/非法/负数一律 None —— 不把 '' 当 0，不编数字。"""
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
    """作者脱敏：已是 hash 则原样（保住跨表 join），否则 sha1 前 16 位。"""
    if raw is None:
        return "unknown"
    s = str(raw).strip()
    if not s:
        return "unknown"
    if re.fullmatch(r"[0-9a-f]{8,64}", s.lower()):
        return s.lower()
    return "h_" + hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def read_csv_rows(path: str, encoding: str = "utf-8-sig") -> Iterator[tuple]:
    """读 CSV 带行号（行号从 2 开始，与 Excel 里看到的一致），用于 raw_ref。"""
    with open(path, "r", encoding=encoding, errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=2):
            yield idx, row


def content_key(platform: str, external_id: str) -> str:
    """★ 内容身份：platform:external_id —— 与「某次观察」是两个概念，必须分开。"""
    return f"{platform}:{external_id}"


def make_event_id(platform: str, source_type: str, native_id: Optional[str], raw_ref: str) -> str:
    if native_id:
        return f"{platform}:{source_type}:{native_id}"
    return f"{platform}:{source_type}:row:{hashlib.sha1(raw_ref.encode('utf-8')).hexdigest()[:16]}"


def snapshot_id(source_id: str, external_id: str, observed_at: str) -> str:
    """★ 观察身份：hash(source_id + external_id + observed_at) —— 同一内容不同时刻是不同 snapshot。"""
    return hashlib.sha1(f"{source_id}|{external_id}|{observed_at}".encode("utf-8")).hexdigest()[:20]


@dataclass
class RawContentEvent:
    """统一内容事件（v1.0）。所有 Connector 经 Protocol Adapter 后都产出这个。"""

    # —— 身份 ——
    event_id: str = ""
    source_id: str = ""                 # ★ Source ≠ Platform：精确到「微博热搜」而不是「微博」
    platform: str = ""
    external_id: str = ""               # 平台原生 id
    parent_id: Optional[str] = None     # 评论所属帖/视频（保住语境，素材层要求存 Thread 不存孤立评论）
    game: Optional[str] = None          # 所属游戏（多游戏档案：games/<key>.json）
    source_type: str = "post"           # 内容形态（post/comment/video/review/...）
    content_type: str = "post"          # 同上（规格里两个字段都出现，这里双向同步，避免两处打架）
    signal_type: str = "community"      # ★ 信号价值分类，见 SIGNAL_TYPES

    # —— 内容 ——
    title: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    # —— 作者（PII 受控）——
    author_id: str = "unknown"
    author_name: Optional[str] = None   # ★ 默认 None；仅当 compliance.contains_pii 允许时填充

    # —— ★ 三个时间，必须分开 ——
    published_at: Optional[str] = None  # 内容发布时间
    observed_at: Optional[str] = None   # 观察到该状态的时刻（指标所属时刻）
    crawled_at: Optional[str] = None    # 本次采集发生时刻

    # —— 指标（同时给扁平字段与 dict，扁平字段为 None 表示"平台没给"）——
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    favorites: Optional[int] = None
    rank: Optional[int] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    # —— 溯源 / 治理 ——
    raw_ref: str = ""                   # 指向不可变原始数据（本地路径或 s3://）
    request_id: str = ""                # 每次 HTTP 请求唯一
    crawl_run_id: str = ""              # 每次采集任务唯一
    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # source_type ⇄ content_type 双向同步：老代码只给一个，另一个自动跟上
        if self.source_type == "post" and self.content_type not in ("post", "", None):
            self.source_type = self.content_type
        elif self.content_type == "post" and self.source_type not in ("post", "", None):
            self.content_type = self.source_type

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def sync_metrics(self) -> "RawContentEvent":
        """扁平字段 ⇄ metrics dict 双向同步，避免两处数字打架。"""
        flat = {
            "views": self.views, "likes": self.likes, "comments": self.comments,
            "shares": self.shares, "favorites": self.favorites, "rank": self.rank,
        }
        merged = {k: v for k, v in flat.items() if v is not None}
        merged.update({k: v for k, v in self.metrics.items() if v is not None})
        self.metrics = merged
        for k, v in merged.items():
            if hasattr(self, k):
                setattr(self, k, v)
        return self


# 兼容旧名（normalize.py 与既有测试引用）
ContentEvent = RawContentEvent


@dataclass
class MetricSnapshot:
    """★ 指标快照：时间 × 指标，永不 UPDATE，只追加。

    第三层要的 velocity = Δviews/Δt、acceleration = Δvelocity/Δt 全靠它。
    这是 L1 最关键的数据产品之一 —— 只存"当前值"的系统算不出加速度。
    """

    source_id: str
    external_id: str
    observed_at: str
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    favorites: Optional[int] = None
    rank: Optional[int] = None
    content_key: str = ""
    snapshot_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_key and self.external_id:
            # 缺 platform 时用 source_id 兜底，保证 key 稳定
            self.content_key = f"{self.source_id}:{self.external_id}"
        if not self.snapshot_id:
            self.snapshot_id = snapshot_id(self.source_id, self.external_id, self.observed_at)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_event(ev: RawContentEvent) -> List[str]:
    """返回问题列表；空 = 合法。收集全部问题，便于一次看清某数据源坏在哪。"""
    problems: List[str] = []
    if ev.platform not in PLATFORM_REGISTRY:
        problems.append(f"未登记的平台: {ev.platform}")
    if ev.content_type not in SOURCE_TYPES:
        problems.append(f"未登记的 content_type: {ev.content_type}")
    if ev.signal_type not in SIGNAL_TYPES:
        problems.append(f"未登记的 signal_type: {ev.signal_type}")
    if not ev.event_id:
        problems.append("event_id 为空")
    if not ev.source_id:
        problems.append("source_id 为空（无法溯源到具体数据源）")
    if not (ev.title or ev.content):
        problems.append("title 与 content 全空")
    for f in ("views", "likes", "comments", "shares", "favorites", "rank"):
        v = getattr(ev, f)
        if v is not None and (not isinstance(v, int) or v < 0):
            problems.append(f"{f} 非法: {v!r}")
    if not _AUTHOR_OK_RE.match(ev.author_id or ""):
        problems.append(f"作者疑似明文未脱敏: {ev.author_id!r}")
    if not ev.raw_ref:
        problems.append("raw_ref 为空（不可溯源）")
    if not ev.observed_at:
        problems.append("observed_at 为空（算不出新鲜度/速度）")
    if ev.schema_version != SCHEMA_VERSION:
        problems.append(f"schema_version 不匹配: {ev.schema_version} != {SCHEMA_VERSION}")
    return problems


def events_to_jsonl(events: Iterable[RawContentEvent], path: str) -> int:
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for ev in events:
            fh.write(ev.to_json() + "\n")
            n += 1
    return n
