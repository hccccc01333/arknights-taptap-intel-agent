from __future__ import annotations

import copy
import json

from agent_v2.tools import ResearchTools, DEFINITIONS, definition, STR, IDS
from agent_v2.store import dump, now_iso
from .discovery import queue, read_topic

ASSET_KINDS=("source_quote","expression_pattern","game_adaptation","copy","script","visual_brief","production_checklist")
ASSET_SCHEMA={"type":"object","properties":{
    "kind":{"type":"string","enum":list(ASSET_KINDS)},
    "origin":{"type":"string","enum":["source","inferred","original"]},
    "title":STR,"content":STR,"tags":{"type":"array","items":STR},
    "evidence_ids":IDS,"rights_status":STR,
},"required":["kind","origin","title","content","tags","evidence_ids","rights_status"]}

def definitions():
    result=copy.deepcopy(DEFINITIONS)
    next(d['function'] for d in result if d['function']['name']=='read_source')['description']='补读已经保存的来源：新闻正文片段、贴吧公开话题简介、B站视频简介；未读取评论或视频转录，保留实际读取范围与失败记录。'
    finish=next(d["function"]["parameters"] for d in result if d["function"]["name"]=="finish_research")
    assessment=finish["properties"]["assessments"]["items"]
    assessment["properties"].update({"topic_id":STR,"topic_fingerprint":STR,"new_event_reason":STR,
        "opportunity":{"type":"object","properties":{k:STR for k in ("audience_need","spread_hook","taptap_bridge","why_now","growth_goal")},
                       "required":["audience_need","spread_hook","taptap_bridge","why_now","growth_goal"]}})
    assessment["required"]+= ["topic_id","topic_fingerprint","opportunity"]
    creative=assessment["properties"]["creatives"]["items"]
    creative["properties"].update({"growth_goal":STR,"hook":STR,"distribution":STR,"resources":STR,
        "journey":{"type":"array","items":STR,"minItems":2},
        "deliverables":{"type":"array","items":ASSET_SCHEMA,"minItems":2,"maxItems":8},
        "reuse_material_ids":{"type":"array","items":STR},
        "reuse_source_asset_ids":{"type":"array","items":STR}})
    creative["required"]+= ["growth_goal","hook","distribution","resources","journey","deliverables","reuse_material_ids"]
    result+= [
        definition("discover_topics","读取跨领域待研究候选队列。先广泛发现，再选择与业务有关的机会。已处理且无变化的候选不会重复入队。",{"limit":{"type":"integer","minimum":1,"maximum":30}}),
        definition("read_topic","读取候选的真实内容、不同渠道观察、历史变化、已有分析缓存；最终提交应引用 topic_id 和当时 fingerprint。",{"topic_id":STR},["topic_id"]),
        definition("search_materials","检索已积累的表达、文案、脚本与视觉方案；借用素材时保留 material_id 和使用限制。",{"query":STR,"kind":STR}),
        definition("search_source_assets","检索持续采集的文本、视频/文章/封面引用及来源版本。媒体引用不等于已下载文件；复用时保留 asset_id。",{"query":STR,"kind":STR}),
        definition("record_insight","将已读原文的情绪、需求、传播方式及表达模式存为带内容版本的分析缓存。均为待核查推断，事实须逐字引用。",{
            "evidence_id":STR,"content_hash":STR,"summary":STR,
            "quotes":{"type":"array","items":STR,"minItems":1},
            "emotions":{"type":"array","items":STR},"needs":{"type":"array","items":STR},
            "spread_mechanics":{"type":"array","items":STR},"patterns":{"type":"array","items":ASSET_SCHEMA},
            "tags":{"type":"array","items":STR},"unknowns":{"type":"array","items":STR},
        },["evidence_id","content_hash","summary","quotes","emotions","needs","spread_mechanics","patterns","tags","unknowns"]),
    ]
    return result


