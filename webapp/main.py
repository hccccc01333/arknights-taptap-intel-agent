# -*- coding: utf-8 -*-
"""Growth Intelligence OS · Web 应用入口。

    uvicorn webapp.main:app --port 8200     # 或 python -m webapp.main

★ 定位（设计 §45 骨架起步）：
  · 后端 = FastAPI 薄适配层：业务全部复用 L4/L5/L6 模块（services.py），
    **权限矩阵在服务端强制**（execution.audit.require_role），前端只管展示；
  · WebSocket /ws 广播变更事件，前端收到后自动刷新当前视图；
  · SQLite 直连现有各层库 —— Postgres/Redis/Next.js 是 §45 的规模化后续，
    迁移路径见 webapp/README.md。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import webapp.services as S
from webapp.auth import issue_token, parse_token, verify_login

app = FastAPI(title="Growth Intelligence OS", version=S.WEBAPP_VERSION)

# A separately hosted frontend (for example GitHub Pages) can opt into CORS.
# Leave it disabled by default; the local Vite proxy works without CORS.
_FRONTEND_ORIGINS = [origin.strip() for origin in
                     os.environ.get("WEBAPP_ALLOWED_ORIGINS", "").split(",")
                     if origin.strip()]
if _FRONTEND_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_FRONTEND_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )


# ---------------------------------------------------------------- 认证

class LoginBody(BaseModel):
    actor: str
    password: str


def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Authorization: Bearer <token> → {actor, role}。无效/缺失 → 401。"""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(401, "未登录（先 POST /api/login 换取令牌）")
    user = parse_token(token)
    if not user:
        raise HTTPException(401, "令牌无效或已过期")
    return user


@app.post("/api/login")
def login(body: LoginBody) -> Dict[str, Any]:
    user = verify_login(body.actor.strip(), body.password)
    if not user:
        raise HTTPException(401, "账号或密码不正确")
    return {"token": issue_token(user["actor"], user["role"]), "actor": user["actor"],
            "role": user["role"]}


