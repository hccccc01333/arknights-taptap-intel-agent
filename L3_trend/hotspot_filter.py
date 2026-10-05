#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/hotspot_filter.py —— 外部热点的「游戏相关性」判断。

★ 为什么要这一层（2026-10-05）：
  全网热搜里绝大多数不是游戏（明星、社会新闻）。TapTap 是游戏社区，
  收到「某明星出轨上榜」这种告警毫无意义。所以外部信号进来要过两道：

    ① 规则粗筛（免费、召回优先）—— crawl_baidu_hot.load_game_terms()
       行业级词表（是不是游戏话题）+ 自有游戏档案（是不是我们关心的游戏）
       实测 B站游戏区 132 条 → 命中 36 条（27%）；只用档案词时只有 5 条（3%）。

    ② LLM 精判（贵、精度优先）—— 本模块
       粗筛进来的每条问一次：这个话题和 TapTap 的游戏社区有什么关系？
       输出分档，**决定它要不要进热点看板**。

★ 三档结果（对齐运营动作，不是"是否游戏"的二值）：
    related    相关 —— 直接可用的素材/情报
    adjacent   邻接 —— 同一批玩家/同一题材但没直接关系，可作背景参考
    irrelevant 无关 —— 丢弃，不进看板

用法：
    from hotspot_filter import HotspotFilter
    f = HotspotFilter()
    verdicts = f.judge([{"id":..., "title":..., "desc":..., "platform":...}, ...])
"""

from __future__ import annotations

import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))     # <root>/L3_trend
_ROOT = os.path.dirname(_HERE)                          # 项目根
for _p in (_HERE, _ROOT, os.path.join(_ROOT, "L4_intelligence"),
           os.path.join(_ROOT, "L1_data_source")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 我们自己的游戏（精判时告诉 LLM"我们关心什么"，避免它自己猜）
def _own_games() -> List[str]:
    names: List[str] = []
    gdir = os.path.join(_ROOT, "games")
    if os.path.isdir(gdir):
        for fn in os.listdir(gdir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(gdir, fn), encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("name"):
                    names.append(str(d["name"]))
            except (ValueError, OSError):
                continue
    return names


OWN_GAMES = _own_games()

SYSTEM_PROMPT = """你是游戏社区（TapTap）的舆情分析员。

我们是一家游戏社区公司，产品是 TapTap 游戏社区。现在在追踪**全网**热点——
微博、抖音、B站、百度热搜、贴吧、知乎等平台上正在被大量讨论的话题。

你的任务：判断每个外部热点话题，**和 TapTap 的游戏社区有没有关系**，以及关系有多强。

判断依据（三条，缺一不可）：
1. 这个话题本身是不是游戏相关（游戏产品、游戏行业事件、游戏玩法讨论等）；
2. 如果是游戏相关，它是否涉及我们关注的游戏，或同一批核心玩家；
3. 它对 TapTap 社区运营**有没有可操作的价值**（能借势、需警惕、纯背景）。

分三档（只选一个）：
- related   相关：直接涉及我们关注的游戏，或游戏行业重大事件（版本事故、爆款玩法、
             行业政策等）。这类会进热点看板，值得深挖。
- adjacent  邻接：是游戏话题但与我们关系较弱（别家游戏的具体内容、泛二次元、
             游戏圈八卦）。留作背景参考，不进看板主列表。
- irrelevant 无关：不是游戏话题，或纯娱乐明星八卦。丢弃。

