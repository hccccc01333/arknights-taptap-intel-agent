#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Thread Feed（L3）—— 给 LLM 用的讨论帖数据服务（S3 通道）。

★ 为什么它长这样（2026-10-04 设计决定）：
  热点的判断者是 LLM，不是算法。LLM 要先看到「有哪些帖子在讨论什么」，
  自己挑出值得深挖的，再叫我们抓评论。所以这里**不是跑批脚本，是三个被调用的能力**：

    list_threads()   只读目录（免费）——LLM 当索引用，自己定排序/过滤/数量
    deepen_threads() 按需抓评论（花钱，受预算护栏）——LLM 点名 + 自己定页数/排序
    read_comments()  读已抓回的（免费）——LLM 判断玩家态度

★ 三条纪律：
  1. 计数与时间永远来自 L1 快照，**不推算、不补齐**；算不出增速就说算不出。
  2. deepen 受全局预算约束，超了**明说超了**（让 LLM 自己缩小范围），不偷偷截断。
  3. 抓过什么记在 cache 里，LLM 重复问同一帖不重复打接口。

用法：
    from thread_feed import ThreadFeed
    feed = ThreadFeed()
    feed.list_threads(window_hours=48, sort="growth", limit=20)
    feed.deepen_threads(["855899591994770843"], pages=3, orders=["rank", "time"])
    feed.read_comments("855899591994770843", order="rank", limit=20)
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_L3 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L3)
# ★ 锚定到本文件位置而非 cwd —— 服务/测试从任意目录 import 都要能找到 data/
_DATA_ROOT = _ROOT
if not os.path.isdir(os.path.join(_DATA_ROOT, "data", "raw")):
    # L3_trend 被从别处软链/复制过来时，向上找带 data/raw 的那一级
    for _up in [_L3, _HERE, os.path.dirname(_L3), os.getcwd()]:
        if os.path.isdir(os.path.join(_up, "data", "raw")):
            _DATA_ROOT = _up
            break
for _p in (_L3, os.path.join(_DATA_ROOT, "L1_data_source")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TZ_CN = timezone(timedelta(hours=8))
RAW = os.path.join(_DATA_ROOT, "data", "raw", "taptap", "community")
CACHE_PATH = os.path.join(RAW, "thread_feed_cache.json")
BUDGET_PATH = os.path.join(_DATA_ROOT, "data", "state", "crawl_config.json")

# 评论排序通道 → 接口 sort/order
# ★ 实测（2026-10-04）：by-moment 只认 sort=rank；sort=time 一律 HTTP 400。
#   所以"按时间看最新评论"走不通 —— rank/desc 本身就是"赞+时间"混合序，
#   要看最新就对本地产出的评论按 publish_time 本地重排（read_comments 的 order=time）。
ORDER_MAP = {"rank": ("rank", "desc")}
UNSUPPORTED_ORDERS = {"time": "接口不支持 sort=time（HTTP 400），已自动降级为 rank；"
                                "要按时间读请用 read_comments(order=time) 本地重排"}


def _now() -> datetime:
    return datetime.now(TZ_CN)


def _ts(v: Any) -> int:
    return int(v) if str(v or "").isdigit() else 0


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ_CN).isoformat() if ts else ""


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class BudgetExceeded(RuntimeError):
    """deepen 请求超出预算。LLM 拿到这个提示会自己缩小范围。"""


