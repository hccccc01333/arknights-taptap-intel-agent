#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_trend/forming_report.py —— 「正在形成」的外部热点报告（可离线跑）。

★ 定位：热榜是**存量榜**（词上榜时通常已过峰值），本报告只报
  "正在形成"的（刚进榜 / 热度在涨 / 排名在升），并按游戏相关性过滤。
  这是热点**追踪**的最小可交付形态 —— 每轮都重新判定，不是一次性摘要。

★ 只用免费能力（规则 + 统计），不调 LLM —— 定时任务随时可能跑，
  不该依赖需要 key 的模型。LLM 精判走 hotspot_filter（需要时手动跑）。

用法：
    python L3_trend/forming_report.py            # 打印文本报告
    python L3_trend/forming_report.py --json     # 机器可读
    python L3_trend/forming_report.py --min-rounds 3   # 数据不够就明确说不够
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from forming import analyze_all  # noqa: E402

MIN_ROUNDS_FOR_TREND = 3      # 少于这个轮数，趋势判断不可信


_BD = None    # baidu 采集模块缓存 —— 里面是 AC 自动机，构建不便宜，只做一次


def game_related(text: str) -> bool:
    """游戏相关性（词表层，免费）。与采集器同源。"""
    global _BD
    if _BD is None:
        import importlib.util
        try:
            spec = importlib.util.spec_from_file_location(
                "bd", os.path.join(_ROOT, "L1_data_source", "collectors", "baidu", "crawl_baidu_hot.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _BD = mod
        except Exception:
            _BD = False
    if _BD is False:
        low = (text or "").lower()
        return any(k in low for k in ("游戏", "手游", "steam", "联动", "抽卡", "公测", "arknights", "明日方舟", "鸣潮"))
    return _BD.is_game_related(text)


def build(root: str = _ROOT, min_rounds: int = MIN_ROUNDS_FOR_TREND) -> Dict[str, Any]:
    res = analyze_all(root)
    report: Dict[str, Any] = {"generated_at": None, "channels": {}, "ready": False,
                              "insufficient": []}
    all_forming: List[Dict[str, Any]] = []
    for plat, r in res.items():
        # ★ 归一化：热榜条目是 (rank, word, trend_rate, on_board_min)，
        #   贴吧帖子是 (reply_count, title, label, forum) —— 字段名完全不同。
        #   统一成 (rank, word, rate, extra)，渲染层只写一份，不用到处 if。
        forming: List[Dict[str, Any]] = []
        for f in (r.get("forming") or [])[:30]:
            if f.get("samples") is not None:        # B站投稿流（关键词级）
                rate = f.get("label") or ""
                rank = f"{f.get('new_now', 0)}投/轮"
                extra = "；".join((f.get("samples") or [])[:2])
            elif f.get("forum"):                    # 贴吧帖子（来自游戏吧，按构造就是游戏相关）
                rate = f.get("label") or ""
                rank = f"{f.get('reply_count', 0)}评"
                extra = f"{f.get('forum','')}吧 · {f.get('activity_time','')}"
            elif f.get("word"):                     # 热榜
                rate = (f"{f['trend_rate']:+.1f}%" if f.get("trend_rate") is not None
                        else "新进榜")
                rank = f"#{f.get('rank')}"
                extra = f"在榜{f.get('on_board_min')}分"
            else:                                    # 贴吧帖子
                rate = f.get("label") or ""
                rank = f"{f.get('reply_count')}评"
                extra = f"{f.get('forum','')}吧 · {f.get('activity_time','')}"
            _w = f.get("word") or f.get("title") or ""
            forming.append({"rank": rank, "word": _w,
                            "rate": rate, "stage": f.get("stage"), "extra": extra,
                            "trend_rate": f.get("trend_rate"),
                            # 贴吧条目来自游戏吧 → 按构造就是游戏相关（标题里往往没游戏名）
                            "game_related": True if f.get("forum") else game_related(_w)})
        entry = {
            "rounds": r.get("rounds") or 0,
            "interval_min": r.get("interval_min"),
            "stages": r.get("stages"), "note": r.get("note"),
            "forming": forming,
            "extra_info": {k: r.get(k) for k in
                           ("posts", "forums", "items", "by_source", "new_count")
                           if r.get(k) is not None},
        }
        report["channels"][plat] = entry
        if (r.get("rounds") or 0) < min_rounds:
            report["insufficient"].append(
                f"{plat}: {r.get('rounds') or 0} 轮（需 ≥{min_rounds}）")
            continue
        report["ready"] = True
        for f in forming:
            g = dict(f)
            g["platform"] = plat
            all_forming.append(g)

    # 排序：游戏相关优先，其次涨幅，再其次排名
    all_forming.sort(key=lambda x: (0 if x["game_related"] else 1,
                                    -(x.get("trend_rate") or 0), x.get("rank") or 999))
    report["forming_all"] = all_forming[:25]
    report["forming_game"] = [x for x in all_forming if x["game_related"]][:15]
    from datetime import datetime, timezone, timedelta
    report["generated_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    return report


def to_text(r: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("=" * 62)
    L.append(f"外部热点 · 正在形成  |  生成于 {r['generated_at']}")
    L.append("=" * 62)
    for plat, c in r["channels"].items():
        stages = c.get("stages") or {}
        s = " ".join(f"{k}:{v}" for k, v in stages.items()) or "无"
        rounds = c.get("rounds")
        L.append(f"\n【{plat}】{rounds or 0} 轮 / 间隔 {c.get('interval_min')} 分钟  [{s}]")
        if c.get("note"):
            L.append(f"  ⚠ {c['note']}")
        if c.get("extra_info"):
            L.append(f"  {c['extra_info']}")
        for f in (c.get("forming") or [])[:6]:
            mark = "★游戏" if f.get("game_related") else "  "
            L.append(f"  {mark} {str(f.get('rank','-')):<7} {str(f.get('word',''))[:20]:<22} "
                     f"{str(f.get('rate','')):>9}  {f.get('extra','')}")
    if r.get("forming_game"):
        L.append("\n" + "-" * 62)
        L.append("★ 与游戏相关的「正在形成」：")
        for f in r["forming_game"]:
            L.append(f"  [{f.get('platform','')}] {str(f.get('word',''))[:26]:<28} "
                     f"{str(f.get('rate','')):>9}  {f.get('extra','')}")
    if not r.get("ready"):
        L.append("\n[数据不足] " + "；".join(r.get("insufficient", [])))
        L.append("  → 热榜趋势需要跨轮对比，请让采集器多跑几轮再判断。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="外部热点形成情况报告")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-rounds", type=int, default=MIN_ROUNDS_FOR_TREND)
    ap.add_argument("--out", default="", help="同时写到指定 json 文件")
    ap.add_argument("--watch", action="store_true",
                    help="常驻：定时刷新 data/state/forming_report.json（webapp 读这份）")
    ap.add_argument("--interval", type=int, default=600)
    args = ap.parse_args()

    if args.watch:
        os.makedirs(os.path.dirname(_ROOT + "/data/state") or ".", exist_ok=True)
        out_path = args.out or os.path.join(_ROOT, "data", "state", "forming_report.json")

        def _tick():
            rr = build(_ROOT, args.min_rounds)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(rr, f, ensure_ascii=False, indent=2)
            print(f"[forming] 报告已刷新（{rr['generated_at']}，"
                  f"游戏 forming {len(rr.get('forming_game') or [])} 条）", flush=True)

        import sys as _sys
        _here = Path(_ROOT) / "L1_data_source"
        for _p in (_here, _here / "collectors"):
            if str(_p) not in _sys.path:
                _sys.path.insert(0, str(_p))
        from robust_watch import run_forever
        return run_forever(name="forming_report", fn=_tick, interval=args.interval)

    r = build(_ROOT, args.min_rounds)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(to_text(r))
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())