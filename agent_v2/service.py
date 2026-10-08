from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

from .engine import DEFAULT_DISCOVERY_TASK, run_agent
from .ingest import ingest
from .model import model_status
from .store import Store, dump, now_iso

_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-v2")
_lock = threading.Lock()
_active: str | None = None
_stop = threading.Event()
_scheduler_started = False
_scheduler_thread: threading.Thread | None = None


def overview():
    store = Store()
    try:
        data = store.overview()
        data["model"] = model_status(store.model_setting())
        data["model_options"] = [model_status(m) for m in ("openrouter/free","nvidia/nemotron-3-ultra-550b-a55b:free","nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free","deepseek-flash")]
        data["active_run"] = store.active_owner()
        from .connectors import CAPABILITIES
        data["connectors"] = [{"platform":p, "capabilities":cap, "health":dict(row) if (row:=store.conn.execute("SELECT * FROM connector_health WHERE platform=?",(p,)).fetchone()) else None} for p,cap in CAPABILITIES.items()]
        data["context"] = store.context()
        data["schedule"] = schedule(store)
        return data
    finally:
        store.close()


def import_sources():
    with _lock:
        if _active:
            raise ValueError("研究正在进行，请在本轮结束后导入")
        store = Store()
        owner = "import_"+uuid4().hex
        try:
            if not store.acquire(owner):
                raise ValueError("已有采集或研究正在执行，请查看其进度")
            return ingest(store)
        finally:
            store.release(owner)
            store.close()


def start_run(task: str, collect_live=True, only_collect=False):
    global _active
    task = task.strip()
    if not task or len(task) > 3000:
        raise ValueError("研究目标须为 1 至 3000 个字符")
    with _lock:
        if _active:
            raise ValueError("已有研究正在执行，请先查看其进度")
        store = Store()
        try:
            run_id = store.create_run(task)
            if not store.acquire(run_id):
                store.finish(run_id,"failed",error="已有采集或研究正在执行")
                raise ValueError("已有采集或研究正在执行，请查看其进度")
        finally:
            store.close()
        _active = run_id
        _worker.submit(_execute, run_id, collect_live, only_collect)
        return {"run_id": run_id, "status": "queued"}


def _execute(run_id, collect_live, only_collect):
    global _active
    store = Store()
    try:
        result = ingest(store)
        store.step(run_id, "ingest", result)
        if collect_live:
            from .connectors import refresh_sources
            store.conn.execute("UPDATE run SET status='running' WHERE run_id=?",(run_id,))
            store.conn.commit()
            live = refresh_sources(store)
            store.step(run_id,"collection",live)
        if only_collect:
            store.finish(run_id,"collected" if live["status"]=="ok" else "partial",result=live)
            return
        run_agent(store, run_id)
    except Exception as error:
        store.finish(run_id, "failed", error=str(error)[:600])
    finally:
        store.release(run_id)
        store.close()
        with _lock:
            _active = None


def get_run(run_id: str):
    store = Store()
    try:
        return store.get_run(run_id)
    finally:
        store.close()


def get_event(event_id: str):
    store = Store()
    try:
        return store.get_event(event_id)
    finally:
        store.close()


def candidates(query=""):
    store = Store()
    try:
        return {"candidates": store.candidates(40, query=query)}
    finally:
        store.close()


def get_brief(days=7):
    from .reports import brief
    store=Store()
    try:
        return brief(store,days)
    finally:
        store.close()


def save_feedback(creative_id, actor, decision, reason, outcome=None):
    store=Store()
    try:
        return store.add_feedback(creative_id,actor,decision,reason,outcome)
    finally:
        store.close()


def business_context(value=None):
    store=Store()
    try:
        return store.set_context(value) if value is not None else store.context()
    finally:
        store.close()


def select_model(value):
    store=Store()
    try:
        if store.active_owner():
            raise ValueError("请在本轮研究结束后切换模型")
        return store.set_model(value)
    finally:
        store.close()


def schedule(store):
    import json
    row=store.conn.execute("SELECT value FROM settings WHERE key='schedule'").fetchone()
    return json.loads(row[0]) if row else {"enabled":False,"interval_minutes":60,"task":DEFAULT_DISCOVERY_TASK,"next_run_at":None}


def save_schedule(value):
    store=Store()
    try:
        if not isinstance(value.get("enabled"),bool) or not isinstance(value.get("interval_minutes"),int) or not 15<=value["interval_minutes"]<=1440 or not isinstance(value.get("task"),str) or not 1<=len(value["task"].strip())<=3000:
            raise ValueError("自动研究需设置 15 至 1440 分钟间隔和具体目标")
        result={k:value[k] for k in ("enabled","interval_minutes","task")}
        result["next_run_at"]=(datetime.now(timezone.utc)+timedelta(minutes=result["interval_minutes"])).isoformat(timespec="seconds") if result["enabled"] else None
        store.conn.execute("INSERT INTO settings VALUES('schedule',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dump(result),))
        store.conn.commit()
        return result
    finally:
        store.close()


def start_scheduler():
    global _scheduler_started, _scheduler_thread
    if _scheduler_started:
        return
    _scheduler_started=True
    _stop.clear()
    def tick():
        while not _stop.wait(30):
            store=Store()
            try:
                config=schedule(store)
                if not config["enabled"] or not config["next_run_at"] or config["next_run_at"]>now_iso() or store.active_owner():
                    continue
                start_run(config["task"],collect_live=True)
                config["next_run_at"]=(datetime.now(timezone.utc)+timedelta(minutes=config["interval_minutes"])).isoformat(timespec="seconds")
                store.conn.execute("UPDATE settings SET value=? WHERE key='schedule'",(dump(config),))
                store.conn.commit()
            except Exception:
                # Individual run errors are retained by the worker; try a later tick.
                pass
            finally:
                store.close()
    _scheduler_thread=threading.Thread(target=tick,name="v2-scheduler",daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    global _scheduler_started
    _stop.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=1)
    _scheduler_started=False
