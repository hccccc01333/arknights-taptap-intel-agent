#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/game_terms.py —— 全局唯一的游戏词表加载器。

★ 为什么抽出来（2026-10-05 用户指出"词表要做全"）：
  之前**六个采集器各自写了一份 load_game_terms()**，内容互不相同：
  有的读 community_map.csv、有的硬编码 132 个行业词、有的用 S1 索引现算。
  后果是同一个热点在不同平台得到不同的"游戏相关性"判定 ——
  百度认得的游戏微博未必认得。新词表建好了却没人读，等于白建。

  现在六个采集器全部走这里，词表只有一份，改一次全网生效。

★ 三层组成（缺一层就会漏）：
    ① 行业级词  —— 判断"这是不是游戏话题"（游戏/手游/版号/抽卡/开黑…）
    ② 游戏名    —— 2022 个游戏名（build_game_terms.py 多来源合并产出）
    ③ 档案词    —— games/*.json 里我们自己关注的游戏（方舟/鸣潮/库洛）

★ 匹配策略（性能）：词表 2000+ 条，不能对每条热点做 2000 次 in 判断。
  用 **AC 自动机**一次扫描全串，复杂度 O(len(text))，与词表大小无关。
  长词优先（"崩坏星穹铁道" 应先于 "崩坏" 命中），由 AC 的 fail 链输出最长匹配保证。

用法：
    from game_terms import GameTermMatcher
    m = GameTermMatcher()
    m.is_game_related("原神新版本上线")   → True
    m.matched("原神新版本上线")           → ["原神"]
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
TERM_TABLE = os.path.join(_ROOT, "data", "state", "game_term_table.json")
GAMES_DIR = os.path.join(_ROOT, "games")

# ── ① 行业级词：判断"这是不是游戏话题"，与具体游戏无关 ──────────────
INDUSTRY_TERMS: Set[str] = {
    # 品类与形态
    "游戏", "手游", "端游", "网游", "主机游戏", "steam", "ps5", "xbox",
    "switch", "任天堂", "索尼", "育碧", "暴雪", "米哈游", "鹰角", "腾讯游戏",
    "网易游戏", "完美世界", "游族", "莉莉丝", "叠纸", "库洛", "鹰角网络",
    "独立游戏", "买断制", "抽卡游戏", "二次元游戏", "手游版", "端游版",
    # 产业与商业
    "游戏行业", "游戏公司", "版号", "版号发放", "上线", "公测", "内测",
    "首测", "开服", "删档", "联动", "IP联动", "二次元", "二游", "开放世界",
    "抽卡", "氪金", "内购", "赛季更新", "版本更新", "平衡性调整", "削弱",
    "加强", "重做", "玩法更新", "新角色", "新干员", "卡池", "up池", "保底",
    "歪", "玩家群体", "游戏主播", "游戏实况", "攻略", "bug", "闪退", "卡顿",
    "服务器", "崩服", "停服", "抵制", "口碑",
    # 玩家行为与社区黑话
    "玩家", "吃鸡", "充值", "代练", "外挂", "赛事", "排位", "大逃杀",
    "开测", "保底歪", "角色碎片", "游戏主播", "电竞选手",
    "tapup", "taptap", "好游快爆", "九游", "游民星空", "3dm", "机核",
    "ign中国", "游戏日报", "篝火营地", "gamersky",
}


# ── ② 歧义词（2026-10-05 实测发现）：游戏圈和娱乐圈共用大量词 ──
#   "主播""上分""开黑"在游戏语境是游戏信号，在微博综合榜的娱乐新闻里是噪声。
#   实测：微博热榜 801 条里 7 条命中，全是「央视00后主播上新」「东航空姐跪地道歉」这类 ——
#   靠"主播"两字命中游戏，与热点真实主题无关。
#   规则：**只有当文本里同时出现一个明确游戏词时才允许歧义词生效**，
#   等于"主播 + 原神"才算游戏话题，"主播 + 上新"不算。
AMBIGUOUS_TERMS: Set[str] = {
    "主播", "上分", "开黑", "皮肤", "陪玩", "代肝", "抽卡欧皇", "非酋",
    "谷子", "痛风", "上镜", "出镜", "首秀", "开箱", "翻红", "塌房",
    "直播", "实况", "晒实况", "攻略向", "萌新", "萌新求助", "电竞",
    "道歉", "道歉门", "抵制", "口碑", "上线", "开服", "服务器", "bug",
    "攻略", "体验", "上新", "回应", "致歉", "争议", "曝光", "调查",
    # ★ 短泛词误报（2026-10-06 forming_game 实测）：词表收了真实游戏名
    #   （《边境》、雀魂UP主、传奇系列），但综合热搜里这些词被新闻通用义支配
    #   ——「中缅边境」「爱打麻将」「传奇人物」。移入歧义层：
    #   单独出现不算游戏话题，与明确游戏词同时出现才放行（"雀魂麻将"）。
    "麻将", "边境", "传奇", "首发",
}

# 明确游戏词 —— 歧义词需要它做"语境确认"。取游戏名 + 强品类词。

# ── 匹配前的文本归一化 ────────────────────────────────────────
# ★ 为什么需要（2026-10-05 实测：B站游戏区 191 条里 139 条漏判）：
#   样本「《崩坏：星穹铁道》动画短片」—— 词表里存的是"崩坏星穹铁道"（清洗时剥了冒号），
#   但匹配跑在**原文**上，原文带全角冒号和书名号，AC 自动机自然扫不到。
#   视频标题还普遍夹《》【】（）等包装符，中文里这些符号常被作者省略或替换。
#   归一化后再喂给自动机，词表侧和文本侧的写法差异就抹平了。
_NORMALIZE_MAP = str.maketrans({
    **{c: "" for c in "：:·・•．-－—–~～、，。！？!?,.;；|/\《》〈〉「」『』【】〔〕()（）<>《》"},
    "　": " ", " ": " ",
})


def normalize_for_match(text: str) -> str:
    """剥掉包装符号与标点，只留下实词字符（英文数字小写）。"""
    return (text or "").lower().translate(_NORMALIZE_MAP)


def _load_term_table(path: str = TERM_TABLE) -> Dict[str, str]:
    """读 build_game_terms.py 产出的词表。文件缺失时返回空（不让采集器崩）。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("terms") or {}
    except (ValueError, OSError):
        return {}


def _load_char_terms() -> Dict[str, dict]:
    """角色/活动/皮肤词典（build_char_terms.py 产出）。文件缺失返回空。"""
    path = os.path.join(_ROOT, "data", "state", "char_terms.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("chars") or {}
    except (ValueError, OSError):
        return {}


def _load_own_games(games_dir: str = GAMES_DIR) -> Set[str]:
    """games/*.json 里的自有游戏档案（name + key + aliases）。"""
    out: Set[str] = set()
    if not os.path.isdir(games_dir):
        return out
    for fn in os.listdir(games_dir):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(games_dir, fn), encoding="utf-8") as f:
                d = json.load(f)
        except (ValueError, OSError):
            continue
        for k in [d.get("name"), d.get("key")] + list(d.get("aliases") or []):
            if k:
                out.add(str(k).strip().lower())
    return out


class _ACNode:
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: Dict[str, "_ACNode"] = {}
        self.fail: Optional["_ACNode"] = None
        self.outputs: List[str] = []


class GameTermMatcher:
    """AC 自动机多模式匹配 —— 2000+ 游戏名 + 行业词一次扫描出全部命中。"""

    def __init__(self, extra_terms: Optional[List[str]] = None) -> None:
        self.own_games: Set[str] = _load_own_games()
        term_table = _load_term_table()

        self.game_terms: Set[str] = {t.lower() for t in term_table if t}
        # ★ 第四层：游戏专有名词（角色/活动/皮肤）—— 标题只有角色名时也能判游戏
        self.char_terms: Dict[str, dict] = _load_char_terms()
        self.game_terms |= {w.lower() for w in self.char_terms}
        self.industry_terms: Set[str] = {t.lower() for t in INDUSTRY_TERMS}
        for t in extra_terms or []:
            self.industry_terms.add(t.lower())

        # 自己关注游戏的别名也进自动机 —— 命中即"这是我们关心的游戏"
        self.own_markers: Set[str] = set(self.own_games)
        self.ambiguous: Set[str] = {t.lower() for t in AMBIGUOUS_TERMS}

        self._root = _ACNode()
        self._add_all(self.industry_terms)
        self._add_all(self.game_terms)
        self._add_all(self.ambiguous)
        self._add_all(self.own_markers)
        self._build_fail()

    # ── AC 构建 ────────────────────────────────────────────────────
    def _add_all(self, terms: Set[str]) -> None:
        for t in terms:
            if not t:
                continue
            node = self._root
            for ch in t:
                node = node.children.setdefault(ch, _ACNode())
            node.outputs.append(t)

    def _build_fail(self) -> None:
        from collections import deque
        q: deque = deque()
        for ch, node in self._root.children.items():
            node.fail = self._root
            q.append(node)
        while q:
            cur = q.popleft()
            for ch, child in cur.children.items():
                f = cur.fail
                while f is not None and ch not in f.children:
                    f = f.fail
                child.fail = f.children[ch] if f is not None and ch in f.children else self._root
                # 继承 fail 链上的输出，保证 "崩坏星穹铁道" 被截断匹配时仍能拿到完整词
                if child.fail is not None:
                    child.outputs.extend(child.fail.outputs)
                q.append(child)

    # ── 查询 ───────────────────────────────────────────────────────
    def matched(self, text: str) -> List[str]:
        """返回文本里命中的全部词条（去重，小写），歧义词已按语境过滤。"""
        raw = self._scan(text)
        if not raw:
            return []
        # 有**明确**游戏词 → 歧义词全部放行（"主播"在游戏视频里是有效信号）。
        # ★ 激活集必须排除歧义词本身：像"麻将"既是真游戏名又在歧义层，
        #   否则它会激活自己（"爱打麻将"→ 游戏，误报）。
        clear = (raw & (self.game_terms | self.own_markers | self.industry_terms))             - self.ambiguous
        if clear:
            return sorted(raw)
        # 只有歧义词命中 → 整条判为非游戏（微博娱乐新闻里全是这种）
        return sorted(raw - self.ambiguous)

    def _scan(self, text: str) -> Set[str]:
        low = normalize_for_match(text)
        hits: Set[str] = set()
        if not low:
            return hits
        node = self._root
        for ch in low:
            while node is not None and ch not in node.children:
                node = node.fail
            if node is None:
                node = self._root
                continue
            node = node.children[ch]
            for out in node.outputs:
                hits.add(out)
        return hits

    def matched_games(self, text: str) -> List[str]:
        """只返回游戏名（不含行业词）—— 用于标注"这条热点说的是哪个游戏"。"""
        return [h for h in self.matched(text) if h in self.game_terms or h in self.own_markers]

    def is_game_related(self, text: str) -> bool:
        return bool(self.matched(text))

    def is_own_game(self, text: str) -> bool:
        """是否命中我们自有游戏档案（方舟/鸣潮/库洛）。"""
        return bool(self.own_markers & set(self.matched(text)))


_default: Optional[GameTermMatcher] = None
_default_built_at: float = 0.0
_TERM_MTIME: float = -1.0
_RELOAD_CHECK_INTERVAL = 300.0    # 词表文件变化检查粒度（秒），不必每次调用都 stat


def get_matcher(force_reload: bool = False) -> GameTermMatcher:
    """进程内单例 + **热更新**。

    ★ 为什么需要热更新（2026-10-05）：
      词表由 build_game_terms.py 持续重建（S1 枚举还在跑，索引每小时都在涨），
      而 watch 采集进程一跑几天。单例若永不重建，进程就只能用启动那一刻的词表 ——
      新收录的游戏名要等下次重启才生效，中间的热点全判成"非游戏"。
      现在每 5 分钟检查一次词表文件 mtime，变了就重建（构建约几十 ms，代价可忽略）。
    """
    global _default, _default_built_at, _TERM_MTIME
    import time as _time
    now = _time.time()
    if _default is not None and not force_reload             and (now - _default_built_at) < _RELOAD_CHECK_INTERVAL:
        return _default
    try:
        mtime = os.path.getmtime(TERM_TABLE)
    except OSError:
        mtime = -1.0
    if _default is not None and not force_reload and mtime == _TERM_MTIME:
        _default_built_at = now
        return _default
    _TERM_MTIME = mtime
    _default = GameTermMatcher()
    _default_built_at = now
    return _default


if __name__ == "__main__":
    m = GameTermMatcher()
    print(f"词表：游戏名 {len(m.game_terms)} + 行业词 {len(m.industry_terms)} "
          f"+ 自有档案 {len(m.own_markers)}")
    for probe in ("原神新版本上线引发玩家讨论", "崩坏星穹铁道动画短片", "今天天气不错",
                  "和平精英又出新赛季", "CS2 Major 观赛", "地铁上看到一个paper",
                  "恋与深空新卡池", "光遇遇见了黄衣"):
        hits = m.matched(probe)
        print(f"  {probe[:22]:<24} → 游戏:{m.matched_games(probe)} 其他:{hits[:3]}")