class ThreadFeed:
    """讨论帖数据服务。三项能力 + 抓取缓存 + 预算护栏。"""

    def __init__(self, community_dir: str = RAW) -> None:
        self.dir = community_dir
        self.posts_path = os.path.join(community_dir, "posts.csv")
        self.comments_path = os.path.join(community_dir, "comments.csv")
        self.snapshots_path = os.path.join(community_dir, "thread_snapshots.csv")
        self.cache_path = os.path.join(community_dir, "thread_feed_cache.json")
        self._crawler_obj = None      # 懒加载，避免只读目录时也要求 X-UA

    # ------------------------------------------------------------ 缓存
    def _cache(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, encoding="utf-8") as f:
                    return json.load(f) or {}
            except (ValueError, OSError):
                return {}
        return {}

    def _save_cache(self, c: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)

    def _budget(self) -> int:
        """每轮评论接口调用预算（前端可配）。"""
        if os.path.exists(BUDGET_PATH):
            try:
                with open(BUDGET_PATH, encoding="utf-8") as f:
                    return int((json.load(f) or {}).get("max_comment_calls", 150))
            except (ValueError, OSError, TypeError):
                pass
        return 150

    # ------------------------------------------------------------ ① 目录（免费）
    def list_threads(self, window_hours: int = 48, sort: str = "growth",
                     limit: int = 20, min_growth_per_hour: float = 0.0,
                     platform: str = "", include_closed: bool = False,
                     snippet_chars: int = 60) -> Dict[str, Any]:
        """列出讨论帖目录。**不花任何接口调用**，LLM 拿它当索引。

        sort: growth(增速) / comments(评论数) / likes(点赞) / recent(最新发布)
        min_growth_per_hour: 只返回增速 ≥ 该值的帖（0 = 不滤）
        返回里 growth_per_hour 可能是 None —— 采样点不够就说算不出，不填 0 假装没增长。
        """
        posts = {r["moment_id"]: r for r in _read_csv(self.posts_path) if r.get("moment_id")}
        series: Dict[str, List[Dict[str, str]]] = {}
        for s in _read_csv(self.snapshots_path):
            series.setdefault(s["moment_id"], []).append(s)
        for v in series.values():
            v.sort(key=lambda x: x.get("observed_at") or "")

        now_ts = _now().timestamp()
        cutoff = now_ts - max(1, window_hours) * 3600
        cache = self._cache()

        # 窗口内全部在监测的帖（算发帖速率的母体）
        all_in_window = [p for p in posts.values()
                         if (include_closed or (p.get("monitor_state") or "active") == "active")
                         and (not _ts(p.get("publish_time")) or _ts(p.get("publish_time")) >= cutoff)
                         and (not platform or (p.get("app_title") or "") == platform)]

        out: List[Dict[str, Any]] = []
        for mid, p in posts.items():
            if not include_closed and (p.get("monitor_state") or "active") == "closed":
                continue
            pub = _ts(p.get("publish_time"))
            if pub and pub < cutoff:            # 窗口外的不给（LLM 要看当下）
                continue
            if platform and (p.get("app_title") or "") != platform:
                continue
            g = self._growth(series.get(mid) or [])
            if g is not None and g < min_growth_per_hour:
                continue
            if g is None and min_growth_per_hour > 0:
                continue                        # 采样不足 → 达不到"有增速"的门槛
            body = (p.get("summary") or p.get("title") or "").strip()
            out.append({
                "id": mid,
                "title": (p.get("title") or "").strip()[:60],
                "game": p.get("app_title") or "",
                "published_at": _iso(pub),
                "age_hours": round((now_ts - pub) / 3600, 1) if pub else None,
                "comments": _ts(p.get("comments")),
                "likes": _ts(p.get("ups")),
                "views": _ts(p.get("pv_total")),
                "growth_per_hour": round(g, 2) if g is not None else None,
                "is_hot": p.get("is_hot") == "1",
                "monitor_state": p.get("monitor_state") or "active",
                "snippet": " ".join(body.split())[:snippet_chars],
                "fetched_orders": (cache.get(mid) or {}).get("orders", []),
                "comments_on_disk": (cache.get(mid) or {}).get("fetched", 0),
                "has_more": bool((cache.get(mid) or {}).get("has_more")),
            })

        key = {"growth": lambda r: -(r["growth_per_hour"] or 0),
               "comments": lambda r: -(r["comments"] or 0),
               "likes": lambda r: -(r["likes"] or 0),
               "recent": lambda r: r["published_at"]}[sort or "growth"]
        out.sort(key=key)
        out = out[:max(1, int(limit))]

        # ★ 发帖速率：以采集时刻为锚点（用户 2026-10-04 定的算法）
        #   —— 不依赖连续采集的历史快照，publish_time 本身就带信息。
        #   「最近 1h/6h/24h 各发了多少帖」= 这个速率；连续采集只是为了不漏帖。
        anchor = _now()
        buckets = {}
        for hours in (1, 3, 6, 12, 24):
            cutoff = anchor.timestamp() - hours * 3600
            n = sum(1 for r in all_in_window if _ts(r.get("publish_time")) >= cutoff)
            buckets[f"posts_last_{hours}h"] = n
        buckets["posts_per_hour"] = round(buckets["posts_last_6h"] / 6, 2)
        buckets["anchor_at"] = anchor.isoformat(timespec="seconds")
        buckets["covered_threads"] = len(all_in_window)

        return {
            "count": len(out),
            "window_hours": window_hours,
            "sort": sort,
            "posting_rate": buckets,
            "note": ("growth_per_hour=评论增速(需≥2次快照)；posting_rate=发帖速率"
                     "(以采集时刻为锚点，单次采集即可算)" if any(r["growth_per_hour"] is None for r in out)
                     else ""),
            "threads": out,
        }

    @staticmethod
    def _growth(series: List[Dict[str, str]]) -> Optional[float]:
        """评论增速（条/小时）。采样 <2 次或间隔 <10 分钟 → None（不算不出来）。"""
        if len(series) < 2:
            return None
        try:
            t0 = datetime.fromisoformat(series[0]["observed_at"])
            t1 = datetime.fromisoformat(series[-1]["observed_at"])
        except (ValueError, KeyError, TypeError):
            return None
        hours = (t1 - t0).total_seconds() / 3600
        if hours < 1 / 6:
            return None
        return (_ts(series[-1].get("comments")) - _ts(series[0].get("comments"))) / hours

    # ------------------------------------------------------------ ② 深挖（花钱）
    def deepen_threads(self, thread_ids: List[str], pages: int = 3,
                       orders: Optional[List[str]] = None,
                       append: bool = True) -> Dict[str, Any]:
        """按 LLM 点名抓评论。**这是唯一花钱的接口**，受全局预算护栏。

        orders: rank(按赞=谁被认可) / time(按时间=现在谁在说)，可都传
        超预算 → 抛 BudgetExceeded，让调用方（LLM 工具层）转成提示词回去，
                 **不静默截断** —— LLM 才知道要缩小范围。
        """
        asked = list(orders or ["rank"])
        degraded = [o for o in asked if o in UNSUPPORTED_ORDERS]
        orders = [o for o in asked if o in ORDER_MAP] or ["rank"]
        pages = max(1, min(int(pages), 30))

        budget = self._budget()
        est = len(thread_ids) * pages * len(orders)
        if est > budget:
            raise BudgetExceeded(
                f"请求约需 {est} 次调用，超出本轮预算 {budget}。"
                f"请减少 thread 数量、pages 或 orders 后重试。")

        cw = self._crawler()
        cache = self._cache()
        posts = {r["moment_id"]: r for r in _read_csv(self.posts_path) if r.get("moment_id")}
        comments = {c["comment_id"]: c for c in _read_csv(self.comments_path) if c.get("comment_id")}

        results, used_total = [], 0
        for tid in thread_ids:
            p = posts.get(tid)
            if not p:
                results.append({"thread_id": tid, "error": "thread 不存在（可能已过期或未采集）"})
                continue
            gid = _ts(p.get("group_id"))
            if not gid:
                results.append({"thread_id": tid, "error": "该帖没有 group_id，无法定位社区"})
                continue
            aid = p.get("app_id") or ""
            referer = f"https://www.taptap.cn/app/{aid}/topic" if aid else "https://www.taptap.cn/forum"
            known = {c["comment_id"] for c in comments.values() if c.get("moment_id") == tid}
            fetched_now, used = 0, 0
            for od in orders:
                sort_key, order_key = ORDER_MAP[od]
                frm = 0
                for _ in range(pages):
                    d = cw._get(
                        "https://www.taptap.cn/webapiv2/moment-comment/v1/by-moment",
                        {"moment_id": tid, "sort": sort_key, "order": order_key,
                         "regulate_all": "false", "group_id": gid, "limit": 20, "from": frm},
                        referer)
                    used += 1
                    lst = (d or {}).get("data", {}).get("list") or []
                    if not lst:
                        break
                    stamp = _now().isoformat(timespec="seconds")
                    for ci in lst:
                        c = cw_parse_comment(ci, tid, stamp)
                        if c and c["comment_id"] not in known:
                            known.add(c["comment_id"])
                            comments[c["comment_id"]] = c
                            fetched_now += 1
                    frm += len(lst)
                    cw.polite_sleep()
            used_total += used
            claimed = _ts(p.get("comments"))
            prev = cache.get(tid) or {}
            merged_orders = sorted(set((prev.get("orders") or []) + [o for o in orders if used > 0]))
            cache[tid] = {
                "orders": merged_orders,
                "pages": max(_ts(prev.get("pages")), pages),
                "fetched": (prev.get("fetched") or 0) + fetched_now,
                "last_fetched_at": _now().isoformat(timespec="seconds"),
                "has_more": len(known) < claimed,
            }
            results.append({
                "thread_id": tid,
                "title": (p.get("title") or "")[:60],
                "fetched_new": fetched_now,
                "fetched_total_on_disk": len(known),
                "total_claimed_by_platform": claimed,
                "has_more": len(known) < claimed,
                "hint": ("还有更多评论，可再调 deepen_threads 续抓" if len(known) < claimed else "已抓齐"),
                "api_calls": used,
            })

        if append and comments:
            self._write_comments(comments)
        self._save_cache(cache)
        return {"fetched": sum(r.get("fetched_new", 0) for r in results),
                "api_calls": used_total,
                "budget_remaining": max(0, budget - used_total),
                "degraded_orders": {o: UNSUPPORTED_ORDERS[o] for o in degraded},
                "results": results}

    # ------------------------------------------------------------ ③ 读缓存（免费）
    def read_comments(self, thread_id: str, order: str = "rank",
                      limit: int = 20, min_likes: int = 0) -> Dict[str, Any]:
        """读已抓回的评论（**不花接口调用**）。LLM 判断玩家态度用这个。

        order: rank(按赞) / time(按时间)。接口只给 rank 序，time 是**本地按
                publish_time 重排**（拿不到"平台最新排序"，这里如实说明）。
        """
        if order not in ("rank", "time"):
            raise ValueError("order 只能是 rank（按赞）或 time（本地按发布时间重排）")
        rows = [c for c in _read_csv(self.comments_path) if c.get("moment_id") == thread_id]
        if min_likes:
            rows = [c for c in rows if _ts(c.get("supports")) >= min_likes]
        rows.sort(key=lambda c: (-_ts(c.get("supports")), -_ts(c.get("publish_time"))))
        if order == "time":
            rows.sort(key=lambda c: -_ts(c.get("publish_time")))
        total = len(rows)

        posts = {r["moment_id"]: r for r in _read_csv(self.posts_path) if r.get("moment_id")}
        claimed = _ts((posts.get(thread_id) or {}).get("comments"))
        out = [{"id": c["comment_id"],
                "text": (c.get("content") or "")[:400],
                "likes": _ts(c.get("supports")),
                "posted_at": _iso(_ts(c.get("publish_time"))),
                "author": (c.get("author_name") or "")[:16] or "匿名"}
               for c in rows[:max(1, int(limit))]]
        return {
            "thread_id": thread_id,
            "title": ((posts.get(thread_id) or {}).get("title") or "")[:60],
            "order": order,
            "returned": len(out),
            "on_disk": total,
            "total_claimed_by_platform": claimed,
            "has_more": total < claimed,
            "hint": ("平台上还有未抓取的评论，可先 deepen_threads 再读"
                     if total < claimed else "已抓齐，这是全部"),
            "comments": out,
        }

    # ------------------------------------------------------------ 内部
    def _crawler(self):
        """复用 L1 的爬虫（限速/重试/X-UA 都在那一层，这里不重复实现）。"""
        if self._crawler_obj is None:
            sys.path.insert(0, os.path.join(_DATA_ROOT, "L1_data_source", "collectors", "taptap"))
            from crawl_taptap_community import CommunityCrawler, load_xua, load_cookie  # noqa
            self._crawler_obj = CommunityCrawler(load_xua(), 0.8, 1.5, 30, load_cookie())
        return self._crawler_obj

    def _write_comments(self, comments: Dict[str, Dict[str, str]]) -> None:
        fields = ["moment_id", "comment_id", "author_name", "author_id_hash", "content",
                  "supports", "publish_time", "crawled_at"]
        rows = sorted(comments.values(), key=lambda c: -_ts(c.get("publish_time")))
        with open(self.comments_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})


