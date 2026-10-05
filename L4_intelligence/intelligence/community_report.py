#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L4 Community Report Generator —— 把 L3 社区检测结果转成运营可读的结构化报告。

★ 这是系统从"聚类结果展示"升级为"Community Intelligence"的关键节点。
  运营看到的不是 cluster_id，而是一份有叙事、有证据、有建议的可执行报告。

★ 输入：L3 社区检测结果（meta 表的 community_data）+ L2 代表性内容（真实标题）
★ 输出：每个社区一份 CommunityReport，存 JSON 供 L6/前端消费。

★ 纪律（给"实用人员"看的报告的三条底线）：
  1. 话题标题用代表性内容的真实标题 —— "arknights/上线"这类实体碎片不是信息；
  2. 热度给档位词（爆/热/温/冷）—— 裸小数没人看得懂；
  3. 建议必须点名具体话题 —— 模板套话等于没说。
  LLM 后续可替换叙事部分（接口预留，不阻塞）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

_L4 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # <root>/L4_intelligence
_ROOT = os.path.dirname(_L4)                                        # 项目根
for _p in (_ROOT, _L4):
    if _p not in sys.path:
        sys.path.insert(0, _p)

L3_DB = os.path.join(_ROOT, "data", "state", "l3_trend.sqlite3")
L2_DB = os.path.join(_ROOT, "data", "state", "l2_processed.sqlite3")

LIFE_ZH = {
    "EMERGING": "正在起势", "GROWING": "正在升温", "PEAKING": "到顶了",
    "REACTIVATED": "重新活跃", "DECLINING": "已经降温", "DORMANT": "基本平静",
}

# 热度档位：给数字一个参照系
HEAT_TIERS = ((0.45, "爆"), (0.35, "热"), (0.22, "温"), (0.0, "冷"))
HEAT_KIND = {"爆": "hot", "热": "warm", "温": "info", "冷": "cool"}

# 实体标签中文名：games/*.json 优先，内置常见游戏兜底
_BUILTIN_GAME_CN = {
    "genshin": "原神", "zenless": "绝区零", "star rail": "星穹铁道",
    "star_rail": "星穹铁道", "delta_force": "三角洲行动",
    "black_myth_wukong": "黑神话悟空", "wuthering-waves": "鸣潮",
    "arknights": "明日方舟", "snowbreak": "尘白禁区", "endfield": "终末地",
}


