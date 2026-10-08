#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/agent/hotspot_search_agent.py —— 搜索型采集 agent。

★ 定位（2026-10-06 用户定调）：
  「和爬虫一样的，去弥补爬虫的不足」—— 它是一个**采集器**，不是分析师：
    · 一样产出原始数据（append-only CSV，进 L2 管线）
    · 一样挂 watch 常驻、走 robust_watch
  不同的只有一点：**查什么、去哪查，由它自己临场决定**。

★ 补固定爬虫的三个短板：
  ① 清单外的话题看不见 —— 固定爬虫只爬 29 个吧/29 个关键词/固定榜单，
     话题扩散到清单外（"克莱门莎"话题自己长出了个吧）就是盲区。
     → agent 拿 forming 信号做**全站搜索**，看话题在哪些吧/哪些视频里蔓延。
  ② 爬虫不会"追" —— 爆发帖标题里的人才是个话题的真名字（"克莱门莎突破
     祖宗之法了"→ 搜"克莱门莎"），标题→查询词这一步固定爬虫做不了。
     → LLM 批量提取查询词（一次调用管一轮），失败回退规则（「」/【】内实体）。
  ③ 新话题进不了监控清单 —— 发现新实体只能等人手加。
     → agent 产出**监控提案**（data/state/agent_watchlist.json），
       bili/tieba 采集器启动时自动并入（带过期时间，48h 观察期）。

★ 数据流：
  forming 信号 → 查询词（LLM+规则）→ 贴吧全站搜索 + B站搜索
    → data/raw/agent/search_results.csv（append-only）
    → data/state/agent_watchlist.json（监控提案）
  两个固定采集器读 watchlist → 下一轮自动扩清单 → 闭环。

★ 成本：无新 forming 信号 = 0 次 LLM；有信号 = 每轮 1 次批量提取（max_tokens≈600）。

用法：
    python hotspot_search_agent.py --once      # 跑一轮（调试）
    python hotspot_search_agent.py --watch --interval 1800
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[3]
TZ_CN = timezone(timedelta(hours=8))
OUT_DIR = ROOT / "data" / "raw" / "agent"
OUT_CSV = OUT_DIR / "search_results.csv"
WATCHLIST = ROOT / "data" / "state" / "agent_watchlist.json"
AGENT_STATE = ROOT / "data" / "state" / "agent_state.json"

CSV_FIELDS = ["observed_at", "trigger_type", "trigger_word", "query",
              "platform", "item_id", "title", "where", "metric", "url", "create_time"]

# 每个查询词最多执行的小时数（同一话题别反复搜）
QUERY_COOLDOWN_H = 24
ZERO_HIT_COOLDOWN_H = 2
PROPOSAL_TTL_H = 48


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 信号读取
def _forming_signals(root: Path) -> List[Dict[str, Any]]:
    """从 forming 分析层拿本轮「正在形成」的游戏信号（三类触发源）。"""
    sys.path.insert(0, str(root / "L3_trend"))
    sys.path.insert(0, str(root / "L1_data_source" / "collectors"))
    from forming import analyze_all                      # noqa: E402
    from forming_report import game_related              # noqa: E402

    res = analyze_all(str(root))
    out: List[Dict[str, Any]] = []
    for plat, r in res.items():
        for f in (r.get("forming") or [])[:30]:
            if plat == "贴吧游戏吧":
                # 贴吧爆发帖：来自游戏吧，按构造就是游戏信号
                out.append({"type": "tieba_burst", "title": f.get("title") or "",
                            "forum": f.get("forum") or "",
                            "metric": f.get("label") or ""})
            elif plat == "B站投稿流":
                # 投稿流爆发关键词：word 本身就是关键词
                out.append({"type": "bili_burst", "title": f.get("word") or "",
                            "forum": "", "metric": f.get("label") or ""})
            else:
                # 热搜（百度/微博）：只要游戏相关的词
                w = f.get("word") or ""
                if game_related(w):
                    out.append({"type": f"{plat}_hot", "title": w,
                                "forum": "", "metric": f.get("rate") or ""})
    return out


# ---------------------------------------------------------------- 查询词提取
_STOP = re.compile(r"[的了啊呢吧么呀哦哈！!？?。，,、~～…\s]")


def _rule_extract(title: str) -> str:
    """规则兜底：优先取「」/【】内的实体，否则取去噪后的前 6 字。"""
    for m in re.findall(r"[「【]([^」】]{2,12})[」】]", title):
        return m
    t = _STOP.sub("", title)
    return t[:6]


