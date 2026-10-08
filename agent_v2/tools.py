from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .ingest import normalize
from .store import Store, ROOT, now_iso


def definition(name: str, description: str, properties: dict[str, Any], required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(required), "additionalProperties": False}}}


STR = {"type": "string"}
IDS = {"type": "array", "items": {"type":"string","pattern":"^evidence_[a-f0-9]{20}$"}, "minItems": 1, "maxItems": 12}
DEFINITIONS = [
    definition("list_candidates", "列出近期真实候选；榜单及搜索命中只是线索。默认均衡显示多个平台。", {
        "limit": {"type": "integer", "minimum": 1, "maximum": 40},
        "hours": {"type": "integer", "minimum": 1, "maximum": 168},
        "platform": STR,
    }),
    definition("search_evidence", "按关键词在真实证据库搜索标题、摘要与话题语境，包含非游戏热点。", {"query": STR, "limit": {"type": "integer"}}, ["query"]),
    definition("read_evidence", "读取候选原文或实际摘要、来源链接、发布时间及互动历史。最终判断只能引用实际读过的证据。", {"evidence_ids": IDS}, ["evidence_ids"]),
    definition("search_sources", "用已有 B 站连接器在线补采关键词结果。空结果或风控不证明没有讨论。每轮网络工具有上限。", {"query": STR}, ["query"]),
    definition("read_source", "从已保存来源读取真实详情或正文。支持 B站视频简介与互动计数、游戏媒体正文；视频简介不是视频转录，评论尚未读取。", {"evidence_id": STR}, ["evidence_id"]),
    definition("compare_observations", "计算同一内容同一指标的实际历史变化。没有可比历史时返回不足，不推测加速。", {"evidence_ids": IDS, "hours": {"type":"integer","minimum":1,"maximum":168}}, ["evidence_ids"]),
    definition("read_feedback", "读取运营对已有创意的采用、调整或拒绝原因，改进下一轮提案；采用不等于已执行。", {}),
    definition("list_events", "查看已经保存的事件，避免重复研究；需要更新时继续 read_event。", {}),
    definition("read_event", "查看事件原文与历史判断；更新时使用它的稳定 event_id。", {"event_id": STR}, ["event_id"]),
    definition("finish_research", "提交最终研究。新事件省略 event_id，不得把 evidence_id 写入 event_id。引用保留完整 evidence_ 前缀。事实需要直接原文引用；创意只为 related 事件生成，不编指标。", {
        "summary": STR,
        "assessments": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "properties": {
                "event_id": STR, "title": STR,
                "verdict": {"type": "string", "enum": ["related", "adjacent", "irrelevant"]},
                "evidence_ids": IDS, "summary": STR,
                "facts": {"type": "array", "items": {"type": "object", "properties": {"quote": STR, "evidence_id": STR}, "required": ["quote", "evidence_id"]}},
                "inferences": {"type": "array", "items": STR},
                "unknowns": {"type": "array", "items": STR},
                "taptap_fit": {"type": "object", "properties": {"audience": STR, "motivation": STR, "reason": STR}, "required": ["audience", "motivation", "reason"]},
                "creatives": {"type": "array", "maxItems": 2, "items": {"type": "object", "properties": {
                    "title": STR, "audience": STR, "placement": STR, "user_action": STR,
                    "steps": {"type": "array", "items": STR}, "copy": STR,
                    "materials": {"type": "array", "items": {"type": "object", "properties": {"description": STR, "evidence_id": STR, "rights_status": STR}, "required": ["description", "evidence_id", "rights_status"]}},
                    "timing": STR, "risks": {"type": "array", "items": STR}, "measurement": STR,
                }, "required": ["title", "audience", "placement", "user_action", "steps", "copy", "materials", "timing", "risks", "measurement"]}},
            }, "required": ["title", "verdict", "evidence_ids", "summary", "facts", "inferences", "unknowns", "taptap_fit", "creatives"]}},
    }, ["summary", "assessments"]),
]


