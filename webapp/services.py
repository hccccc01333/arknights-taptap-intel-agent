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
