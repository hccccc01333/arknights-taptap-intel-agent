"""Persist observed provider failures so independent stages share backoff."""
from datetime import datetime, timedelta, timezone
import json

from agent_v2.model import LiveModel, model_status as legacy_status
from agent_v2.store import now_iso
from .opencode_zen import OpenCodeModel, StructuredDeliveryError, ZEN_MODELS, status as zen_status
from .space_bunny import SpaceBunnyModel, MODEL as SPACE_BUNNY_MODEL, status as bunny_status
from .deepseek import DeepSeekModel, ALIASES as DEEPSEEK_MODELS, status as deepseek_status


def model_status(selected=None):
    selected = selected or legacy_status()["model"]
    if selected in DEEPSEEK_MODELS:return deepseek_status(selected)
    if selected==SPACE_BUNNY_MODEL:return bunny_status()
    return zen_status(selected) if selected.startswith("opencode/") else legacy_status(selected)


def model_options():
    return [deepseek_status(), bunny_status()] + [model_status("opencode/"+key) for key in ZEN_MODELS] + [model_status(m) for m in
            ("openrouter/free", "nvidia/nemotron-3-ultra-550b-a55b:free")]


def scope(model):
    if model in DEEPSEEK_MODELS:return "deepseek:flash"
    if model==SPACE_BUNNY_MODEL:return "spacebunny:alpha"
    if model.startswith("opencode/"):return model
    return "openrouter:free" if model=="openrouter/free" or model.endswith(":free") else model


QUOTA_REASON = "已确认 Zen 免费额度耗尽，AI 等待恢复"


def zen_quota(store):
    row=store.conn.execute("SELECT value FROM settings WHERE key='zen_quota'").fetchone()
    return json.loads(row[0]) if row else {"hold":False,"updated_at":None}


def set_zen_quota(store,hold):
    if type(hold) is not bool:raise ValueError("需设置 Zen 免费额度暂停状态")
    value={"hold":hold,"updated_at":now_iso()}
    with store.conn:
        store.conn.execute("INSERT INTO settings VALUES('zen_quota',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (json.dumps(value,ensure_ascii=False),))
    if not hold and gate(store)["status"]=="ready":
        from .work import wake_provider_work
        wake_provider_work(store)
    return value


def gate(store,selected=None):
    selected=selected or model_status(store.model_setting())["model"]
    hold=store.conn.execute("SELECT value FROM settings WHERE key=?",('provider_hold:'+scope(selected),)).fetchone()
    if hold and json.loads(hold[0]).get('hold'):
        return {'status':'deferred','scope':scope(selected),'reason':'用户确认模型额度不足，AI 等待恢复',
            'observed_at':json.loads(hold[0])['updated_at'],'retry_at':None,'manual_resume':True,
            'note':'采集继续；恢复额度并明确解除暂停前，不自动尝试付费调用。'}
    quota=zen_quota(store)
    if selected.startswith("opencode/") and quota["hold"]:
        return {"status":"deferred","scope":"opencode:free","reason":QUOTA_REASON,
                "observed_at":quota["updated_at"],"retry_at":None,"manual_resume":True,
                "note":"额度恢复时间未知；确认恢复后继续，期间采集与正文补读照常运行。"}
    row=store.conn.execute("""SELECT * FROM model_gate WHERE retry_at>? AND
        (scope=? OR (scope='opencode:free' AND ? LIKE 'opencode/%' AND reason IN
        ('OpenCode Zen 免费层拒绝调用（HTTP 403）','模型服务限流','免费模型日额度耗尽')))
        ORDER BY retry_at DESC LIMIT 1""",(now_iso(),scope(selected),selected)).fetchone()
    return {"status":"deferred",**dict(row),"note":"重试时间是系统退避时间，不代表供应商额度已恢复。"} if row else {"status":"ready","model":selected}

def set_provider_hold(store,model,hold):
    if type(hold) is not bool:raise ValueError('暂停状态需为布尔值')
    value={'hold':hold,'updated_at':now_iso()}
    with store.conn:store.conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
        ('provider_hold:'+scope(model),json.dumps(value,ensure_ascii=False)))
    if not hold and gate(store,model)['status']=='ready':
        from .work import wake_provider_work
        wake_provider_work(store)
    return value