_LLM_FAIL_KEY = "llm_consecutive_fails"


def _llm_extract(titles: List[str], st: Dict[str, Any]) -> Dict[int, str]:
    """LLM 批量提取查询词（爬虫做不了的"标题→话题真名"）。失败返回空。

    ★ 熔断：连续失败 ≥3 次（免费模型限流/空回是常态）→ 跳过 LLM 直接规则，
      不再每轮白等 1-2 分钟。成功一次就清零。
    """
    if st.get(_LLM_FAIL_KEY, 0) >= 3:
        return {}
    try:
        sys.path.insert(0, str(ROOT / "L4_intelligence" / "intelligence"))
        from llm import ModelRouter                             # noqa: E402
        router = ModelRouter(enabled=True)
        r = router.resolve(node="hotspot_agent_query")
        model = r.get("model")
        if not model:
            return {}
        lines = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
        prompt = (
            "你是游戏舆情系统的搜索查询词提取器。下面是正在爆发的帖子/视频标题。\n"
            "为每条提取一个搜索查询词（用于在贴吧/B站搜索该话题），要求：\n"
            "- 提取角色名/干员名/活动名/版本名/游戏名等实体词（2~8 字），不要整句\n"
            "- 标题里的游戏名（如明日方舟）不是重点，重点是这个话题特有的词\n"
            "只输出 JSON 数组：[{\"i\": 序号, \"q\": \"查询词\"}, ...]\n\n" + lines)
        out = router.chat(model=model, messages=[{"role": "user", "content": prompt}],
                          max_tokens=1200, temperature=0.1, json_mode=True)
        arr = out.get("content") or "[]"
        if isinstance(arr, str):
            # 免费模型输出形态不一：先整体解析（对象也收），再退最外层 [...]，再退逐条正则
            try:
                arr = json.loads(arr)
            except ValueError:
                i, j = arr.find("["), arr.rfind("]")
                if i == -1 or j <= i:
                    raise ValueError(f"无 JSON: {arr[:80]!r}")
                arr = json.loads(arr[i: j + 1])
            if isinstance(arr, dict):        # 单对象 {"i":0,"q":...} 或 {"items":[...]}
                arr = next((v for v in arr.values() if isinstance(v, list)), [arr])
        out_map: Dict[int, str] = {}
        for idx, x in enumerate(arr):
            if isinstance(x, dict) and x.get("q"):
                out_map[int(x.get("i", idx))] = str(x["q"]).strip()
            elif isinstance(x, str) and len(x.strip()) >= 2:
                out_map[idx] = x.strip()     # 模型偷懒回字符串数组也接住
        return out_map
    except Exception as e:
        st[_LLM_FAIL_KEY] = st.get(_LLM_FAIL_KEY, 0) + 1
        print(f"[warn] LLM 提取失败({st[_LLM_FAIL_KEY]}连败，3 次熔断)，回退规则："
              f"{type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
        return {}
    st[_LLM_FAIL_KEY] = 0
    return {}


# ---------------------------------------------------------------- 搜索执行
def _tieba_search(query: str, cookie: str) -> List[Dict[str, Any]]:
    """贴吧全站搜索：话题扩散到了哪些吧（PC 端已改前端渲染，移动端 JSON 可用）。"""
    import requests
    try:
        r = requests.get(
            "https://tieba.baidu.com/mo/q/search/thread",
            params={"word": query, "pn": 1},
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                     "Cookie": cookie},
            timeout=15)
        d = r.json()
        no = d.get("no")
        # ★ no=0 是成功 —— 不能写 `d.get("no") or -1`，0 是假值会把成功判成失败
        if no is None or int(no) != 0:
            print(f"[warn] 贴吧搜索 {query}: no={no} err={d.get('error')}",
                  file=sys.stderr)
            return []
        posts = (d.get("data") or {}).get("post_list") or []
        if not posts:
            print(f"[note] 贴吧搜索 {query}: no=0 但 0 帖", file=sys.stderr)
        out = []
        for p in posts:
            out.append({"platform": "tieba", "item_id": str(p.get("tid") or ""),
                        "title": re.sub(r"<[^>]+>", "", str(p.get("title") or ""))[:80],
                        "where": str(p.get("forum_name") or ""),
                        "metric": str(p.get("reply_num") or ""),
                        "url": f"https://tieba.baidu.com/p/{p.get('tid')}",
                        "create_time": str(p.get("create_time") or "")})
        return out
    except Exception as e:
        print(f"[warn] 贴吧搜索 {query} 失败：{type(e).__name__}", file=sys.stderr)
        return []