def _load_game_names() -> Dict[str, str]:
    names = dict(_BUILTIN_GAME_CN)
    gdir = os.path.join(_ROOT, "games")
    if os.path.isdir(gdir):
        for fn in os.listdir(gdir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(gdir, fn), encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("key"):
                    names[d["key"]] = d.get("name") or d["key"]
            except (ValueError, OSError):
                continue
    return names


GAME_CN = _load_game_names()


def heat_label(v: Optional[float]) -> str:
    for th, name in HEAT_TIERS:
        if (v or 0) >= th:
            return name
    return "冷"


def _short(text: str, limit: int = 44) -> str:
    t = " ".join((text or "").split())
    return t[:limit] + ("…" if len(t) > limit else "")


def _cn_label(entity: str) -> str:
    lab = entity.split("_", 1)[1] if "_" in entity else entity
    return GAME_CN.get(lab, GAME_CN.get(lab.lower(), lab))


def load_communities(l3_db: Optional[str] = None) -> List[Dict[str, Any]]:
    db = l3_db or L3_DB
    if not os.path.exists(db):
        return []
    con = sqlite3.connect(db)
    row = con.execute("SELECT value FROM meta WHERE key='community_data'").fetchone()
    con.close()
    if not row:
        return []
    return json.loads(row[0])


def _parse_entities(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return []
    return []


def load_community_events(l3_db: Optional[str] = None):
    """返回 (社区列表, 全部活跃事件)。事件带 entities / 真实显示标题 / 主平台。"""
    db = l3_db or L3_DB
    if not os.path.exists(db):
        return [], []
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    events = []
    for r in con.execute(
            "SELECT event_id, canonical_title, lifecycle, hot_score, momentum_score, "
            "confidence_score, content_count, platform_count, platforms, entity_ids, "
            "representative_content_id FROM trend_event WHERE status='active' "
            "ORDER BY content_count DESC"):
        ev = dict(r)
        ev["entities"] = _parse_entities(ev.get("entity_ids"))
        ev["platforms_list"] = _parse_entities(ev.get("platforms"))
        events.append(ev)
    con.close()

    # 批量取代表性内容的真实标题（★ 标题信息量的来源）
    rep_ids = [e["representative_content_id"] for e in events if e.get("representative_content_id")]
    rep: Dict[str, Dict[str, str]] = {}
    if rep_ids and os.path.exists(L2_DB):
        con = sqlite3.connect(L2_DB)
        con.row_factory = sqlite3.Row
        q = ",".join("?" * len(set(rep_ids)))
        for r in con.execute(
                f"SELECT content_id, raw_title, normalized_text, platform FROM content "
                f"WHERE content_id IN ({q})", tuple(set(rep_ids))):
            title = (r["raw_title"] or "").strip() or (r["normalized_text"] or "").strip()
            rep[r["content_id"]] = {"title": title, "platform": r["platform"] or ""}
        con.close()

    for e in events:
        rc = rep.get(e.get("representative_content_id") or "")
        # ★ 显示标题：真实内容标题优先，实体碎片标题只做兜底
        e["display_title"] = _short(rc["title"]) if rc and rc["title"] else _short(e.get("canonical_title"))
        plat_list = e.get("platforms_list") or ([rc["platform"]] if rc and rc.get("platform") else [])
        e["main_platform"] = plat_list[0] if plat_list else ""

    comms = load_communities(db)
    return comms, events


def _headline(comm: Dict[str, Any], events: List[Dict[str, Any]]) -> str:
    """社群标题：按实体在本组事件里的覆盖率排序（字母序会让标题和内容对不上），
    再按前缀优先级破平（游戏 > 话题 > 厂商），游戏名中文化，去重。"""
    prio = {"game": 0, "event": 1, "developer": 2, "publisher": 3, "genre": 4}
    ents = set(comm.get("entities") or [])

    def coverage(e: str) -> tuple:
        cov = sum(1 for ev in events if e in set(ev["entities"]))
        return (-cov, prio.get(e.split("_", 1)[0], 9))

    labels, seen = [], set()
    for e in sorted(ents, key=coverage):
        lab = _cn_label(e)
        if not lab or lab.lower() in seen:
            continue
        seen.add(lab.lower())
        labels.append(lab)
        if len(labels) >= 3:
            break
    if labels:
        return " · ".join(labels)
    return events[0]["display_title"][:20] if events else "未命名社群"


def _event_row(e: Dict[str, Any]) -> Dict[str, Any]:
    lab = heat_label(e.get("hot_score"))
    return {"event_id": e["event_id"],
            "title": e["display_title"],
            "hot": e["hot_score"],
            "heat": lab,
            "heat_kind": HEAT_KIND[lab],
            "lifecycle": e["lifecycle"],
            "content_count": e.get("content_count") or 0,
            "platform": e.get("main_platform")}


def generate_report(comm: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    n_events = len(events)
    total_content = sum(e.get("content_count") or 0 for e in events)
    max_heat = max((e.get("hot_score") or 0) for e in events) if events else 0

    platforms: Dict[str, int] = {}
    lifecycles: Dict[str, int] = {}
    for e in events:
        for p in e.get("platforms_list") or []:
            platforms[p] = platforms.get(p, 0) + 1
        lc_zh = LIFE_ZH.get(e.get("lifecycle") or "", e.get("lifecycle") or "")
        if lc_zh:
            lifecycles[lc_zh] = lifecycles.get(lc_zh, 0) + 1

    # 主线 = 讨论量最大的事件；起势 = EMERGING/GROWING 里最热的一个
    top = max(events, key=lambda e: ((e.get("content_count") or 0), (e.get("hot_score") or 0))) \
        if events else None
    emerging = [e for e in events if e.get("lifecycle") in ("EMERGING", "GROWING")]
    emg_top = max(emerging, key=lambda e: e.get("hot_score") or 0) if emerging else None

    # —— 叙事：点出主线、规模、主战场、新起势的话题（人话，不复读数字）——
    parts: List[str] = []
    if top:
        parts.append(f"主线是〈{top['display_title']}〉"
                     f"（{top.get('main_platform') or '平台未知'}，{top['content_count']} 条，"
                     f"热度「{heat_label(top.get('hot_score'))}」档）")
    parts.append(f"整组共 {n_events} 个话题、{total_content} 条内容")
    if platforms:
        parts.append("主战场 " + "、".join(f"{k}({v})" for k, v in
                                          list(platforms.items())[:2]))
    if emg_top and emg_top is not top:
        parts.append(f"刚起势：〈{_short(emg_top['display_title'], 28)}〉")
    narrative = "。".join(parts) + "。"

    # —— 建议：点名具体话题 ——
    actions: List[str] = []
    if emg_top:
        actions.append(f"〈{_short(emg_top['display_title'], 20)}〉刚起势"
                       f"（热度「{heat_label(emg_top.get('hot_score'))}」），建议 24h 内评估是否跟进")
    if top and (top.get("content_count") or 0) >= 20:
        actions.append(f"〈{_short(top['display_title'], 20)}〉讨论量最大"
                       f"（{top['content_count']} 条），可考虑配合内容或活动")
    if not actions:
        actions.append("暂无紧急行动建议，保持监测")

    # —— 话题列表：只展示有信息量的，碎片折叠 ——
    shown = [e for e in events
             if (e.get("content_count") or 0) >= 2 or (e.get("hot_score") or 0) >= 0.3]
    shown.sort(key=lambda e: (-(e.get("content_count") or 0), -(e.get("hot_score") or 0)))
    shown = shown[:8]
    hidden = n_events - len(shown)

    return {
        "community_id": comm.get("community_id"),
        "headline": _headline(comm, events),
        "entity_labels": [_cn_label(e) for e in (comm.get("entities") or [])[:8]],
        "generated_at": datetime.now().isoformat(),
        "n_events": n_events,
        "total_content": total_content,
        "max_heat": round(max_heat, 4),
        "max_heat_label": heat_label(max_heat),
        "top_titles": [e["display_title"] for e in shown[:3]],
        "platforms": platforms,
        "lifecycles": lifecycles,
        "narrative": narrative,
        "actions": actions,
        "hidden_topics": hidden,
        "events": [_event_row(e) for e in shown],
    }


def generate_all(l3_db: Optional[str] = None) -> List[Dict[str, Any]]:
    comms, events = load_community_events(l3_db)
    if not comms or not events:
        return []

    # ★ 每个事件只归"实体重叠最多"的社群 —— 同一话题不再出现在两份报告里
    comm_ents = [(c["community_id"], set(c.get("entities") or [])) for c in comms]
    by_comm: Dict[str, List[Dict]] = {c["community_id"]: [] for c in comms}
    misc: List[Dict] = []
    for ev in events:
        best, best_n = None, 0
        for cid, ents in comm_ents:
            n = len(ents & set(ev["entities"]))
            if n > best_n:
                best, best_n = cid, n
        (by_comm[best] if best else misc).append(ev)

    reports = []
    for c in comms:
        items = by_comm.get(c["community_id"]) or []
        if items:
            reports.append(generate_report(c, items))

    # ★ 掉群的零散话题拼一份报告 —— 不假装它们有归属，但也不让它们消失
    if misc:
        notable = [e for e in misc if (e.get("content_count") or 0) >= 2]
        if notable:
            notable.sort(key=lambda e: (-(e.get("content_count") or 0), -(e.get("hot_score") or 0)))
            shown = notable[:8]
            parts = [f"以下 {len(misc)} 个话题还没和主社群连上，多为单一平台的小范围讨论"]
            hot_ev = max(misc, key=lambda e: e.get("hot_score") or 0)
            if (hot_ev.get("hot_score") or 0) >= 0.35:
                parts.append(f"其中〈{_short(hot_ev['display_title'], 28)}〉"
                             f"热度「{heat_label(hot_ev.get('hot_score'))}」档，值得关注")
            mx = max((e.get("hot_score") or 0) for e in misc)
            reports.append({
                "community_id": "comm_misc",
                "headline": "零散话题（未成社群）",
                "entity_labels": [],
                "generated_at": datetime.now().isoformat(),
                "n_events": len(misc),
                "total_content": sum(e.get("content_count") or 0 for e in misc),
                "max_heat": round(mx, 4),
                "max_heat_label": heat_label(mx),
                "top_titles": [e["display_title"] for e in shown[:3]],
                "platforms": {},
                "lifecycles": {},
                "narrative": "。".join(parts) + "。",
                "actions": ["社群检测等数据更多时会把它们连上；单条详情仍可点开看"],
                "hidden_topics": len(misc) - len(shown),
                "events": [_event_row(e) for e in shown],
            })
    out_path = os.path.join(_ROOT, "data", "state", "community_reports.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now().isoformat(),
                   "reports": reports}, f, ensure_ascii=False, indent=2)
    return reports


if __name__ == "__main__":
    reports = generate_all()
    print(f"生成 {len(reports)} 份社区报告")
    for r in reports:
        print(f"  [{r['headline']}]")
        print(f"    {r['narrative']}")
        for a in r["actions"]:
            print(f"    ▸ {a}")