def failure(store,model,error,*,observed_at=None):
    message=str(error)
    if model==SPACE_BUNNY_MODEL and "502" in message:
        minutes=10;reason="太空兔上游请求失败（HTTP 502）"
    elif model==SPACE_BUNNY_MODEL and "401" in message:
        minutes=1440;reason="太空兔密钥或访问权限不可用"
    elif model==SPACE_BUNNY_MODEL and any(code in message for code in ("400","403","404")):
        minutes=1440;reason="太空兔接口或模型配置不可用"
    elif "429" in message:
        minutes=360 if "当天调用额度" in message or "free-models-per-day" in message else 15
        reason="免费模型日额度耗尽" if minutes==360 else "模型服务限流"
    elif "403" in message and model.startswith("opencode/"):
        minutes=60;reason="OpenCode Zen 免费层拒绝调用（HTTP 403）"
    elif "402" in message:
        minutes=1440;reason="模型账户额度或服务配置不可用"
    else:
        minutes=10;reason="模型响应未完成，等待重试"
    observed_at=observed_at or now_iso()
    retry=(datetime.fromisoformat(observed_at)+timedelta(minutes=minutes)).isoformat(timespec="seconds")
    failure_scope="opencode:free" if model.startswith("opencode/") and ("403" in message or "429" in message) else scope(model)
    with store.conn:
        store.conn.execute("""INSERT INTO model_gate VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET
          reason=excluded.reason,observed_at=excluded.observed_at,retry_at=excluded.retry_at""",
          (failure_scope,reason,observed_at,retry))
    return {"status":"deferred","reason":reason,"retry_at":retry}


def import_failure_history(store):
    if store.conn.execute("SELECT 1 FROM settings WHERE key='v3_model_gate_migration'").fetchone():return
    for row in store.conn.execute("SELECT model,error,finished_at FROM run WHERE status='failed' AND model IS NOT NULL ORDER BY finished_at DESC LIMIT 12").fetchall():
        if row["finished_at"] and row["error"] and ("429" in row["error"] or "402" in row["error"]):
            if not store.conn.execute("SELECT 1 FROM model_gate WHERE scope=?",(scope(row["model"]),)).fetchone():
                failure(store,row["model"],row["error"],observed_at=row["finished_at"])
    with store.conn:store.conn.execute("INSERT INTO settings VALUES('v3_model_gate_migration','true')")


class GatedModel:
    def __init__(self,store):
        self.store=store
        selected=model_status(store.model_setting())["model"]
        cooldown=gate(store,selected)
        if cooldown["status"]=="deferred":
            from L4_intelligence.intelligence.llm import LLMUnavailable
            raise LLMUnavailable(cooldown["reason"])
        if selected in DEEPSEEK_MODELS:self.client=DeepSeekModel(selected,reasoning_effort=store.reasoning_setting(),output_limit=store.output_setting())
        elif selected==SPACE_BUNNY_MODEL:self.client=SpaceBunnyModel(reasoning_effort=store.reasoning_setting())
        else:self.client=OpenCodeModel(selected,reasoning_effort=store.reasoning_setting()) if selected.startswith("opencode/") else LiveModel(selected)
        self.model=self.client.model

    @property
    def last_session(self):
        return getattr(self.client,"last_session",None)

    @property
    def supports_tasks(self):
        return bool(getattr(self.client,"supports_tasks",False))

    @property
    def supports_research_tasks(self):return self.supports_tasks

    @property
    def compact_tasks(self):
        return bool(getattr(self.client,"compact_tasks",False))

    @property
    def supports_main_agent(self):return self.supports_tasks and self.compact_tasks

    @property
    def transport(self):return getattr(self.client,"transport","opencode-agent" if self.model.startswith("opencode/") else "chat-completions")

    def run_task(self,*args,**kwargs):
        cooldown=gate(self.store,self.model)
        if cooldown['status']=='deferred':
            from L4_intelligence.intelligence.llm import LLMUnavailable
            raise LLMUnavailable(cooldown['reason'])
        try:return self.client.run_task(*args,**kwargs)
        except StructuredDeliveryError:raise
        except Exception as error:
            failure(self.store,self.model,error)
            raise
    def decide(self,messages,definitions):
        cooldown=gate(self.store,self.model)
        if cooldown['status']=='deferred':
            from L4_intelligence.intelligence.llm import LLMUnavailable
            raise LLMUnavailable(cooldown['reason'])
        try:
            result=self.client.decide(messages,definitions);self.model=self.client.model
            return result
        except Exception as error:
            self.model=self.client.model;failure(self.store,self.model,error)
            raise


def task_metadata(response):
    return {k:response[k] for k in ("model","api_model","usage","seconds","transport","session_id",
            "request_id","task_id","input_file","reported_cost","cost_status","reasoning_effort",
            "output_limit","finish_reason","reasoning_present") if k in response}
