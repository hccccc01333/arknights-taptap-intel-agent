#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/game_tags.py —— 游戏标签库（S1 索引 → 热点 ↔ TapTap 社区匹配）。

★ 为什么需要这层（2026-10-05 用户指出）：
  外部热点（百度/微博/B站）提到某个游戏时，我们需要知道：
    · 这个游戏在 TapTap 上有没有社区？（app_id / group_id）
    · 社区有多大？（关注数 / 帖子量 —— 判断这个热点值不值得跟进）
    · 有没有官方号？（有没有官方声量）
  这些全在 S1 索引里（community_map.csv），不需要额外构建。

★ 数据来源：groups/game 接口 + app_id 枚举补齐（详见 S1 采集器注释）。
  1246 个社区、1016 个可寻址（有 app_id + group_id）。

用法：
    from game_tags import GameTagIndex
    idx = GameTagIndex()
    idx.match("原神")           → {"app_id": "168332", "group_id": "201765", ...}
    idx.match("arknights")     → 同上（支持别名）
    idx.match("光遇")           → None（不在索引里 = TapTap 没这个社区或枚举没覆盖）
"""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

MAP_CSV = os.path.join(_ROOT, "data", "raw", "taptap", "community", "community_map.csv")

# 常见别名 → 游戏名（匹配用，和 event_graph 同源）
_COMMON_ALIASES = {
    "崩铁": "崩坏：星穹铁道", "星穹铁道": "崩坏：星穹铁道", "星铁": "崩坏：星穹铁道",
    "hsr": "崩坏：星穹铁道",
    "绝区零": "绝区零", "zenless": "绝区零",
    "原神": "原神", "genshin": "原神",
    "鸣潮": "鸣潮", "wuthering waves": "鸣潮",
    "永劫": "永劫无间",
    "蛋仔": "蛋仔派对",
    "方舟": "明日方舟",
    "王者": "王者荣耀",
    "lol": "英雄联盟", "英雄联盟": "英雄联盟",
    "光遇": "光·遇", "sky": "光·遇",
    "三角洲": "三角洲行动",
    "cs2": "CS2", "csgo": "CS2",
    "我的世界": "我的世界", "minecraft": "我的世界",
}


class GameTagIndex:
    """游戏标签索引：游戏名/别名 → TapTap 社区信息（app_id/group_id/规模）。"""

    def __init__(self, map_csv: str = MAP_CSV) -> None:
        self.games: Dict[str, Dict[str, Any]] = {}          # title → row
        self.alias_to_title: Dict[str, str] = {}            # alias/lower → title
        if os.path.exists(map_csv):
            with open(map_csv, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    title = (r.get("title") or "").strip()
                    if not title:
                        continue
                    # 别名预登记（让 match 能命中带中点/后缀的名字）
                    pass
                    row = {
                        "title": title,
                        "app_id": (r.get("app_id") or "").strip(),
                        "group_id": (r.get("group_id") or "").strip(),
                        "favorite_count": int(r.get("favorite_count") or 0),
                        "topic_count": int(r.get("topic_count") or 0),
                        "recent_topic_count": int(r.get("recent_topic_count") or 0),
                        "official_topic_count": int(r.get("official_topic_count") or 0),
                        "source": r.get("source") or "",
                    }
                    self.games[title] = row
                    # 精确名 → title
                    self.alias_to_title[title.lower()] = title
                    # 常见别名
                    for alias, canon in _COMMON_ALIASES.items():
                        if canon == title:
                            self.alias_to_title[alias] = title

    @staticmethod
    def _clean(s: str) -> str:
        """去掉中点/空格/标点，"光·遇"→"光遇"。"""
        return "".join(ch for ch in (s or "") if ch.isalnum() or "一" <= ch <= "鿿").lower()

    def match(self, text: str) -> Optional[Dict[str, Any]]:
        """从一段文本里匹配游戏名。

        三层匹配（越来越宽松）：
          ① 去标点后精确匹配（"光·遇"→"光遇"）
          ② 别名表
          ③ 子串包含（长名优先）
        """
        query = self._clean(text or "")
        if not query:
            return None
        # ① 去标点后精确
        for title in self.games:
            if self._clean(title) == query:
                return self.games[title]
        # ② 别名表（也去标点）
        for alias, title in self.alias_to_title.items():
            if self._clean(alias) == query:
                return self.games.get(title)
        # ③ 子串包含（查询词或社区名任一方包含另一方）
        for title, row in self.games.items():
            ct = self._clean(title)
            if ct and (ct in query or query in ct):
                return row
        # ④ 别名子串
        for alias, title in sorted(self.alias_to_title.items(), key=lambda x: -len(x[0])):
            ca = self._clean(alias)
            if len(ca) >= 2 and (ca in query or query in ca):
                return self.games.get(title)
        return None

    def stats(self) -> Dict[str, Any]:
        total = len(self.games)
        addressable = sum(1 for g in self.games.values()
                         if g["app_id"] and g["group_id"])
        return {"total": total, "addressable": addressable}

    def top_by(self, field: str, n: int = 10) -> List[Dict[str, Any]]:
        return sorted((g for g in self.games.values() if g.get(field)),
                      key=lambda g: -g[field])[:n]


if __name__ == "__main__":
    idx = GameTagIndex()
    st = idx.stats()
    print(f"游戏标签库: {st['total']} 个游戏，{st['addressable']} 个可寻址\n")

    # 实测：百度游戏热搜的 30 条能不能匹配到
    import requests
    UA = {"User-Agent": "Mozilla/5.0 Chrome/138.0.0.0"}
    r = requests.get("https://top.baidu.com/api/board?platform=pc&tab=game",
                     headers=UA, timeout=15)
    items = []
    def walk(o):
        if isinstance(o, list):
            for x in o: walk(x)
        elif isinstance(o, dict):
            if "word" in o: items.append(o["word"])
            else: [walk(v) for v in o.values()]
    walk(r.json())

    matched = unmatched = 0
    for w in items[:30]:
        m = idx.match(w)
        if m:
            matched += 1
            print(f"  ✓ {w[:18]:<20} app={m['app_id']:<8} 关注{m['favorite_count']:>10,}")
        else:
            unmatched += 1
            print(f"  ✗ {w[:18]:<20} （TapTap 无此社区或不在索引）")
    print(f"\n匹配率: {matched}/{matched+unmatched}")