def _bili_search(query: str) -> List[Dict[str, Any]]:
    """B站搜索（综合排序）：话题在 B站的内容面。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bilibili"))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from crawl_bili_game_hot import BiliHot                 # noqa: E402
        global _BILI
        if "_BILI" not in globals() or _BILI is None:
            _BILI = BiliHot(1.0)
            if _BILI._wbi_error:
                print(f"[warn] B站 WBI 客户端初始化失败：{_BILI._wbi_error}", file=sys.stderr)
        vids = _BILI.search(query, order="totalrank")
        if not vids and _BILI._wbi_error:
            print(f"[warn] B站搜索 {query} 空结果（WBI: {_BILI._wbi_error}）", file=sys.stderr)
        out = []
        for v in vids[:15]:
            row = BiliHot._row(v, f"agent:{query}", 0, now_iso())
            out.append({"platform": "bili", "item_id": row["bvid"],
                        "title": re.sub(r"<[^>]+>", "", row["title"])[:80],
                        "where": row["author"],
                        "metric": str(row["play"]),
                        "url": row["url"],
                        "create_time": str(row["pubdate"] or "")})
        return out
    except Exception as e:
        print(f"[warn] B站搜索 {query} 失败：{type(e).__name__}", file=sys.stderr)
        return []


_BILI: Any = None



def _weibo_search(query: str, cookie: str) -> List[Dict[str, Any]]:
    """微博关键词搜索（s.weibo.com SSR）：话题在微博的舆论面。

    ★ 为什么走 SSR 而不是 m.weibo.cn 的 JSON 接口（2026-10-06 实测）：
      m 端 getIndex 对非官方客户端回 ok=-100；s.weibo.com 用 weibo.com 登录态
      （热搜采集器同款 cookie）直接出完整 SSR。卡片解析按 block 切分。
    """
    import requests
    from urllib.parse import unquote
    try:
        r = requests.get(
            "https://s.weibo.com/weibo", params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 Chrome/120 Safari/537.36",
                     "Cookie": cookie},
            timeout=15)
        if r.status_code != 200 or len(r.text) < 5000:
            print(f"[warn] 微博搜索 {query}: 状态 {r.status_code} 长度 {len(r.text)}"
                  "（可能是登录墙）", file=sys.stderr)
            return []
        blocks = re.split(
            r'<div class="card-wrap" action-type="feed_list_item" mid="', r.text)[1:]
        out = []
        for b in blocks[:15]:
            mid = b[:24].split('"')[0].strip()
            nm = re.search(r'name=([^&"]+)&uid=(\d+)', b)
            nick = unquote(nm.group(1)) if nm else ""
            uid = nm.group(2) if nm else ""
            m = re.search(r'node-type="feed_list_content[^"]*"[^>]*>(.*?)</p>', b, re.S)
            text = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip()[:100] if m else ""
            act = re.search(r'<div class="card-act">(.*?)</ul>', b, re.S)
            counts = re.findall(r"</span>\s*(\d+)</a>", act.group(1)) if act else []
            counts = [int(x) for x in counts]
            repost, comment, like = (counts + [0, 0, 0])[:3]
            if not text:
                continue
            out.append({"platform": "weibo", "item_id": mid, "title": text,
                        "where": nick, "metric": f"{repost}/{comment}/{like}",
                        "url": f"https://weibo.com/{uid}/{mid}" if uid else
                               f"https://s.weibo.com/weibo?q={query}",
                        "create_time": ""})
        return out
    except Exception as e:
        print(f"[warn] 微博搜索 {query} 失败：{type(e).__name__}: {str(e)[:60]}",
              file=sys.stderr)
        return []


# ---------------------------------------------------------------- 落盘
def _append_csv(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not OUT_CSV.exists()
    with OUT_CSV.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def _load_state() -> Dict[str, Any]:
    if AGENT_STATE.exists():
        try:
            return json.loads(AGENT_STATE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"queries": {}, "proposals": []}


def _save_state(st: Dict[str, Any]) -> None:
    AGENT_STATE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 监控提案
def _propose(st: Dict[str, Any], tieba_hits: Dict[str, Counter],
             queries: List[str]) -> List[Dict[str, Any]]:
    """反哺固定爬虫：新吧/新实体 → watchlist 提案（采集器启动时自动并入）。

    - 贴吧：话题同名吧出现即提案；未监控吧 ≥2 帖提案
    - B站/贴吧结果标题里反复出现、且词表认不出的新实体 → 提案加入投稿流关键词
    """
    sys.path.insert(0, str(ROOT / "L1_data_source" / "collectors"))
    from game_terms import get_matcher                      # noqa: E402
    m = get_matcher()

    monitored_forums = {d.name for d in (ROOT / "data" / "raw" / "tieba" / "forums").iterdir()
                        if d.is_dir()}
    known = {p["name"] for p in st.get("proposals", [])}
    exp = (datetime.now(TZ_CN) + timedelta(hours=PROPOSAL_TTL_H)).isoformat(timespec="seconds")
    new = []

    for forum, cnt in tieba_hits.items():
        n = sum(cnt.values())            # cnt 是 query→次数 的 Counter
        if not forum or forum in monitored_forums or forum in known:
            continue
        # ★ 话题同名吧 = 最强扩散信号（"克莱门莎"话题长出了"克莱门莎吧"）——出现即提案；
        #   其他未监控吧单页搜索能到 2 帖已算明显扩散
        if (forum in queries or n >= 2) and _game_gate(forum):
            new.append({"type": "tieba_forum", "name": forum,
                        "reason": f"话题搜索中出现 {n} 帖（未监控吧）"})

    # ★★ 提案闸门（2026-10-06 事故教训）：游戏吧里有人发诺奖帖 → 搜"贾韦国" →
    #   "贾韦国吧"被提案开爬——没有游戏相关性闸门，提案池会被非游戏话题灌满。
    #   规则：提案名必须命中游戏词（含游戏名的组合如"明日方舟内鬼"过，"诺贝尔奖"拒）
    BAD_SUFFIX = re.compile(r"交易|避雷|id$|ＩＤ", re.I)   # 交易/避雷吧=灰产噪声
    def _game_gate(name: str) -> bool:
        if BAD_SUFFIX.search(name):
            return False
        return bool(m.matched_games(name))

    # 新实体：多个查询的搜索结果标题里高频共现的片段，且不是已有游戏词
    bag: Counter = Counter()
    titles = [r.get("title") or "" for r in st.get("_last_results", [])]
    for t in titles:
        for seg in re.findall(r"[「【『]([^」】』]{2,8})[」】』]", t):
            seg = seg.strip()
            if seg and not m.is_game_related(seg) and seg not in known:
                bag[seg] += 1
    for seg, cnt in bag.most_common(5):
        if cnt >= 3 and _game_gate(seg):
            new.append({"type": "bili_keyword", "name": seg,
                        "reason": f"搜索结果标题中 {cnt} 次出现（词表未收录）"})
    # 查询本身成为临时关键词：跨吧扩散 = 话题正在蔓延，值得盯 48h
    for q in queries:
        if q in known:
            continue
        spread = sum(1 for c in tieba_hits.values() if q in c)
        if _game_gate(q) and (len(tieba_hits) >= 2 or spread >= 2):
            new.append({"type": "bili_keyword", "name": q,
                        "reason": f"forming 话题搜索命中 {len(tieba_hits)} 个吧（扩散中）"})

    for p in new:
        p.update({"proposed_at": now_iso(), "expires_at": exp})
    if new:
        st.setdefault("proposals", []).extend(new)
        _save_state(st)
        # 独立 watchlist 文件：固定采集器只读这个（不碰 agent state），过滤未过期项
        unexpired = [x for x in st["proposals"]
                     if x.get("expires_at", "") > now_iso()]
        WATCHLIST.parent.mkdir(parents=True, exist_ok=True)
        WATCHLIST.write_text(json.dumps({"proposals": unexpired},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
    return new


# ---------------------------------------------------------------- 主流程
def run_once(root: Path = ROOT) -> Dict[str, Any]:
    st = _load_state()
    now = time.time()
    # 清理过期冷却（值 = {"t": ts, "hits": n}；兼容旧纯时间戳）
    cleaned = {}
    for q, v in st.get("queries", {}).items():
        t, hits = (v, 0) if isinstance(v, (int, float)) else (v.get("t", 0), v.get("hits", 0))
        ttl = QUERY_COOLDOWN_H * 3600 if hits else ZERO_HIT_COOLDOWN_H * 3600
        if now - t < ttl:
            cleaned[q] = {"t": t, "hits": hits}
    st["queries"] = cleaned

    signals = _forming_signals(root)
    if not signals:
        return {"ok": True, "queries": 0, "note": "无 forming 信号"}

    # ① 信号 → 候选标题（去重）
    seen_titles = set()
    titles: List[str] = []
    for s in signals:
        t = (s.get("title") or "").strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            titles.append(t)

    # ② 查询词提取（LLM 优先，规则兜底）
    llm_q = _llm_extract(titles, st) if titles else {}
    # 候选全收集后做规范形合并："克莱门莎突破"/"克莱门莎技能" → "克莱门莎"（短词优先）
    cands: List[str] = []
    for i, t in enumerate(titles):
        q = (llm_q.get(i) or _rule_extract(t)).strip()
        if q and len(q) >= 2 and q not in st["queries"]:
            cands.append(q)
    cands.sort(key=len)
    queries: List[str] = []
    q_of: Dict[str, List[str]] = defaultdict(list)     # query → 触发源描述
    for q in cands:
        if any(q in kept or kept in q for kept in queries):
            continue
        queries.append(q)
        for t in titles:
            if q in t or q == _rule_extract(t):
                q_of[q].append(t[:20])

    # ③ 执行搜索（贴吧全站 + B站）
    sys.path.insert(0, str(ROOT / "L1_data_source" / "collectors" / "tieba"))
    from crawl_tieba_forum import load_cookie           # noqa: E402
    ck = load_cookie()
    cookie = ck if isinstance(ck, str) else "; ".join(f"{k}={v}" for k, v in (ck or {}).items())

    all_rows: List[Dict[str, Any]] = []
    tieba_forum_hits: Dict[str, Counter] = defaultdict(Counter)
    stamp = now_iso()
    sys.path.insert(0, str(ROOT / "L1_data_source" / "collectors" / "weibo"))
    from crawl_weibo_hot import load_cookie as _wb_cookie            # noqa: E402
    _wck = _wb_cookie()
    wb_cookie = _wck if isinstance(_wck, str) else         "; ".join(f"{k}={v}" for k, v in (_wck or {}).items())
    for q in queries[:8]:                               # 单轮上限 8 个查询，控成本控时长
        hits_tb = _tieba_search(q, cookie)
        time.sleep(1.5)
        hits_bili = _bili_search(q)
        time.sleep(1.5)
        hits_wb = _weibo_search(q, wb_cookie)
        for h in hits_tb + hits_bili + hits_wb:
            h.update({"observed_at": stamp, "trigger_word": q,
                      "query": q, "trigger_type": "forming"})
        all_rows += hits_tb + hits_bili + hits_wb
        for h in hits_tb:
            tieba_forum_hits[h["where"]][q] += 1
        n_hits = len(hits_tb) + len(hits_bili) + len(hits_wb)
        # 冷却分级：有结果 24h（同话题别反复搜）；零结果 2h（可能是瞬时反爬，短冷却重试）
        st["queries"][q] = {"t": now, "hits": n_hits}
        time.sleep(1.5)

    # ④ 落盘（和爬虫一样：append-only 原始数据）
    _append_csv(all_rows)
    st["_last_results"] = all_rows[-120:]               # 供提案抽取，截尾防爆

    # ⑤ 监控提案（反哺固定爬虫）
    proposals = _propose(st, tieba_forum_hits, queries)
    st.pop("_last_results", None)
    _save_state(st)

    return {"ok": True, "signals": len(signals), "queries": queries,
            "results": len(all_rows), "new_proposals": proposals,
            "tieba_forum_spread": {k: sum(v.values()) for k, v in tieba_forum_hits.items()}}


def main() -> int:
    ap = argparse.ArgumentParser(description="搜索型采集 agent（弥补固定爬虫盲区）")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出（调试）")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=1800)
    args = ap.parse_args()

    if args.once or not args.watch:
        r = run_once(ROOT)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str)[:3000])
        return 0

    import sys as _sys
    _here = Path(__file__).resolve().parent
    for _p in (_here, _here.parent, _here.parent.parent):
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
    from robust_watch import run_forever
    return run_forever(name="agent_search",
                       fn=run_once,
                       interval=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