@app.get("/api/me")
def me(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return user


# ---------------------------------------------------------------- 读端点
# 读也需要登录（§43：Viewer 只能看趋势——可见范围控制后续版本细化）

@app.get("/api/feed")
def get_feed(limit: int = 50, user: Dict[str, Any] = Depends(current_user)):
    return S.feed(limit=limit)


@app.get("/api/communities")
def get_communities(refresh: int = 0, user: Dict[str, Any] = Depends(current_user)):
    """L3 图社区检测 + L4 报告生成的社区报告列表（首页主列表）。"""
    return S.communities(refresh=bool(refresh))


@app.get("/api/communities/raw")
def get_communities_raw(user: Dict[str, Any] = Depends(current_user)):
    """L3 meta 里的原始社区数据（不含 L4 叙事，调试用）。"""
    return S.communities_raw()


@app.get("/api/crawl-config")
def get_crawl_config(user: Dict[str, Any] = Depends(current_user)):
    """采集参数（前端可调，落盘后爬虫下次运行自动生效）。"""
    return S.crawl_config()


@app.post("/api/crawl-config")
def set_crawl_config(body: Dict[str, Any], user: Dict[str, Any] = Depends(current_user)):
    """改采集参数。运维权限（require_role → 403）。"""
    from execution.audit import require_role
    require_role(user["role"], "configure")
    out = S.save_crawl_config(body.get("values") or body)
    S.broadcast(user["actor"], "crawl_config")
    return out


@app.get("/api/sources")
def get_sources(user: Dict[str, Any] = Depends(current_user)):
    """第一层（采集层）全貌：源注册表、入库统计、采集运行、配对诊断。"""
    return S.sources()


@app.get("/api/events/{event_id}/workspace")
def get_workspace(event_id: str, user: Dict[str, Any] = Depends(current_user)):
    out = S.workspace(event_id)
    if out is None:
        raise HTTPException(404, f"事件不存在：{event_id}")
    return out


@app.get("/api/events/{event_id}/opportunities")
def get_opportunities(event_id: str, user: Dict[str, Any] = Depends(current_user)):
    out = S.opportunities(event_id)
    if out is None:
        raise HTTPException(404, f"事件不存在：{event_id}")
    return out


@app.get("/api/events/{event_id}/studio")
def get_studio(event_id: str, opportunity: Optional[str] = None,
               user: Dict[str, Any] = Depends(current_user)):
    out = S.studio(event_id, opportunity)
    if out is None:
        raise HTTPException(404, f"事件不存在：{event_id}")
    return out


@app.get("/api/events/{event_id}/trace")
def get_trace(event_id: str, user: Dict[str, Any] = Depends(current_user)):
    return S.trace(event_id)


@app.get("/api/workflow")
def get_workflow(user: Dict[str, Any] = Depends(current_user)):
    return S.workflow_items()


@app.get("/api/plans")
def get_plans(user: Dict[str, Any] = Depends(current_user)):
    return S.plans()


@app.get("/api/experiments")
def get_experiments(user: Dict[str, Any] = Depends(current_user)):
    return S.experiments()


@app.get("/api/experiments/{experiment_id}/monitor")
def get_monitor(experiment_id: str, user: Dict[str, Any] = Depends(current_user)):
    st = S.get_state()
    try:
        return st.run(lambda: st.app.experiments.monitor(experiment_id))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/alerts")
def get_alerts(user: Dict[str, Any] = Depends(current_user)):
    return S.alerts_pending()


@app.get("/api/universe")
def get_universe(limit: int = 240, user: Dict[str, Any] = Depends(current_user)):
    """Universe 数据（设计 §C：映射规则的唯一真源在 webapp/universe.py）。"""
    import webapp.universe as U
    return U.build_universe(limit=limit)


@app.get("/api/funnel")
def get_funnel(user: Dict[str, Any] = Depends(current_user)):
    return S.funnel()


@app.get("/api/value")
def get_value(user: Dict[str, Any] = Depends(current_user)):
    return S.value()


@app.get("/api/tta")
def get_tta(user: Dict[str, Any] = Depends(current_user)):
    return S.tta()


@app.get("/api/stats")
def get_stats(user: Dict[str, Any] = Depends(current_user)):
    return S.stats()


# ---------------------------------------------------------------- 写端点
# 权限 = 角色矩阵（execution.audit.require_role），服务端强制；
# ValueError→400 / PermissionDenied→403 / KeyError→404（见 exception handlers）

class DecideBody(BaseModel):
    action: str
    note: str = ""
    deadline: Optional[str] = None


@app.post("/api/feed/{event_id}/follow")
def follow_event(event_id: str, body: DecideBody,
                 user: Dict[str, Any] = Depends(current_user)):
    return S.follow_event(event_id, user["actor"], user["role"],
                          deadline=body.deadline)


@app.post("/api/workflow/{object_type}/{object_id}/decide")
def decide(object_type: str, object_id: str, body: DecideBody,
           user: Dict[str, Any] = Depends(current_user)):
    return S.decide(object_type, object_id, body.action, user["actor"], user["role"],
                    note=body.note, deadline=body.deadline)


class AssignBody(BaseModel):
    owner: Optional[str] = None
    reviewer: Optional[str] = None
    deadline: Optional[str] = None


@app.post("/api/workflow/{object_type}/{object_id}/assign")
def assign(object_type: str, object_id: str, body: AssignBody,
           user: Dict[str, Any] = Depends(current_user)):
    return S.assign(object_type, object_id, body.owner, body.reviewer,
                    body.deadline, user["actor"], user["role"])


class AssetBody(BaseModel):
    kinds: Optional[List[str]] = None


@app.get("/api/growth-creatives")
def get_growth_creatives(user: Dict[str, Any] = Depends(current_user)):
    """结构化增长创意（热点→创意产出）+ 外部热点形成情况。

    ★ 路径不叫 /api/creatives —— 那个前缀已被 L6 的创意工作流占用
      （/api/creatives/{idea_id}/assets 等），避免路由冲突。
    """
    return S.growth_creatives()


@app.post("/api/creatives/{idea_id}/assets")
def gen_assets(idea_id: str, body: AssetBody,
               user: Dict[str, Any] = Depends(current_user)):
    return S.generate_assets(idea_id, body.kinds, user["actor"], user["role"])


@app.post("/api/assets/{asset_id}/approve")
def approve_asset(asset_id: str, user: Dict[str, Any] = Depends(current_user)):
    return S.approve_asset(asset_id, user["actor"], user["role"])


@app.post("/api/assets/{asset_id}/publish")
def publish_asset(asset_id: str, user: Dict[str, Any] = Depends(current_user)):
    return S.publish_asset(asset_id, user["actor"], user["role"])


class PlanBody(BaseModel):
    channels: List[str] = ["community_feed"]
    audience: str = "active_users"
    start_at: str = ""
    end_at: str = ""
    experiment: bool = False


@app.post("/api/creatives/{idea_id}/plan")
def create_plan(idea_id: str, body: PlanBody,
                user: Dict[str, Any] = Depends(current_user)):
    return S.create_plan(idea_id, body.channels, body.audience, body.start_at,
                         body.end_at, body.experiment, user["actor"], user["role"])


class PlanActionBody(BaseModel):
    action: str = "launch"
    reason: str = ""


@app.post("/api/plans/{plan_id}/transition")
def plan_transition(plan_id: str, body: PlanActionBody,
                    user: Dict[str, Any] = Depends(current_user)):
    return S.plan_transition(plan_id, body.action, user["actor"], user["role"],
                             reason=body.reason)


class ExperimentBody(BaseModel):
    plan_id: Optional[str] = None
    audience: str = "active_users"


@app.post("/api/creatives/{idea_id}/experiment")
def create_experiment(idea_id: str, body: ExperimentBody,
                      user: Dict[str, Any] = Depends(current_user)):
    return S.create_experiment(idea_id, body.plan_id, body.audience,
                               user["actor"], user["role"])


class ObserveBody(BaseModel):
    metric_name: str
    metric_class: str
    baseline_value: Optional[float] = None
    treatment_value: Optional[float] = None
    x_t: Optional[int] = None
    n_t: Optional[int] = None
    x_c: Optional[int] = None
    n_c: Optional[int] = None


@app.post("/api/experiments/{experiment_id}/observe")
def observe(experiment_id: str, body: ObserveBody,
            user: Dict[str, Any] = Depends(current_user)):
    return S.observe_experiment(experiment_id, body.metric_name, body.metric_class,
                                body.model_dump(), user["actor"], user["role"])


@app.post("/api/experiments/{experiment_id}/finish")
def finish_experiment(experiment_id: str,
                      user: Dict[str, Any] = Depends(current_user)):
    return S.finish_experiment(experiment_id, user["actor"], user["role"])


@app.post("/api/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, user: Dict[str, Any] = Depends(current_user)):
    return S.ack_alert(alert_id, user["actor"], user["role"])


# ---------------------------------------------------------------- 命令面板 + 设置

class CommandBody(BaseModel):
    input: str
    selected: Optional[Dict[str, str]] = None


@app.post("/api/command")
def command(body: CommandBody, user: Dict[str, Any] = Depends(current_user)):
    """斜杠命令入口（coding-agent 交互）。权限在 services/L6 层强制。"""
    import webapp.commands as C
    out = C.run(body.input, user, body.selected)
    if out.get("ok"):
        S.broadcast(user["actor"], body.input.split()[0].lstrip("/"))
    return out


@app.get("/api/ops-context")
def get_ops_context(user: Dict[str, Any] = Depends(current_user)):
    from execution import ops_context
    return ops_context.load()


@app.post("/api/ops-context")
def set_ops_context(body: Dict[str, Any],
                    user: Dict[str, Any] = Depends(current_user)):
    """运营约束写入（§39）。只有 admin 能改（require_role → 403）。"""
    from execution import ops_context
    from execution.audit import require_role
    require_role(user["role"], "configure")
    ops_context.save(
        resources=body.get("resources") or {"design": 1, "dev": 0, "ops": 3},
        slots_24h=body.get("slots_24h") or {"push": 2, "homepage_banner": 1},
        budget_level=body.get("budget_level", "low"),
        channels_allowed=body.get("channels_allowed") or ["taptap_inhouse"],
        ttl_hours=float(body.get("ttl_hours", 24)),
        notes=body.get("notes", ""))
    S.broadcast(user["actor"], "ops_context")
    return ops_context.load()


# ---------------------------------------------------------------- 异常 → HTTP

@app.exception_handler(PermissionError)
def permission_denied_handler(_req, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(KeyError)
def not_found_handler(_req, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})


@app.exception_handler(ValueError)
def bad_request_handler(_req, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------- WebSocket 变更广播

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    S.register_client(ws)
    loop = asyncio.get_running_loop()
    ws._loop_ref = loop            # 广播从工作线程安全投递回事件循环
    try:
        while True:
            await ws.receive_text()          # 保活；前端不发内容
    except WebSocketDisconnect:
        pass
    finally:
        S.unregister_client(ws)


# ---------------------------------------------------------------- 静态前端

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")


def main() -> int:
    import uvicorn
    port = int(os.environ.get("WEBAPP_PORT", "8200"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