class ResearchTools:
    def __init__(self, store: Store, network_budget=2):
        self.store = store
        self.read_ids: set[str] = set()
        self.read_events: set[str] = set()
        self.network_budget = network_budget

    def call(self, name: str, args: dict[str, Any]):
        if not isinstance(args, dict):
            raise ValueError("工具参数必须是对象")
        if name == "list_candidates":
            limit = max(1, min(40, int(args.get("limit", 24))))
            hours = max(1, min(168, int(args.get("hours", 24))))
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
            platform = str(args.get("platform") or "")
            if platform:
                return {"candidates": self.store.candidates(limit, since, platform=platform)}
            groups = [self.store.candidates(max(1, limit // 6), since, platform=p)
                      for p in ("gamemedia", "bilibili", "weibo", "baidu", "tieba", "taptap")]
            return {"candidates": [item for group in groups for item in group][:limit],
                    "note": "按平台均衡抽样；这是候选，不是已确认热点或全网覆盖。"}
        if name == "search_evidence":
            query = str(args.get("query") or "").strip()[:100]
            if not query:
                raise ValueError("查询词不能为空")
            return {"candidates": self.store.candidates(args.get("limit", 20), query=query)}
        if name == "read_evidence":
            ids = args.get("evidence_ids")
            if not isinstance(ids, list) or not all(isinstance(e, str) for e in ids):
                raise ValueError("evidence_ids 必须是字符串列表")
            rows = self.store.evidence(ids)
            if len(rows) != len(set(ids)):
                missing=set(ids)-{e["evidence_id"] for e in rows}
                raise ValueError("证据 ID 不存在或过多："+", ".join(sorted(missing))+"。必须保留完整 evidence_ 前缀；从检索结果复制实际 ID。")
            self.read_ids.update(e["evidence_id"] for e in rows)
            return {"evidence": rows, "note": "body 是实采正文或摘要；空正文不能当作已读全文。"}
        if name == "list_events":
            return {"events": self.store.events(20)}
        if name == "read_event":
            event_id = str(args.get("event_id") or "")
            event = self.store.get_event(event_id)
            if event is None:
                raise ValueError("事件不存在")
            self.read_events.add(event_id)
            self.read_ids.update(e["evidence_id"] for e in event["evidence"])
            return event
        if name == "search_sources":
            if self.network_budget <= 0:
                raise ValueError("本轮在线补采预算已用完")
            query = str(args.get("query") or "").strip()[:80]
            if not query:
                raise ValueError("查询词不能为空")
            self.network_budget -= 1
            from .connectors import collect
            return collect(self.store, "bilibili", query)
        if name == "read_source":
            if self.network_budget <= 0:
                raise ValueError("本轮在线补采预算已用完")
            self.network_budget -= 1
            from .connectors import read_source
            try:
                output = read_source(self.store, str(args.get("evidence_id") or ""))
            except Exception as error:
                return {"status":"unavailable", "error":type(error).__name__, "note":"详情读取未成功，不能当作已读原文。"}
            self.read_ids.update(e["evidence_id"] for e in output.get("evidence", []))
            return output
        if name == "compare_observations":
            ids = args.get("evidence_ids")
            if not isinstance(ids,list) or not all(isinstance(e,str) and e in self.read_ids for e in ids):
                raise ValueError("对比前必须读取相关证据")
            from .tracking import compare_observations
            return compare_observations(self.store, ids, args.get("hours",24))
        if name == "read_feedback":
            return {"feedback":self.store.overview()["feedback"][:20]}
        raise ValueError(f"不支持的工具：{name}")

    def validate(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(result,dict):
            raise ValueError("最终交付参数必须是对象")
        assessments = result.get("assessments")
        if not isinstance(result.get("summary"),str) or not result["summary"].strip() or len(result["summary"])>4000 or not isinstance(assessments, list) or not 1 <= len(assessments) <= 3:
            raise ValueError("最终研究须有 summary 及 1 至 3 条事件判断")
        for item in assessments:
            if not isinstance(item, dict):
                raise ValueError("事件判断必须是对象")
            ids = item.get("evidence_ids")
            if not isinstance(ids, list) or not 1<=len(ids)<=12 or not all(isinstance(e, str) and e in self.read_ids for e in ids):
                raise ValueError("引用了未实际读取或不存在的证据")
            if any(not isinstance(item.get(k),str) or not item[k].strip() or len(item[k])>4000 for k in ("title","summary")):
                raise ValueError("事件需要具体标题和判断说明")
            if item.get("verdict") not in ("related", "adjacent", "irrelevant"):
                raise ValueError("关联判断无效")
            if item.get("event_id") and item["event_id"] not in self.read_events:
                raise ValueError("更新已有事件前必须读取其历史")
            evidence = {e["evidence_id"]: e for e in self.store.evidence(ids)}
            facts = item.get("facts")
            if not isinstance(facts, list) or not 1<=len(facts)<=20:
                raise ValueError("需要至少一条直接原文引用作为事实依据")
            for fact in facts:
                if not isinstance(fact, dict) or fact.get("evidence_id") not in evidence:
                    raise ValueError("事实引用的证据不属于本事件")
                source = evidence[fact["evidence_id"]]
                if not isinstance(fact.get("quote"),str):
                    raise ValueError("事实引文必须是原文字符串")
                quote = fact["quote"].strip()
                if len(quote) < 4 or quote not in source["title"] + "\n" + source["body"]:
                    raise ValueError("事实引文没有出现在真实标题或正文中")
            fit = item.get("taptap_fit")
            if not isinstance(fit, dict) or any(not isinstance(fit.get(k),str) or not fit[k].strip() for k in ("audience","motivation","reason")):
                raise ValueError("需说明 TapTap 关联或不关联的依据")
            for field in ("inferences", "unknowns", "creatives"):
                if not isinstance(item.get(field), list):
                    raise ValueError(f"{field} 必须是列表")
            if any(not isinstance(s,str) or not s.strip() for s in item["inferences"]+item["unknowns"]):
                raise ValueError("推断和未知须为具体文本")
            if len(item["creatives"]) > 2:
                raise ValueError("每个事件最多两条创意")
            if item["creatives"] and item["verdict"] != "related":
                raise ValueError("非 related 判断不能附行动创意")
            for creative in item["creatives"]:
                if not isinstance(creative, dict) or any(not isinstance(creative.get(k),str) or not creative[k].strip() or len(creative[k])>4000 for k in ("title", "audience", "placement", "user_action", "copy", "timing", "measurement")):
                    raise ValueError("创意缺少人群、位置、动作、文案、时效或验证方法")
                steps = creative.get("steps")
                if not isinstance(steps, list) or not steps or not all(isinstance(s, str) and s.strip() for s in steps):
                    raise ValueError("创意需要具体执行步骤")
                if not isinstance(creative.get("materials"), list) or not creative["materials"] or not isinstance(creative.get("risks"), list) or not all(isinstance(s,str) for s in creative["risks"]):
                    raise ValueError("素材与风险必须明确列出")
                for material in creative["materials"]:
                    if not isinstance(material, dict) or material.get("evidence_id") not in evidence:
                        raise ValueError("素材必须引用本事件实际证据")
                    if any(not isinstance(material.get(k),str) or not material[k].strip() for k in ("rights_status","description")):
                        raise ValueError("素材使用状态需明确，不能假定已授权")
        return assessments