class GrowthTools(ResearchTools):
    definitions=definitions()

    def __init__(self,store,network_budget=3):
        super().__init__(store,network_budget)
        self.topics={};self.read_materials=set();self.read_source_assets=set();self.run_id=None

    def call(self,name,args):
        if name not in {d['function']['name'] for d in self.definitions}:raise ValueError('工具未注册')
        from .tool_executor import ToolExecutor
        if not getattr(self,'_executor',None):
            self._executor=ToolExecutor(self.store,self.run_id,limit=self.network_budget)
        network=name in ('read_source','search_sources')
        def execute(units):
            if network:self.network_budget=min(self.network_budget,units)
            return self._call_impl(name,args)
        return self._executor.invoke(name,args,execute,maximum=min(1,self.network_budget) if network else 0)

    def _call_impl(self,name,args):
        if not isinstance(args,dict):raise ValueError("工具参数必须是对象")
        if name=='read_source':
            if self.network_budget<=0:return {'status':'budget_exhausted','note':'本轮来源读取预算用完'}
            eid=str(args.get('evidence_id') or '')
            if not self.store.evidence([eid]):raise ValueError('来源不存在')
            from .enrichment import prepare
            row=self.store.conn.execute('SELECT topic_id FROM topic_member WHERE evidence_id=? AND active=1 LIMIT 1',(eid,)).fetchone()
            if row and self.store.evidence([eid])[0]['platform'] in ('chinanews','tieba','gamemedia','bilibili'):
                self.network_budget-=1
                # The reader accepts a selected evidence ID, so a multi-source topic
                # cannot spend the tool budget reading a different source.
                result=prepare(self.store,topic_id=row[0],evidence_id=eid,max_calls=1)
                self.read_ids.add(eid)
                return {**result,'status':result['results'][0]['status'] if result['results'] else 'skipped',
                        'evidence':self.store.evidence([eid]),'note':'返回来源实际读取范围，不包含未读取的评论。'}
            return super().call(name,args)
        if name=="compare_observations":
            result=super().call(name,args)
            for row in result["comparisons"]:
                row["metrics"].pop("rank",None);row["metrics"].pop("hot_score",None)
            result["note"]+=" V3 榜单指标仅按 channel_observation 保存的同渠道观察比较；迁移数据中的未分渠道榜单指标不参与这里的计算。"
            return result
        if name=="discover_topics":
            return {"topics":queue(self.store,args.get("limit",24)),"coverage_note":"已接入的渠道覆盖；候选优先级只是观察信号，不是增长价值评分。"}
        if name=="read_topic":
            result=read_topic(self.store,str(args.get("topic_id") or ""))
            from .runtime_guard import basis
            snapshot=basis(self.store,result['topic_id'],result['fingerprint'],packet={
                'evidence':result['evidence']+result.get('discussion_samples',[])+result.get('research_sources',[])})
            previous=getattr(self.store,'_delivery_basis',None)
            if previous:
                snapshot['topics']={**previous['topics'],**snapshot['topics']}
                snapshot['sources']={**previous['sources'],**snapshot['sources']}
            self.store._delivery_basis=snapshot
            self.topics[result["topic_id"]]=result
            self.read_ids.update(e["evidence_id"] for e in result["evidence"])
            self.read_ids.update(e['evidence_id'] for e in result.get('discussion_samples',[])+result.get('research_sources',[]))
            self.read_source_assets.update(a["asset_id"] for a in result["source_assets"])
            if result.get("event_id"):
                event=self.store.get_event(result["event_id"])
                if event:
                    result["existing_event"]=event;self.read_events.add(event["event_id"])
                    self.read_ids.update(e["evidence_id"] for e in event["evidence"])
            return result
        if name=="search_materials":
            materials=self.store.usable_materials(str(args.get("query") or ""),20,kind=str(args.get("kind") or ""))
            self.read_materials.update(m["material_id"] for m in materials)
            return {"materials":materials,"note":"来源素材与原创草案分别标注，原创草案仍需制作审核。"}
        if name=="search_source_assets":
            assets=self.store.source_assets(str(args.get("query") or ""),str(args.get("kind") or ""),20)
            self.read_source_assets.update(a["asset_id"] for a in assets)
            return {"source_assets":assets,"note":"素材的来源与读取范围已标注；引用不等于原文件或商业授权。"}
        if name=="record_insight":
            eid=args.get("evidence_id")
            if eid not in self.read_ids:raise ValueError("先读取对应原文")
            source=self.store.evidence([eid])[0]
            if args.get("content_hash")!=source["content_hash"]:raise ValueError("内容版本变化，请重新读取")
            if not isinstance(args.get("summary"),str) or not args["summary"].strip():raise ValueError("分析需要具体摘要")
            quotes=args.get("quotes")
            if not isinstance(quotes,list) or not quotes:raise ValueError("分析需要原文引文")
            for quote in quotes:self.quote(quote,source)
            for key in ("emotions","needs","spread_mechanics","tags","unknowns"):
                if not isinstance(args.get(key),list) or any(not isinstance(s,str) or not s.strip() for s in args[key]):raise ValueError(key+" 必须为文本列表")
            if not isinstance(args.get("patterns"),list) or len(args["patterns"])>6:raise ValueError("patterns 须为最多六条素材列表")
            for pattern in args["patterns"]:self.validate_asset(pattern,{eid})
            existing=self.store.insight(eid,source["content_hash"])
            if existing:return {"status":"cached","insight":existing}
            with self.store.conn:
                self.store.conn.execute("INSERT INTO insight VALUES(?,?,?,?,?,?)",(eid,source["content_hash"],"insight-v3.1",now_iso(),self.run_id,dump({**args,"status":"ai_inference_unreviewed"})))
                for pattern in args["patterns"]:self.store.save_material(pattern,self.run_id)
            return {"status":"saved","content_hash":source["content_hash"]}
        return super().call(name,args)

    @staticmethod
    def quote(text,source):
        if not isinstance(text,str) or len(text.strip())<4 or text.strip() not in source["title"]+"\n"+source["body"]:
            raise ValueError("引文没有出现在对应原文中")

    def validate_asset(self,asset,allowed_ids):
        if not isinstance(asset,dict) or asset.get("kind") not in ASSET_KINDS or asset.get("origin") not in ("source","inferred","original"):
            raise ValueError("素材需标明种类和来源/推断/原创")
        for key in ("title","content","rights_status"):
            if not isinstance(asset.get(key),str) or not asset[key].strip() or len(asset[key])>6000:raise ValueError("素材缺少具体内容或使用条件")
        ids=asset.get("evidence_ids")
        if not isinstance(ids,list) or not ids or not all(isinstance(e,str) and e in allowed_ids for e in ids):raise ValueError("素材必须关联已读来源；原创素材的关联表示创作依据，不代表来源授权")
        if not isinstance(asset.get("tags"),list) or any(not isinstance(s,str) or not s.strip() for s in asset["tags"]):raise ValueError("素材标签须为文本列表")
        if asset["kind"]=="source_quote":
            if asset["origin"]!="source":raise ValueError("摘录必须标为来源素材")
            sources=self.store.evidence(ids)
            if not any(asset["content"].strip() in s["title"]+"\n"+s["body"] for s in sources):raise ValueError("来源摘录与原文不符")
        elif asset["origin"]=="source":raise ValueError("来源素材目前仅支持逐字摘录，制作内容需标为原创或推断")

    def validate(self,result):
        assessments=super().validate(result)
        if getattr(self,"assigned_topic",None) and (len(assessments)!=1 or assessments[0].get("topic_id")!=self.assigned_topic):
            raise ValueError("当前创意任务只接受分配话题的交付")
        for item in assessments:
            tid=item.get("topic_id");topic=self.topics.get(tid)
            if not topic or item.get("topic_fingerprint")!=topic["fingerprint"]:raise ValueError("交付前必须读取候选话题，并保留该版本 fingerprint")
            if topic.get("event_id") and not item.get("event_id"):
                reason=item.get("new_event_reason")
                if not isinstance(reason,str) or len(reason.strip())<12:raise ValueError("已有话题再次出现时，更新原事件；若属新的发生，需要 new_event_reason 说明对象、时间或行为差异")
            member_ids={e["evidence_id"] for e in topic["evidence"]}
            if not member_ids.intersection(item["evidence_ids"]):raise ValueError("事件必须包含所选话题的实际依据")
            opportunity=item.get("opportunity")
            if not isinstance(opportunity,dict) or any(not isinstance(opportunity.get(k),str) or not opportunity[k].strip() for k in ("audience_need","spread_hook","taptap_bridge","why_now","growth_goal")):
                raise ValueError("机会判断需解释需求、传播吸引点、TapTap 关联、时机和增长目标；不值得做也要说明")
            for c in item["creatives"]:
                for key in ("growth_goal","hook","distribution","resources"):
                    if not isinstance(c.get(key),str) or not c[key].strip():raise ValueError("创意需要增长目标、吸引点、传播入口和资源条件")
                if not isinstance(c.get("journey"),list) or len(c["journey"])<2 or any(not isinstance(s,str) or not s.strip() for s in c["journey"]):raise ValueError("需给出传播到 TapTap 承接的用户路径")
                delivery=c.get("deliverables")
                if not isinstance(delivery,list) or not 2<=len(delivery)<=8:raise ValueError("需交付 2 至 8 项可制作素材")
                for asset in delivery:self.validate_asset(asset,set(item["evidence_ids"]))
                kinds={a["kind"] for a in delivery}
                if "copy" not in kinds or not kinds.intersection({"script","visual_brief"}):raise ValueError("每条创意至少有具体文案，以及脚本或视觉制作方案")
                mids=c.get("reuse_material_ids")
                if not isinstance(mids,list) or any(not isinstance(m,str) or m not in self.read_materials for m in mids):raise ValueError("复用素材必须先检索读取，未复用填空列表")
                sids=c.get("reuse_source_asset_ids",[])
                if not isinstance(sids,list) or any(not isinstance(s,str) or s not in self.read_source_assets for s in sids):raise ValueError("复用来源素材必须先读取，不能虚构引用")
        return assessments

    def delivery_context(self):
        return {"topics_read":[{"topic_id":t["topic_id"],"fingerprint":t["fingerprint"],"title":t["title"],"event_id":t.get("event_id"),"intelligence":t.get("intelligence")} for t in self.topics.values()],"material_ids_read":sorted(self.read_materials),"source_asset_ids_read":sorted(self.read_source_assets)}
