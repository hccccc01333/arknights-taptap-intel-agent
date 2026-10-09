from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from . import service
from .engine import TASK
from fastapi.responses import FileResponse


class CycleBody(BaseModel):
    research: bool = True
    live: bool = True
    task: str = TASK
    topic_id: str | None = None


def router(current_user):
    result=APIRouter(prefix="/api/v3")
    def operator(user):
        if user.get("role") not in ("admin","operator"):raise HTTPException(403,"需要运营或管理员角色")

    @result.get("/overview")
    def overview(user=Depends(current_user)):return service.overview()

    @result.get('/models')
    def models(user=Depends(current_user)):
        operator(user)
        from .store import Store
        from .providers import listing
        store=Store()
        try:return listing(store)
        finally:store.close()

    @result.post("/cycles")
    def cycle(body:CycleBody,user=Depends(current_user)):
        operator(user)
        try:return service.start_cycle(**body.model_dump())
        except ValueError as error:raise HTTPException(409,str(error)) from error

    @result.get("/runs/{run_id}")
    def run(run_id:str,user=Depends(current_user)):
        value=service.read("run",run_id)
        if value is None:raise HTTPException(404,"运行不存在")
        return value

    @result.get("/topics/{topic_id}")
    def topic(topic_id:str,user=Depends(current_user)):
        try:return service.read("topic",topic_id)
        except ValueError as error:raise HTTPException(404,str(error)) from error

    @result.get('/topics')
    def topics(domain:str='',user=Depends(current_user)):
        if domain not in ('','综合','社会','娱乐','文化','生活方式','游戏'):
            raise HTTPException(400,'未知发现领域')
        return service.read('topics',domain=domain)

    @result.get('/graph/context')
    def graph_context(topic_id:str='',query:str='',mode:str='local',user=Depends(current_user)):
        if mode not in ('local','global') or len(query)>100 or len(topic_id)>100:raise HTTPException(400,'图检索参数无效')
        try:return service.read('graph_'+mode,topic_id or None,query=query)
        except ValueError as error:raise HTTPException(400,str(error)) from error

    @result.get('/research-captures/{capture_id}/{frame}')
    def research_image(capture_id:str,frame:int,user=Depends(current_user)):
        from .browser_tools import ARTIFACT_ROOT
        from .store import Store
        import re,json
        if not re.fullmatch(r'capture_[a-f0-9]{32}',capture_id) or frame not in range(3):raise HTTPException(404,'截图不存在')
        s=Store()
        try:row=s.conn.execute('SELECT payload FROM research_capture WHERE capture_id=?',(capture_id,)).fetchone()
        finally:s.close()
        if not row or frame>=len(json.loads(row[0]).get('captures',[])):raise HTTPException(404,'截图不存在')
        path=ARTIFACT_ROOT/capture_id/('frame-'+str(frame)+'.png')
        if not path.is_file():raise HTTPException(404,'截图文件未保留')
        return FileResponse(path,media_type='image/png',headers={'Cache-Control':'private, max-age=3600'})

    @result.get("/events/{event_id}")
    def event(event_id:str,user=Depends(current_user)):
        value=service.read("event",event_id)
        if value is None:raise HTTPException(404,"事件不存在")
        return value

    @result.get('/tracked-events/{tracked_id}')
    def tracked_event(tracked_id:str,user=Depends(current_user)):
        try:return service.read('tracked_event',tracked_id)
        except ValueError as error:raise HTTPException(404,str(error)) from error

    @result.get('/creative-sources/{creative_id}')
    def creative_sources(creative_id:str,user=Depends(current_user)):
        value=service.read('creative_event',creative_id)
        if value is None:raise HTTPException(404,'创意依据不存在')
        return value

    @result.get('/sources')
    def sources(ids:str='',user=Depends(current_user)):
        import re
        values=list(dict.fromkeys(ids.split(','))) if ids else []
        if len(values)>20 or any(not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',v) for v in values):
            raise HTTPException(400,'一次最多核查20条有效来源')
        return service.read('sources',values)

    @result.get('/event-relations/{pair_id}')
    def relation(pair_id:str,user=Depends(current_user)):
        try:return service.read('relation',pair_id)
        except ValueError as error:raise HTTPException(404,str(error)) from error

    @result.post('/event-relations/{pair_id}/{action}')
    def decide_relation(pair_id:str,action:str,body:dict[str,Any],user=Depends(current_user)):
        operator(user)
        if action not in ('decide','withdraw'):raise HTTPException(400,'未知关系操作')
        try:return service.relation_decision(pair_id,body,user['actor'],withdraw=action=='withdraw')
        except ValueError as error:raise HTTPException(409,str(error)) from error

    @result.get("/materials")
    def materials(q:str="",user=Depends(current_user)):return service.read("materials",query=q[:100])

    @result.get("/source-materials")
    def source_materials(q:str="",user=Depends(current_user)):return service.read("source_materials",query=q[:100])

    @result.get("/work")
    def work(user=Depends(current_user)):return service.read("work")

    @result.get("/brief")
    def brief(days:int=7,user=Depends(current_user)):
        try:return service.read("brief",days=days)
        except ValueError as error:raise HTTPException(400,str(error)) from error

    @result.post("/settings/{kind}")
    def settings(kind:str,body:dict[str,Any],user=Depends(current_user)):
        operator(user)
        try:return service.configure(kind,body.get("model") if kind=="model" else body)
        except ValueError as error:raise HTTPException(400,str(error)) from error

    @result.post("/feedback")
    def feedback(body:dict[str,Any],user=Depends(current_user)):
        operator(user)
        try:return service.feedback(body,user["actor"])
        except (ValueError,KeyError) as error:raise HTTPException(400,str(error)) from error

    return result
