from agent_v2.reports import brief as evidence_brief
from datetime import datetime, timedelta, timezone
from .model import gate


def brief(store,days=7):
    result=evidence_brief(store,days)
    result["execution_breakdown"]={"interactive_assisted":0,"autonomous":0}
    extras=["\n## 增长创意与制作交付\n"]
    for action in result.get("actions",[]):
        c=action["proposal"]
        if action["decision"]=="rejected":continue
        mode=c.get("execution_mode","autonomous")
        result["execution_breakdown"][mode]=result["execution_breakdown"].get(mode,0)+1
        if not c.get("deliverables"):continue
        extras.extend(["### "+c["title"],"执行方式："+("交互辅助验收；后台自动研究另行验收" if mode=="interactive_assisted" else "后台研究运行器"),"增长目标："+c["growth_goal"],"吸引点："+c["hook"],"传播入口："+c["distribution"],"用户路径："+" → ".join(c["journey"]),"资源条件："+c["resources"]])
        if c.get('growth_hypothesis'):
            extras.extend(['增长假设（尚未验证）：'+c['growth_hypothesis'],'小规模验证：'+c['validation_plan'],
                           '上线前条件：'+'；'.join(c.get('prerequisites',[]))])
        for asset in c["deliverables"]:
            extras.extend(["#### "+asset["title"]+"（"+asset["kind"]+" / "+asset["origin"]+"）",asset["content"],"使用条件："+asset["rights_status"]])
    result["markdown"] += "\n\n".join(extras)
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec="seconds")
    ceiling=datetime.now(timezone.utc).isoformat(timespec="seconds")
    records=[i for i in store.intelligence_feed(100) if cutoff<=i["created_at"]<=ceiling and i["fingerprint"]==i["current_fingerprint"]]
    result["intelligence"]=records
    result["model_gate"]=gate(store)
    intelligence_lines=["\n## 情报与素材系统的持续积累\n"]
    if records:
        for record in records[:12]:
            p=record["payload"]
            safety=store.event_risk({'topic_id':record['topic_id'],'topic_fingerprint':record['fingerprint']})
            intelligence_lines.extend(["### "+record["title"],p["summary"],
                "需求线索（分析推断）："+"；".join(p["needs"]),
                "传播方式（分析推断）："+"；".join(p["spread_mechanics"]),
                "可复用角度："+("；".join(p["reusable_angles"]) if safety['growth_allowed'] else '仅内部研究，不用于传播'),
                "机会判断："+(p["opportunity"]["decision"]+" / "+p["opportunity"]["reason"] if safety['growth_allowed'] else '停止推广创意 / '+safety['gate_reason']),
                "尚待核查："+"；".join(p["unknowns"])])
            if safety['growth_allowed'] and p['opportunity'].get('hypothesis'):
                intelligence_lines.extend(['增长假设（尚未验证）：'+p['opportunity']['hypothesis'],
                    '验证方法：'+p['opportunity']['validation_plan'],'待确认条件：'+'；'.join(p['opportunity'].get('prerequisites',[]))])
    else:intelligence_lines.append("本期尚无后台独立 AI 情报分析，不能据已采集的标题推断用户情绪或增长机会。")
    if result["model_gate"]["status"]=="deferred":
        intelligence_lines.append("AI 等待重试："+result["model_gate"]["reason"]+"。来源采集与素材整理继续运行；退避时间不保证供应商额度恢复。")
    inventory=[dict(r) for r in store.conn.execute("SELECT kind,COUNT(*) count FROM source_asset WHERE last_seen_at>=? AND last_seen_at<=? GROUP BY kind",(cutoff,ceiling))]
    result["source_inventory"]=inventory
    intelligence_lines.append("本期被观察的来源素材记录："+"；".join(f"{r['kind']} {r['count']}" for r in inventory)+"。媒体引用尚未下载或转录，使用条件待核查。")
    result["markdown"]+="\n\n".join(intelligence_lines)
    drafts=[d for d in store.growth_drafts(30) if d['fingerprint']==d['current_fingerprint']]
    result['production_pending']=drafts
    if drafts:
        result['markdown']+='\n\n## 已规划、待完成制作的创意\n\n'+'\n\n'.join(
            '### '+d['payload']['title']+'\n\n'+d['payload']['hook']+'\n\n制作内容尚未完成，不计为完整增长创意交付。' for d in drafts)
    result["materials_note"]="包含实采文本与媒体引用、分析表达模式和原创制作草案；媒体原文件和图像/视频制作另行验收。"
    return result
