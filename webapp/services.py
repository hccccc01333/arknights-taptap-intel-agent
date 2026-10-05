# -*- coding: utf-8 -*-
"""webapp 服务层：把六层模块组装成一个线程安全的单例。

★ 复用而非重写：IntelligenceFeed / Workflow / AssetStudio / ExecutionCenter /
  ExperimentEngine / AlertSystem / Lineage 全部直接用 L4/L5/L6 的现成模块 ——
  权限矩阵、写入策略、facts 纪律在层内生效，Web 层只做 HTTP 适配。

★ 线程模型：SQLite 连接不能跨线程使用；与其给三层 DB 构造器都加
  check_same_thread，不如把**全部 DB 工作收敛到一个专职工作线程**
  （max_workers=1 的 executor）——天然串行、天然线程安全，骨架流量足够。
  以后换异步/连接池时只改这一个文件。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "L6_execution"),
           os.path.join(_ROOT, "L5_memory"), os.path.join(_ROOT, "L4_intelligence")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WEBAPP_VERSION = "0.1.0"


class WebState:
    """应用级单例：在**专职工作线程**里构建 L6 App 组合，之后所有 DB 操作都提交到该线程。"""

    def __init__(self) -> None:
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="webapp-db")
        self._lock = threading.Lock()
        from execution.pipeline import App          # L6 组合根（延迟导入，便于测试 patch）
        self.app: Any = self._ex.submit(App).result()
        self.sockets: set = set()                   # WebSocket 连接（async 侧管理）

    def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """把一个 DB 操作提交到工作线程并等结果（异常原样抛给路由层）。"""
        with self._lock:
            return self._ex.submit(fn, *args, **kwargs).result()

    def close(self) -> None:
        self._ex.submit(lambda: self.app.close()).result()
        self._ex.shutdown(wait=True)


_state: Optional[WebState] = None
_state_lock = threading.Lock()


def get_state() -> WebState:
    global _state
    if _state is None:
        with _state_lock:
            if _state is None:
                _state = WebState()
    return _state


def reset_state() -> None:
    """测试用：关闭并清空单例（env 变了要重建）。"""
    global _state
    with _state_lock:
        if _state is not None:
            try:
                _state.close()
            except Exception:
                pass
            _state = None


# ---------------------------------------------------------------- 读路径适配
def feed(limit: int = 50) -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.feed.build(limit=limit))


def communities(refresh: bool = False) -> Dict[str, Any]:
    """L4 社区报告列表：读 L3 meta 的社区检测结果 + L4 报告生成。

    缓存策略：community_reports.json 比 L3 库新就直接用；
    L3 刚跑过（或调用方强制 refresh）才重算 —— 报告是派生物，重算幂等。
    """
    st = get_state()
    return st.run(lambda: _communities_impl(st, refresh))


def _communities_impl(st: WebState, refresh: bool) -> Dict[str, Any]:
    from intelligence.community_report import generate_all   # L4 模块（sys.path 已含 L4_intelligence）

    l3_db = os.path.join(_ROOT, "data", "state", "l3_trend.sqlite3")
    out_json = os.path.join(_ROOT, "data", "state", "community_reports.json")
    cache_fresh = (os.path.exists(out_json) and os.path.exists(l3_db)
                   and os.path.getmtime(out_json) >= os.path.getmtime(l3_db))
    if refresh or not cache_fresh:
        reports = generate_all(l3_db)
        source = "fresh"
    else:
        with open(out_json, encoding="utf-8") as f:
            cached = json.load(f)
        reports, source = cached.get("reports") or [], "cache"
    return {"count": len(reports), "reports": reports, "source": source}


def communities_raw() -> Dict[str, Any]:
    """L3 meta 原始社区数据（调试用，不经过 L4 报告生成）。"""
    st = get_state()
    return st.run(lambda: _communities_raw_impl(st))


def _communities_raw_impl(st: WebState) -> Dict[str, Any]:
    import sqlite3
    l3_db = os.path.join(_ROOT, "data", "state", "l3_trend.sqlite3")
    if not os.path.exists(l3_db):
        return {"count": 0, "communities": []}
    con = sqlite3.connect(l3_db)
    try:
        row = con.execute("SELECT value FROM meta WHERE key='community_data'").fetchone()
    finally:
        con.close()
    comms = json.loads(row[0]) if row else []
    return {"count": len(comms), "communities": comms}


# ---------------------------------------------------------------- 采集参数（前端可调）
_CRAWL_CFG_PATH = os.path.join(_ROOT, "data", "state", "crawl_config.json")

# 默认值 + 取值范围（前端按这个渲染控件，后端按这个校验）
CRAWL_DEFAULTS: Dict[str, Any] = {
    "fresh_hours": 72,          # 新帖窗口：只收最近 N 小时发布的帖
    "max_age_days": 14,         # 监测窗口：超过 N 天无增长就关监测
    "max_pages": 12,            # 每轮翻页深度
    "elite_pages": 2,           # 精华流单独抓几页
    "max_comment_calls": 150,   # 每轮评论接口预算
    "comment_pages_per_post": 10,  # 新帖评论抓全页数上限（每页 20 条）
    "hot_ups_threshold": 50,    # 点赞阈值：达到就抓全评论 + 重点监测
    "sleep_min": 0.8,
    "sleep_max": 1.5,
}
CRAWL_LIMITS: Dict[str, Any] = {
    "fresh_hours": (1, 720), "max_age_days": (1, 90), "max_pages": (1, 60),
    "elite_pages": (0, 10), "max_comment_calls": (10, 600),
    "comment_pages_per_post": (1, 30), "hot_ups_threshold": (0, 10000),
    "sleep_min": (0.3, 5.0), "sleep_max": (0.5, 10.0),
}
CRAWL_LABELS: Dict[str, str] = {
    "fresh_hours": "新帖窗口（小时）",
    "max_age_days": "监测窗口（天）",
    "max_pages": "每轮翻页深度",
    "elite_pages": "精华流页数",
    "max_comment_calls": "评论接口预算（次/轮）",
    "comment_pages_per_post": "新帖评论页数上限",
    "hot_ups_threshold": "点赞阈值（重点监测线）",
    "sleep_min": "请求最小间隔（秒）",
    "sleep_max": "请求最大间隔（秒）",
}


def crawl_config() -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: _crawl_config_impl())


def _crawl_config_impl() -> Dict[str, Any]:
    import json as _json
    cur = dict(CRAWL_DEFAULTS)
    if os.path.exists(_CRAWL_CFG_PATH):
        try:
            with open(_CRAWL_CFG_PATH, encoding="utf-8") as f:
                cur.update(_json.load(f) or {})
        except (ValueError, OSError):
            pass
    return {"values": cur, "defaults": CRAWL_DEFAULTS,
            "limits": CRAWL_LIMITS, "labels": CRAWL_LABELS,
            "path": _CRAWL_CFG_PATH}


def save_crawl_config(values: Dict[str, Any]) -> Dict[str, Any]:
    """前端改采集参数 → 落 data/state/crawl_config.json（爬虫下次运行自动读取）。"""
    import json as _json
    cur = dict(CRAWL_DEFAULTS)
    if os.path.exists(_CRAWL_CFG_PATH):
        try:
            with open(_CRAWL_CFG_PATH, encoding="utf-8") as f:
                cur.update(_json.load(f) or {})
        except (ValueError, OSError):
            pass
    for k, v in (values or {}).items():
        if k not in CRAWL_DEFAULTS:
            continue
        lo, hi = CRAWL_LIMITS[k]
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{CRAWL_LABELS.get(k, k)} 必须是数字")
        n = max(lo, min(hi, n))
        cur[k] = int(n) if float(n).is_integer() else n
    if cur["sleep_min"] > cur["sleep_max"]:     # 防止把节奏配反
        cur["sleep_min"], cur["sleep_max"] = cur["sleep_max"], cur["sleep_min"]
    os.makedirs(os.path.dirname(_CRAWL_CFG_PATH), exist_ok=True)
    with open(_CRAWL_CFG_PATH, "w", encoding="utf-8") as f:
        _json.dump(cur, f, ensure_ascii=False, indent=2)
    return _crawl_config_impl()
# ---------------------------------------------------------------- L1 数据源视图
def sources() -> Dict[str, Any]:
    """第一层（采集层）全貌：源注册表 + 入库统计 + 采集运行 + 配对诊断。"""
    st = get_state()
    return st.run(lambda: _sources_impl(st))


def _sources_impl(st: WebState) -> Dict[str, Any]:
    import csv
    import sqlite3

    reg_db = os.path.join(_ROOT, "data", "state", "l1_source_registry.sqlite3")
    l2_db = os.path.join(_ROOT, "data", "state", "l2_processed.sqlite3")

    # ---- 注册表 + 断点 ----
    con = sqlite3.connect(reg_db)
    con.row_factory = sqlite3.Row
    srcs = [dict(r) for r in con.execute(
        "SELECT source_id, platform, source_name, connector, enabled, priority, config "
        "FROM source_registry ORDER BY enabled DESC, priority DESC")]
    checkpoints = {r["source_id"]: dict(r) for r in con.execute(
        "SELECT source_id, last_success_at, cursor FROM source_checkpoint")}
    runs = [dict(r) for r in con.execute(
        "SELECT source_id, started_at, finished_at, status, records, error_type "
        "FROM crawl_run ORDER BY started_at DESC LIMIT 24")]
    con.close()
    last_run: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        last_run.setdefault(r["source_id"], r)

    # ---- L2 入库统计（按源 × 内容类型 × 配对）----
    per_source: Dict[str, Dict[str, Any]] = {}
    totals = {"content": 0, "comment": 0, "post_like": 0, "with_parent": 0, "comment_with_parent": 0}
    if os.path.exists(l2_db):
        con = sqlite3.connect(l2_db)
        POST_LIKE = ("moment", "video", "post", "article", "hashtag", "review")
        for sid, ctype, n, wp in con.execute(
                "SELECT source_id, content_type, COUNT(*), "
                "SUM(CASE WHEN parent_id IS NOT NULL AND parent_id != '' THEN 1 ELSE 0 END) "
                "FROM content GROUP BY source_id, content_type"):
            d = per_source.setdefault(sid, {"total": 0, "types": {}, "with_parent": 0})
            d["total"] += n
            d["types"][ctype or "unknown"] = n
            d["with_parent"] += wp
            totals["content"] += n
            if ctype == "comment":
                totals["comment"] += n
                totals["comment_with_parent"] += wp
            elif ctype in POST_LIKE:
                totals["post_like"] += n
            totals["with_parent"] += wp
        con.close()

    # ---- 原始文件配对诊断（帖子/视频 ↔ 评论覆盖）----
    def _col_set(path: str, col: str) -> set:
        if not os.path.exists(path):
            return set()
        with open(path, encoding="utf-8-sig", newline="") as f:
            return {row.get(col, "") for row in csv.DictReader(f) if row.get(col)}

    tap_posts = _col_set(os.path.join(_ROOT, "data/raw/taptap/discovery_posts.csv"), "moment_id")
    tap_cmts = _col_set(os.path.join(_ROOT, "data/raw/taptap/discovery_comments.csv"), "moment_id")
    bili_vids = _col_set(os.path.join(_ROOT, "data/raw/bilibili/videos_sample.csv"), "aid")
    bili_cmts = _col_set(os.path.join(_ROOT, "data/raw/bilibili/comments_sample.csv"), "aid")
    pairing = [
        {"label": "TapTap 帖子↔评论", "total": len(tap_posts),
         "covered": len(tap_posts & tap_cmts)},
        {"label": "B站 视频↔评论", "total": len(bili_vids),
         "covered": len(bili_vids & bili_cmts)},
    ]

    # ---- thread 监测（爆火检测的数据基础：计数快照 + 监测状态）----
    def _read_csv_rows(path: str) -> List[Dict[str, str]]:
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    threads: Dict[str, Any] = {}
    for label, sub in (("单游戏社区（S2）", "taptap/community"),
                       ("平台发现流（S5/S6）", "taptap")):
        base = os.path.join(_ROOT, "data", "raw", sub)
        posts = _read_csv_rows(os.path.join(base, "posts.csv")) or \
            _read_csv_rows(os.path.join(base, "discovery_posts.csv"))
        snaps = _read_csv_rows(os.path.join(base, "thread_snapshots.csv"))
        cmts = _read_csv_rows(os.path.join(base, "comments.csv")) or \
            _read_csv_rows(os.path.join(base, "discovery_comments.csv"))
        by_post: Dict[str, int] = {}
        for c in cmts:
            by_post[c.get("moment_id", "")] = by_post.get(c.get("moment_id", ""), 0) + 1
        # 每个 thread 只取最近一次快照
        latest: Dict[str, Dict[str, str]] = {}
        for s in snaps:
            latest[s.get("moment_id", "")] = s
        active = sum(1 for p in posts if (p.get("monitor_state") or "active") == "active")
        covered = sum(1 for p in posts if int(by_post.get(p.get("moment_id", ""), 0)) > 0)
        hottest = sorted(
            (p for p in posts if p.get("moment_id") in latest),
            key=lambda p: int(latest[p["moment_id"]].get("comment_delta") or 0),
            reverse=True)[:5]
        threads[label] = {
            "total": len(posts), "active": active, "snapshots": len(snaps),
            "comments": len(cmts), "comment_covered": covered,
            "growing": [
                {"title": (p.get("title") or "(无标题)")[:34],
                 "delta": int(latest[p["moment_id"]].get("comment_delta") or 0),
                 "comments": int(latest[p["moment_id"]].get("comments") or 0)}
                for p in hottest if int(latest[p["moment_id"]].get("comment_delta") or 0) > 0],
        }

    # ---- 组装源行 ----
    TYPE_ZH = {"moment": "帖子", "comment": "评论", "video": "视频", "review": "评测",
               "article": "公告", "hashtag": "话题", "post": "博文"}
    out = []
    for s in srcs:
        try:
            cfg = json.loads(s.pop("config") or "{}")
        except ValueError:
            cfg = {}
        sid = s["source_id"]
        cp = checkpoints.get(sid, {})
        lr = last_run.get(sid, {})
        st_ = per_source.get(sid, {"total": 0, "types": {}, "with_parent": 0})
        out.append({
            "source_id": sid, "platform": s["platform"], "name": s["source_name"],
            "connector": s["connector"], "enabled": bool(s["enabled"]),
            "dataset": cfg.get("dataset", ""), "file": cfg.get("file", ""),
            "last_success_at": cp.get("last_success_at"),
            "last_run": {k: lr.get(k) for k in ("started_at", "status", "records", "error_type")} if lr else None,
            "ingested": st_["total"],
            "types": {TYPE_ZH.get(k, k): v for k, v in st_["types"].items()},
        })

    # ---- S1 社区索引（app_id → group_id 寻址基础 + 社区体量四要素）----
    map_csv = os.path.join(_ROOT, "data/raw/taptap/community/community_map.csv")
    game_index = {"total": 0, "addressable": 0, "top": [], "covered": False}
    if os.path.exists(map_csv):
        rows = _read_csv_rows(map_csv)
        addr = [r for r in rows if r.get("app_id") and r.get("group_id")]
        game_index["total"] = len(rows)
        game_index["addressable"] = len(addr)
        game_index["covered"] = True
        def _n(r, k):
            try:
                return int(r.get(k) or 0)
            except (TypeError, ValueError):
                return 0
        top = sorted(addr, key=lambda r: -_n(r, "topic_count"))[:8]
        game_index["top"] = [{"title": (r.get("title") or "")[:16], "app_id": r.get("app_id"),
                              "group_id": r.get("group_id"),
                              "fav": _n(r, "favorite_count"), "topics": _n(r, "topic_count"),
                              "recent": _n(r, "recent_topic_count"),
                              "official": _n(r, "official_topic_count")} for r in top]

    return {
        "sources": out, "crawl_runs": runs[:12], "pairing": pairing,
        "threads": threads, "game_index": game_index,
        "totals": {**totals, "enabled": sum(1 for s in srcs if s["enabled"]),
                   "registered": len(srcs)},
    }


def workspace(event_id: str) -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.ws.trend(event_id))


def opportunities(event_id: str) -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.ws.opportunities(event_id))


def studio(event_id: str, opportunity_id: Optional[str] = None) -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.ws.creative_studio(event_id, opportunity_id))


def funnel() -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.lineage.funnel(st.app.up))


def value() -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.lineage.value(st.app.wf, st.app.up))


def tta() -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.wf.time_to_action())


def trace(event_id: str) -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: st.app.lineage.trace_event(event_id))


def alerts_pending() -> List[Dict[str, Any]]:
    st = get_state()
    return st.run(lambda: st.app.alerts.pending())


def workflow_items() -> List[Dict[str, Any]]:
    st = get_state()
    return st.run(lambda: st.app.wf.items_by_state())


def experiments() -> List[Dict[str, Any]]:
    st = get_state()
    return st.run(lambda: st.app.db.query(
        "SELECT experiment_id, creative_id, event_id, name, status, result_state,"
        " result_reason, primary_metric FROM experiment ORDER BY updated_at DESC LIMIT 100"))


def plans() -> List[Dict[str, Any]]:
    st = get_state()
    rows = st.run(lambda: st.app.db.query(
        "SELECT * FROM execution_plan ORDER BY updated_at DESC LIMIT 100"))
    for r in rows:
        r["channels"] = st.app.db.loads(r.get("channels"))
        r["asset_ids"] = st.app.db.loads(r.get("asset_ids"))
    return rows


def stats() -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: {t: st.app.db.table_count(t) for t in
                           ("workflow_item", "workflow_event", "production_asset",
                            "execution_plan", "experiment", "alert")})


# ---------------------------------------------------------------- 写路径适配
# 返回值统一 {"ok": True, ...} 或抛异常：
#   ValueError → 400；execution.audit.PermissionDenied → 403；KeyError → 404


def _run_write(fn: Callable[[], Any], actor: str, action: str) -> Any:
    result = get_state().run(fn)
    broadcast(actor, action)
    return result


def follow_event(event_id: str, actor: str, role: str,
                 owner: Optional[str] = None, deadline: Optional[str] = None) -> Dict[str, Any]:
    st = get_state()

    def _do():
        ev = st.app.up.event(event_id)
        if not ev:
            raise KeyError(f"事件不存在：{event_id}")
        st.app.wf.ensure_item("event", event_id, title=ev.get("canonical_title") or "",
                              event_id=event_id,
                              detected_at=ev.get("first_detected_at") or "")
        return st.app.wf.apply("event", event_id, "follow", actor, role,
                               deadline=deadline)

    def _assign():
        st.app.wf.assign("event", event_id, owner=owner, reviewer=None,
                         deadline=deadline, actor=actor, role=role)

    out = _run_write(_do, actor, "follow")
    if owner:
        _run_write(_assign, actor, "assign")
    return out


def decide(object_type: str, object_id: str, action: str, actor: str, role: str,
           note: str = "", deadline: Optional[str] = None) -> Dict[str, Any]:
    st = get_state()

    def _do():
        return st.app.wf.apply(object_type, object_id, action, actor, role,
                               note=note, deadline=deadline)

    return _run_write(_do, actor, action)


def assign(object_type: str, object_id: str, owner: Optional[str],
           reviewer: Optional[str], deadline: Optional[str],
           actor: str, role: str) -> Dict[str, Any]:
    st = get_state()

    def _do():
        return st.app.wf.assign(object_type, object_id, owner=owner, reviewer=reviewer,
                                deadline=deadline, actor=actor, role=role)

    return _run_write(_do, actor, "assign")


def creative_from_l4(idea_id: str) -> Dict[str, Any]:
    st = get_state()
    return st.run(lambda: _creative(app=st.app, idea_id=idea_id))


def _creative(app: Any, idea_id: str) -> Dict[str, Any]:
    from execution.feed import l4_one, l4_json
    row = l4_one(app.l4, "SELECT * FROM intelligence_creative WHERE idea_id=?", (idea_id,))
    if not row:
        raise KeyError(f"L4 创意不存在：{idea_id}")
    creative = l4_json(app.l4, row["payload"]) or {}
    for k in ("event_id", "analysis_id", "opportunity_id"):
        if creative.get(k) is None and row.get(k):
            creative[k] = row[k]
    if creative.get("score") is None and row.get("score") is not None:
        creative["score"] = row["score"]
    return creative


def _ensure_creative(app: Any, creative: Dict[str, Any]) -> Dict[str, Any]:
    from execution.pipeline import _ensure_creative_item
    return _ensure_creative_item(app, creative)


def generate_assets(idea_id: str, kinds: Optional[List[str]],
                    actor: str, role: str) -> List[Dict[str, Any]]:
    st = get_state()

    def _do():
        creative = _creative(app=st.app, idea_id=idea_id)
        _ensure_creative(st.app, creative)
        return st.app.assets.generate(creative, kinds, actor=actor, role=role)

    return _run_write(_do, actor, "asset_generate")


def approve_asset(asset_id: str, actor: str, role: str) -> Dict[str, Any]:
    st = get_state()
    return _run_write(lambda: st.app.assets.approve(asset_id, actor, role),
                      actor, "asset_approve")


def publish_asset(asset_id: str, actor: str, role: str) -> Dict[str, Any]:
    st = get_state()
    return _run_write(lambda: st.app.assets.mark_published(asset_id, actor, role),
                      actor, "asset_publish")


def create_plan(idea_id: str, channels: List[str], audience: str, start_at: str,
                end_at: str, experiment: bool, actor: str, role: str) -> Dict[str, Any]:
    st = get_state()

    def _do():
        creative = _creative(app=st.app, idea_id=idea_id)
        _ensure_creative(st.app, creative)
        res = st.app.center.create_plan(
            creative_id=idea_id, channels=channels, audience=audience,
            start_at=start_at, end_at=end_at, asset_ids=[],
            experiment_enabled=experiment,
            control="existing_feed" if experiment else "",
            treatment=creative.get("idea_name") or idea_id,
            actor=actor, role=role)
        if experiment:
            res["experiment"] = st.app.experiments.create(
                creative_id=idea_id, plan_id=res["plan_id"],
                event_id=creative.get("event_id") or "",
                name=creative.get("idea_name") or idea_id,
                hypothesis=creative.get("growth_hypothesis") or
                f"{creative.get('idea_name')} 能提升 {creative.get('primary_metric')}",
                population=audience, treatment=creative.get("idea_name") or idea_id,
                control="existing_feed",
                primary_metric=creative.get("primary_metric") or "primary_metric",
                secondary_metrics=creative.get("secondary_metrics") or [],
                actor=actor, role=role)
        return res

    return _run_write(_do, actor, "plan_create")


def plan_transition(plan_id: str, action: str, actor: str, role: str,
                    reason: str = "") -> Dict[str, Any]:
    st = get_state()

    def _do():
        if action == "launch":
            return st.app.center.launch(plan_id, actor, role)
        if action == "complete":
            return st.app.center.complete(plan_id, actor, role)
        if action in ("pause", "stop", "rollback"):
            return st.app.center.kill_switch(plan_id, action, reason or action,
                                             actor, role)
        raise ValueError(f"未知计划动作：{action}")

    return _run_write(_do, actor, f"plan_{action}")


def create_experiment(idea_id: str, plan_id: Optional[str], audience: str,
                      actor: str, role: str) -> Dict[str, Any]:
    st = get_state()

    def _do():
        creative = _creative(app=st.app, idea_id=idea_id)
        _ensure_creative(st.app, creative)
        return st.app.experiments.create(
            creative_id=idea_id, plan_id=plan_id,
            event_id=creative.get("event_id") or "",
            name=creative.get("idea_name") or idea_id,
            hypothesis=creative.get("growth_hypothesis") or "（L4 未给出假设）",
            population=audience, treatment=creative.get("idea_name") or idea_id,
            control="existing_feed",
            primary_metric=creative.get("primary_metric") or "primary_metric",
            secondary_metrics=creative.get("secondary_metrics") or [],
            actor=actor, role=role)

    return _run_write(_do, actor, "experiment_create")


def observe_experiment(experiment_id: str, metric_name: str, metric_class: str,
                       body: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
    st = get_state()

    def _do():
        return st.app.experiments.observe(
            experiment_id, metric_name, metric_class,
            baseline_value=body.get("baseline_value"),
            treatment_value=body.get("treatment_value"),
            x_t=body.get("x_t"), n_t=body.get("n_t"),
            x_c=body.get("x_c"), n_c=body.get("n_c"),
            actor=actor, role=role)

    return _run_write(_do, actor, "experiment_observe")


def finish_experiment(experiment_id: str, actor: str, role: str) -> Dict[str, Any]:
    st = get_state()
    return _run_write(lambda: st.app.experiments.finish(experiment_id, actor, role),
                      actor, "experiment_finish")


def ack_alert(alert_id: str, actor: str, role: str) -> Dict[str, Any]:
    st = get_state()
    return _run_write(lambda: st.app.alerts.acknowledge(alert_id, actor, role),
                      actor, "alert_ack")


# ---------------------------------------------------------------- WebSocket 广播
_clients: set = set()
_clients_lock = threading.Lock()


def register_client(ws: Any) -> None:
    with _clients_lock:
        _clients.add(ws)


def unregister_client(ws: Any) -> None:
    with _clients_lock:
        _clients.discard(ws)


def broadcast(actor: str, action: str) -> None:
    """变更通知（在事件循环侧真正发送；这里只做快照）。"""
    payload = json.dumps({"event": "changed", "actor": actor, "action": action},
                         ensure_ascii=False)
    with _clients_lock:
        dead = []
        for ws in list(_clients):
            try:
                import asyncio
                loop = getattr(ws, "_loop_ref", None)
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_send(ws, payload), loop)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


async def _send(ws: Any, text: str) -> None:
    try:
        await ws.send_text(text)
    except Exception:
        unregister_client(ws)


# ---------------------------------------------------------------- 增长创意（结构化）
CREATIVES_PATH = os.path.join(_ROOT, "data", "state", "growth_creatives.json")
FORMING_PATH = os.path.join(_ROOT, "data", "state", "forming_report.json")
CREATIVE_TYPE_ZH = {
    "content": "热点专题", "community": "讨论活动", "ugc": "投稿挑战",
    "crm": "Push 触达", "h5": "站外传播", "social": "社媒传播",
    "creator": "达人联动", "publisher": "厂商合作", "product": "产品机制",
}
RISK_KIND = {"high": "hot", "medium": "warm", "low": "cool"}


def growth_creatives() -> Dict[str, Any]:
    """读结构化增长创意 + 形成中报告（webapp 创意卡片按 schema 直接渲染）。"""
    import json as _json
    creatives: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    if os.path.exists(CREATIVES_PATH):
        try:
            with open(CREATIVES_PATH, encoding="utf-8") as f:
                data = _json.load(f) or {}
            creatives = data.get("creatives") or data.get("reports") or []
            meta = data.get("meta") or {}
        except (ValueError, OSError):
            creatives = []
    for c in creatives:
        c["type_zh"] = CREATIVE_TYPE_ZH.get(c.get("creative_type") or "", c.get("creative_type") or "")
        for r in (c.get("risks") or []):
            r["kind"] = RISK_KIND.get(r.get("level") or "medium", "cool")
    forming: Dict[str, Any] = {}
    if os.path.exists(FORMING_PATH):
        try:
            with open(FORMING_PATH, encoding="utf-8") as f:
                forming = _json.load(f) or {}
        except (ValueError, OSError):
            forming = {}
    return {"creatives": creatives, "meta": meta,
            "forming_game": (forming.get("forming_game") or [])[:10],
            "channels": {k: {"rounds": v.get("rounds"), "stages": v.get("stages"),
                             "note": v.get("note")}
                         for k, v in (forming.get("channels") or {}).items()}}