严格按 JSON 输出，不要任何多余文字：
{"verdicts":[{"id":"原样返回的id","verdict":"related|adjacent|irrelevant","reason":"一句话理由（中文，不超过30字）"}]}"""


class HotspotFilter:
    def __init__(self, enabled: Optional[bool] = None) -> None:
        self._router = None
        self._enabled = enabled
        self.usage_stats = {"llm_calls": 0, "llm_items": 0, "rule_items": 0}

    def _get_router(self):
        if self._router is None and self._enabled is not False:
            try:
                from intelligence.llm import ModelRouter
                self._router = ModelRouter(enabled=True)
            except Exception as e:
                print(f"[warn] LLM 不可用（{str(e)[:80]}），全部走规则兜底", file=sys.stderr)
                self._enabled = False
        return self._router

    # ------------------------------------------------------------ 规则粗筛
    def screen(self, items: List[Dict[str, Any]],
               use_semantic: bool = True) -> Dict[str, Any]:
        """**双路召回**（词表 + 语义），返回 {candidates, via_rule, via_semantic, ...}。

        ★ 为什么两路（2026-10-05 实测）：
          词表只认字面，实测漏掉《崩铁》遐蝶测评、蛋仔派对、lol愚人节彩蛋、
          绝区零玩家日常、ARK no NIGHTS 攻略 —— 这些是真游戏热点，但标题里
          没有我们的关键词（我们只给方舟/鸣潮建了档案，社区关心的是整个游戏圈）。
          语义召回（bge）实测把这些救回来了：崩铁 0.764 / 蛋仔 0.767 / lol 0.743。
          模型不可用时自动退回纯词表，**不崩**（见 semantic_recall 的降级）。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "crawl_baidu_hot",
            os.path.join(_ROOT, "L1_data_source", "collectors", "baidu", "crawl_baidu_hot.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        via_rule, rest = [], []
        for it in items:
            text = "{} {}".format(it.get("title", ""), it.get("desc", ""))
            (via_rule if mod.is_game_related(text) else rest).append(it)

        via_semantic: List[Dict[str, Any]] = []
        sem_scores: Dict[str, float] = {}
        sem_note = "未启用"
        if use_semantic and rest:
            try:
                from semantic_recall import get_recaller
                res = get_recaller().recall(rest)
                sem_scores = res.get("hits") or {}
                if not res.get("available"):
                    sem_note = "语义模型不可用（{}），本轮只用词表".format(res.get("reason", "")[:60])
                else:
                    via_semantic = [it for it in rest if str(it.get("id")) in sem_scores]
            except Exception as e:
                sem_note = "语义召回异常：{}".format(str(e)[:60])

        cands = via_rule + via_semantic
        self.usage_stats["via_rule"] = self.usage_stats.get("via_rule", 0) + len(via_rule)
        self.usage_stats["via_semantic"] = self.usage_stats.get("via_semantic", 0) + len(via_semantic)
        for it in via_semantic:
            it["_sem_score"] = sem_scores.get(str(it.get("id")))
        return {"candidates": cands, "via_rule": via_rule, "via_semantic": via_semantic,
                "sem_scores": sem_scores, "sem_note": sem_note,
                "missed_by_rule": len(rest)}

    # ------------------------------------------------------------ LLM 精判
    def judge(self, items: List[Dict[str, Any]], batch_size: int = 8) -> List[Dict[str, Any]]:
        """对粗筛后的候选逐条判相关性。返回带 verdict/reason 的完整列表。"""
        screen = self.screen(items)
        candidates = screen["candidates"]
        self.usage_stats["rule_items"] += len(candidates)
        router = self._get_router()
        # 未过粗筛的直接判无关（免费）
        cand_ids = {str(it.get("id")) for it in candidates}
        verdicts: Dict[str, Dict[str, Any]] = {}
        for it in items:
            iid = str(it.get("id"))
            if iid in cand_ids:
                continue
            if iid in screen.get("sem_scores", {}):
                verdicts[iid] = {"verdict": "adjacent", "reason": "语义召回但未过 LLM"}
            else:
                verdicts[iid] = {"verdict": "irrelevant", "reason": "词表与语义均未召回"}
        if not router or not candidates:
            for c in candidates:
                verdicts[str(c.get("id"))] = {"verdict": "adjacent", "reason": "LLM 不可用，保守留作背景"}
            return self._merge(items, verdicts)

        games = "、".join(OWN_GAMES) if OWN_GAMES else "（未配置）"
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            lines = []
            for c in batch:
                lines.append(
                    f"- id={c.get('id')}\n  平台：{c.get('platform', '?')}\n"
                    f"  话题：{c.get('title', '')}\n  描述：{c.get('desc', '')[:120]}")
            prompt = f"我们关注的游戏：{games}\n\n待判断的话题：\n" + "\n".join(lines)
            try:
                self.usage_stats["llm_calls"] += 1
                self.usage_stats["llm_items"] += len(batch)
                out = router.call_json("relevance", prompt, system=SYSTEM_PROMPT,
                                       max_tokens=1600)
                got = (out or {}).get("verdicts") or []
                # ★ 返回条数不足 → 拆半重试（实测：8 条稳定，10 条开始漏，20 条全空）
                if len(got) < len(batch) and len(batch) > 1:
                    half = max(1, len(batch) // 2)
                    got = []
                    for sub in (batch[:half], batch[half:]):
                        got += self._judge_batch(router, sub, games) or []
                got_map = {str(g.get("id")): g for g in got if g.get("id")}
                for c in batch:
                    g = got_map.get(str(c.get("id")))
                    if g and g.get("verdict") in ("related", "adjacent", "irrelevant"):
                        verdicts[str(c.get("id"))] = {"verdict": g["verdict"],
                                                     "reason": str(g.get("reason") or "")[:60]}
                    else:
                        verdicts[str(c.get("id"))] = {"verdict": "adjacent",
                                                     "reason": "LLM 未给出有效分档，保守留作背景"}
            except Exception as e:
                print(f"[warn] 精判批次失败：{str(e)[:80]}", file=sys.stderr)
                for c in batch:
                    verdicts[str(c.get("id"))] = {"verdict": "adjacent",
                                                 "reason": "精判调用失败，保守留作背景"}
        return self._merge(items, verdicts)

    def _judge_batch(self, router, batch: List[Dict[str, Any]],
                     games: str) -> List[Dict[str, Any]]:
        """调一次 LLM 判一批。返回解析出的 verdicts 列表（失败返回空）。"""
        lines = []
        for c in batch:
            lines.append(
                "- id={}\n  平台：{}\n  话题：{}\n  描述：{}".format(
                    c.get("id"), c.get("platform", "?"),
                    c.get("title", ""), c.get("desc", "")[:120]))
        prompt = "我们关注的游戏：{}\n\n待判断的话题：\n{}".format(
            games, "\n".join(lines))
        try:
            self.usage_stats["llm_calls"] += 1
            self.usage_stats["llm_items"] += len(batch)
            out = router.call_json("relevance", prompt, system=SYSTEM_PROMPT, max_tokens=1600)
            return (out or {}).get("verdicts") or []
        except Exception as e:
            print(f"[warn] 精判批次失败：{str(e)[:80]}", file=sys.stderr)
            return []

    @staticmethod
    def _merge(items: List[Dict[str, Any]], verdicts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for it in items:
            v = verdicts.get(str(it.get("id"))) or {"verdict": "irrelevant", "reason": "未判定"}
            row = dict(it)
            row.update({"verdict": v["verdict"], "reason": v["reason"]})
            out.append(row)
        return out

    # ------------------------------------------------------------ 落盘
    @staticmethod
    def save(path: str, verdicts: List[Dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"verdicts": verdicts}, f, ensure_ascii=False, indent=2)


def load_items_from_csv(path: str, platform: str) -> List[Dict[str, Any]]:
    """从各平台的 raw csv 读成统一格式的候选列表。"""
    if not os.path.exists(path):
        return []
    items: List[Dict[str, Any]] = []
    seen: set = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = r.get("bvid") or r.get("word") or r.get("external_id") or ""
            title = r.get("title") or r.get("word") or ""
            if not key or not title:
                continue
            # ★ 这些 csv 是**累积快照**（每轮 append），同一 bvid/词会出现多次；
            #   不去重会让同一个话题被重复判、重复计入统计。
            if key in seen:
                continue
            seen.add(key)
            items.append({"id": f"{platform}:{key}", "platform": platform,
                          "title": title,
                          "desc": r.get("desc") or r.get("description") or "",
                          "observed_at": r.get("observed_at") or ""})
    return items


if __name__ == "__main__":
    import sys as _s
    root = _ROOT
    items = (load_items_from_csv(os.path.join(root, "data/raw/baidu_index/hot_search.csv"), "baidu") +
             load_items_from_csv(os.path.join(root, "data/raw/bilibili/hot_videos.csv"), "bilibili"))
    f = HotspotFilter()
    res = f.judge(items)
    counts: Dict[str, int] = {}
    for r in res:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    out_path = os.path.join(_ROOT, "data", "state", "hotspot_relevance.json")
    HotspotFilter.save(out_path, res)
    sc = f.screen(items)
    print(f"共 {len(res)} 条: {counts}  | 统计 {f.usage_stats}")
    print(f"双路召回: 词表 {len(sc['via_rule'])} + 语义 {len(sc['via_semantic'])} "
          f"| 词表漏掉 {sc['missed_by_rule']} 条 | {sc['sem_note']}")
    print(f"判定结果已存: {out_path}")
    for v in ("related", "adjacent"):
        hits = [r for r in res if r["verdict"] == v][:10]
        if hits:
            print(f"\n== {v} ==")
            for r in hits:
                print(f"  [{r['platform']}] {r['title'][:40]}  ← {r['reason']}")