#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/semantic_recall.py —— 游戏话题的**语义召回**（词表之外的第二道网）。

★ 为什么需要它（2026-10-05 实测暴露的缺口）：
  词表粗筛只认字面。实测 B站 132 条里漏掉了这些**真·游戏热点**：
    【崩铁】遐蝶综合测评      ← 「崩铁」不在 games/ 档案里
    蛋仔派对：新手教程暗藏地牢
    “你们可能不相信，但这就是绝区零玩家的日常”
    lol愚人节彩蛋：风龙变派对龙
    斥巨资拿下金色大象！皮卡新皮肤测评
    【ARK no NIGHTS】通关攻略        ← 完全没有任何我们的关键词
  原因很简单：**我们只给方舟和鸣潮建了档案，但社区关心的是整个游戏圈。**
  词表永远追不上黑话、简称、缩写、二创梗。

★ 做法：用中文 embedding（bge-small-zh-v1.5）算语义相似度，
  与一批"游戏话题锚点"比对，超过阈值就召回。
  这是**召回**（宁可多召回），精度仍由下游 LLM 精判保证。

★ 模型是可选依赖：没装/下载不到就返回空召回，词表层照常工作（降级不崩）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# 语义锚点：覆盖不同游戏品类和黑话风格，作为"这条像不像游戏话题"的参照系
ANCHORS: List[str] = [
    "明日方舟新版本上线，玩家讨论新干员强度和抽卡",
    "崩坏星穹铁道角色测评，抽卡建议和队伍搭配",
    "蛋仔派对新版本玩法，玩家吐槽和攻略",
    "绝区零新角色爆料，玩家讨论剧情和战斗",
    "英雄联盟愚人节活动彩蛋，玩家玩梗",
    "永劫无间新模式实机演示，竞技游戏讨论",
    "独立游戏发售预告，玩家评价和愿望单",
    "三角洲行动版本更新，武器平衡和外挂吐槽",
    "游戏联动官宣，跨平台合作和 IP 联动",
    "游戏主播实况解说，玩家弹幕互动",
    "手游公测开服，预约人数和氪金讨论",
    "单机恐怖游戏实况，剧情解说和通关评价",
]


class SemanticRecall:
    """向量召回器。首次调用才加载模型（加载约 1-2 秒）。"""

    def __init__(self, model_name: str = MODEL_NAME, threshold: float = 0.62) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._model = None
        self._tok = None
        self._anchors: Any = None
        self.available = False
        self.reason = ""

    # ---------- 模型 ----------
    def _ensure(self) -> bool:
        if self.available:
            return True
        if self._model is not None and not self.available:
            return False
        try:
            import torch
            import torch.nn.functional as F
            from transformers import AutoModel, AutoTokenizer
            self._torch, self._F = torch, F
            self._tok = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModel.from_pretrained(self.model_name)
            self._model.eval()
            self._anchors = self._encode(ANCHORS)
            self.available = True
            return True
        except Exception as e:                 # 没装 transformers / 下载失败 / 无网
            self.reason = f"{type(e).__name__}: {str(e)[:120]}"
            self._model = None
            return False

    def _encode(self, texts: Sequence[str]):
        with self._torch.no_grad():
            b = self._tok(list(texts), padding=True, truncation=True,
                          max_length=256, return_tensors="pt")
            out = self._model(**b).last_hidden_state[:, 0]
            return self._F.normalize(out, dim=-1)

    # ---------- 召回 ----------
    def recall(self, items: List[Dict[str, Any]], limit: int = 200) -> Dict[str, Any]:
        """返回 {item_id: 相似度}，只含相似度 ≥ 阈值的条目。

        LRU 缓存：同一批文本不重复编码（跨轮采样时省算力）。
        """
        if not items:
            return {"hits": {}, "available": self.available, "reason": self.reason,
                    "scanned": 0}
        if not self._ensure():
            return {"hits": {}, "available": False, "reason": self.reason,
                    "scanned": len(items)}

        pool = items[:limit]
        texts = [f"{it.get('title','')}。{it.get('desc','')}"[:250] for it in pool]
        try:
            vecs = self._encode(texts)
        except Exception as e:
            self.reason = f"encode failed: {str(e)[:100]}"
            return {"hits": {}, "available": False, "reason": self.reason,
                    "scanned": len(pool)}

        sims = (vecs @ self._anchors.T).max(dim=1).values    # 与最相似锚点的分数
        hits: Dict[str, float] = {}
        for it, s in zip(pool, sims.tolist()):
            if s >= self.threshold:
                hits[str(it.get("id"))] = round(float(s), 3)
        return {"hits": hits, "available": True, "reason": "",
                "scanned": len(pool), "threshold": self.threshold}


# 进程内单例（加载模型很贵，别反复来）
_RECALLER: Optional[SemanticRecall] = None


def get_recaller(threshold: float = 0.62) -> SemanticRecall:
    global _RECALLER
    if _RECALLER is None:
        _RECALLER = SemanticRecall(threshold=threshold)
    return _RECALLER


if __name__ == "__main__":
    import json
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import hotspot_filter as hf

    items = (hf.load_items_from_csv("data/raw/bilibili/hot_videos.csv", "bilibili") +
             hf.load_items_from_csv("data/raw/baidu_index/hot_search.csv", "baidu"))
    passed = {i["id"] for i in hf.HotspotFilter.rule_screen(items)}
    missed = [i for i in items if i["id"] not in passed]
    r = get_recaller()
    res = r.recall(missed)
    print(f"未过词表 {len(missed)} 条；语义召回可用={res['available']} {res['reason']}")
    print(f"召回了 {len(res['hits'])} 条（阈值 {res.get('threshold')}）\n")
    for it in sorted(missed, key=lambda i: -res["hits"].get(i["id"], 0)):
        s = res["hits"].get(it["id"])
        if s:
            print(f"  {s:.3f}  [{it['platform']}] {it['title'][:52]}")
    json.dump({"recalled": res["hits"]},
              open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "state", "semantic_recall.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)