def cw_parse_comment(item: Dict[str, Any], moment_id: str, stamp: str) -> Optional[Dict[str, str]]:
    """复用 L1 的评论解析（口径与主爬虫一致，不另写一套）。"""
    try:
        from crawl_taptap_community import parse_comment  # noqa
    except ImportError:      # 没在 sys.path 上时退化为最小解析
        c = item.get("comment") or item
        cid = c.get("id_str") or c.get("id")
        if not cid:
            return None
        return {"moment_id": moment_id, "comment_id": str(cid), "content": "",
                "supports": "0", "publish_time": "", "crawled_at": stamp}
    return parse_comment(item, moment_id, stamp)


# ============================================================ LLM 工具层
def tool_schemas() -> List[Dict[str, Any]]:
    """喂给 LLM 的 function calling 定义（OpenAI 风格，DeepSeek/OpenRouter 都吃）。"""
    def fn(name: str, desc: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
        return {"type": "function",
                "function": {"name": name, "description": desc,
                             "parameters": {"type": "object", "properties": props,
                                            "required": required}}}

    return [
        fn("list_threads",
           "列出社区里正在被讨论的帖子目录（不消耗采集预算）。"
           "先用它了解有哪些帖子、话题是什么、哪些在爆发，再决定深挖哪几个。",
           {"window_hours": {"type": "integer", "description": "只看最近多少小时发布的帖子", "default": 48},
            "sort": {"type": "string", "enum": ["growth", "comments", "likes", "recent"],
                     "description": "排序：growth=增速优先（正在爆发的），comments=讨论量最多，"
                                    "likes=点赞最多，recent=最新发布"},
            "limit": {"type": "integer", "description": "返回条数，默认 20，上限 100"},
            "min_growth_per_hour": {"type": "number",
                                    "description": "只看评论增速≥该值（条/小时）的帖子；0=不过滤"},
            "platform": {"type": "string", "description": "只看某个游戏社区，空=全部"}},
           []),
        fn("deepen_threads",
           "抓取指定帖子的评论（消耗采集预算）。"
           "★ 会真正访问接口，请只对 list_threads 里挑中的帖子调用。",
           {"thread_ids": {"type": "array", "items": {"type": "string"},
                           "description": "要深挖的帖子 id 列表（来自 list_threads）"},
            "pages": {"type": "integer", "default": 3,
                      "description": "每个帖子每种排序翻几页，每页20条"},
            "orders": {"type": "array", "items": {"type": "string", "enum": ["rank", "time"]},
                       "description": "排序通道。★接口目前只支持 rank（按赞）；"
                                      "传 time 会被自动降级为 rank 并在返回值里说明原因。"
                                      "要按时间读评论，用 read_comments(order=time) 本地重排。"}},
           ["thread_ids"]),
        fn("read_comments",
           "读取已抓取到的评论（不消耗预算）。抓完用这个读评论内容做判断。",
           {"thread_id": {"type": "string"},
            "order": {"type": "string", "enum": ["rank", "time"], "default": "rank"},
            "limit": {"type": "integer", "default": 20},
            "min_likes": {"type": "integer", "default": 0,
                           "description": "只看点赞≥该数的评论"}},
           ["thread_id"]),
    ]


def dispatch(name: str, arguments: Dict[str, Any], feed: Optional[ThreadFeed] = None) -> Dict[str, Any]:
    """工具调用分发。BudgetExceeded 转成结构化提示，让 LLM 自己调整后重试。"""
    f = feed or ThreadFeed()
    try:
        if name == "list_threads":
            return f.list_threads(**(arguments or {}))
        if name == "deepen_threads":
            return f.deepen_threads(**(arguments or {}))
        if name == "read_comments":
            return f.read_comments(**(arguments or {}))
        return {"error": f"未知工具：{name}",
                "available": [t["function"]["name"] for t in tool_schemas()]}
    except BudgetExceeded as e:
        return {"error": "budget_exceeded", "reason": str(e),
                "advice": "请减少 thread 数量或 pages/orders 后重试"}
    except TypeError as e:
        return {"error": "bad_arguments", "reason": str(e)}
    except Exception as e:                       # 工具失败不炸掉整个 Agent 循环
        return {"error": type(e).__name__, "reason": str(e)[:300]}


if __name__ == "__main__":
    feed = ThreadFeed()
    print(json.dumps(feed.list_threads(window_hours=72, sort="growth", limit=8),
                     ensure_ascii=False, indent=2))