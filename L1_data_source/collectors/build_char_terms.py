#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/build_char_terms.py —— 游戏专有名词词典（角色/活动/皮肤层）。

★ 为什么需要（2026-10-06 缺口③）：
  词表 10774 个游戏名判不了「公孙离」「盖伦」「遐蝶」「瑞秋儿」——
  B站游戏区视频标题大量只有角色名没有游戏名，全部漏判。
  语义召回 + LLM 三级分级能兜底，但召回率没量化过；先把高频专有名词
  直接进词表（免费、确定），剩下的长尾才交给语义层。

★ 流程：B站标题 → 高频 CJK 片段（≥5 次）→ LLM 批量判定是否游戏专有名词
  → data/state/char_terms.json → game_terms.py 加载为词表第四层。

★ 评估：B站 game_ranking 来源的视频 100% 是游戏内容（榜单定义），
  就是现成的 ground truth —— 标题判定召回率可直接算。

用法：
    python build_char_terms.py --run       # 抽取+LLM判定+落盘
    python build_char_terms.py --eval      # B站标题召回率 before/after
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "L1_data_source" / "collectors"))
from game_terms import get_matcher            # noqa: E402

OUT = ROOT / "data" / "state" / "char_terms.json"
CAND_CACHE = ROOT / "data" / "state" / "_char_candidates.json"
BILI_CSV = ROOT / "data" / "raw" / "bilibili" / "hot_videos.csv"

NOISE = {"直播回放", "完整版", "快来围观", "请选择", "穿搭", "合集", "视频", "解说",
         "排名", "攻略", "教程", "盘点", "黑话", "漫画", "动画", "短片", "预告"}


def extract(min_freq: int = 5) -> list:
    m = get_matcher()
    rows = list(csv.DictReader(open(BILI_CSV, encoding="utf-8-sig", newline="")))
    # ★ 按 bvid 去重：快照每轮都记同一视频，不去重的话一个视频的标题碎片
    #   会被计 95 次（轮数），频次完全失真 —— 一个视频只投一票
    seen_bv = set()
    bag: Counter = Counter()
    for r in rows:
        bv = r.get("bvid") or ""
        if bv and bv in seen_bv:
            continue
        if bv:
            seen_bv.add(bv)
        t = re.sub(r"</?em[^>]*>", "", r.get("title") or "")
        segs = re.findall(r"[【「『《]([^】」』》]{2,16})[】」』》]", t)
        segs += [s for s in re.split(r"[\s|｜/，,。！!？?~～\-—:：]+", t)
                 if 2 <= len(s) <= 8]
        for seg in segs:
            seg = seg.strip("·…—-[]()（）】【 ")
            if not (2 <= len(seg) <= 8) or not re.search(r"[\u4e00-\u9fff]", seg):
                continue
            if re.search(r"[\da-zA-Z]", seg):
                continue
            if seg in NOISE or m.is_game_related(seg):
                continue
            bag[seg] += 1
    return [(w, c) for w, c in bag.most_common(500) if c >= min_freq]


def llm_judge(cands: list) -> dict:
    """LLM 批量判定。返回 {词: {"type": ..., "freq": n}}，只收判定为专有名词的。"""
    sys.path.insert(0, str(ROOT / "L4_intelligence" / "intelligence"))
    from llm import ModelRouter
    router = ModelRouter(enabled=True)
    model = router.resolve(node="char_terms").get("model")
    # ★ 用非思考型免费模型：思考型（nemotron）在批量判定上会把 token 全烧在
    #   思考里返回空 content（2026-10-06 实测 7 块全挂）。ling-flash 轻快够用。
    model = "inclusionai/ling-3.1-flash"
    if not model:
        print("[error] 无可用模型")
        return {}
    accepted: dict = {}
    CHUNK = 60
    MODELS = ["inclusionai/ling-3.1-flash", "dots-studio/dots-3-note-preview:free",
              "thinkingmachines/inkling-small:free"]      # 429 时轮换
    import time
    for i in range(0, len(cands), CHUNK):
        chunk = cands[i: i + CHUNK]
        lines = "\n".join(f"{j}. {w}（出现{n}次）" for j, (w, n) in enumerate(chunk))
        prompt = (
            "下面是从B站游戏区视频标题抽出的高频片段。判断每个是否为「游戏专有名词」：\n"
            "算：游戏角色/英雄/干员名、活动名、版本名、关卡名、皮肤名、武器装备名、游戏简称。\n"
            "不算：日常语句、泛指词（射手/大合集/完整版）、非游戏专有名词。\n"
            '只输出 JSON 数组：[{"w": "原词", "ok": true, "type": "角色|活动|版本|皮肤|武器|简称"}, ...]\n\n'
            + lines)
        got = None
        for mi, model in enumerate(MODELS):
            try:
                out = router.chat(model=model,
                                  messages=[{"role": "user", "content": prompt}],
                                  max_tokens=4000, temperature=0.1, json_mode=True)
                got = out
                break
            except Exception as e:
                print(f"[warn] 块{i // CHUNK} 模型{model.split('/')[-1]}失败：{str(e)[:50]}")
                time.sleep(20)
        if got is None:
            print(f"[warn] 判定块 {i // CHUNK} 全模型失败，跳过")
            continue
        try:
            out = got
            arr = json.loads(out.get("content") or "[]")
            if isinstance(arr, dict):
                arr = next((v for v in arr.values() if isinstance(v, list)), [])
            for x in arr:
                if isinstance(x, dict) and x.get("ok") and x.get("w"):
                    w = str(x["w"]).strip()
                    freq = dict(chunk).get(w, 0)
                    if w:
                        accepted[w] = {"type": str(x.get("type", "其他")),
                                       "freq": freq}
        except Exception as e:
            print(f"[warn] 判定块 {i // CHUNK} 解析失败：{type(e).__name__}: {str(e)[:50]}")
        time.sleep(30)          # 免费档限流敏感，块间强制间隔
    return accepted


def run(rule_only: bool = False) -> None:
    cands = extract()
    accepted: dict = {}
    # ★ 规则层：≥5 个不同视频共用同一片段 = 实体信号（bvid 已去重，频次可信）。
    #   LLM 判定（免费模型限流/思考空回，极不稳定）降级为可选增强，收进来的
    #   全部标 rule@待审 —— 人扫一眼 30 个词比调模型快得多。
    for w, c in cands:
        if c >= 5:
            accepted[w] = {"type": "待审", "freq": c}
    n_rule = len(accepted)
    print(f"规则层收录 {n_rule} 个（≥5 视频）")
    if not rule_only and cands:
        accepted.update(llm_judge(cands))
    # 合并旧词典（LLM 挂了的块不丢历史）
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("chars", {})
            for w, v in old.items():
                accepted.setdefault(w, v)
        except (ValueError, OSError):
            pass
    OUT.write_text(json.dumps({"chars": accepted, "total": len(accepted),
                               "rule_count": n_rule},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"词典落盘 {len(accepted)} 个（规则 {n_rule} + LLM 增判 "
          f"{len(accepted) - n_rule}）→ {OUT}")
    for w, v in sorted(accepted.items(), key=lambda x: -x[1]["freq"])[:30]:
        print(f"  {w}  [{v['type']}]  {v['freq']}视频")


def eval_recall() -> None:
    """B站 game_ranking 标题 = 全是游戏内容（榜单定义）→ 直接算标题判定召回率。"""
    import importlib
    sys.path.insert(0, str(ROOT / "L1_data_source" / "collectors"))
    import game_terms
    importlib.reload(game_terms)
    m = game_terms.get_matcher(force_reload=True)
    rows = [r for r in csv.DictReader(open(BILI_CSV, encoding="utf-8-sig", newline=""))
            if r.get("source") == "game_ranking" and r.get("title")]
    titles = {t.strip("<em>").strip() for t in
              (re.sub(r"</?em[^>]*>", "", r["title"]) for r in rows)}
    hit = sum(1 for t in titles if m.is_game_related(t))
    print(f"B站 game_ranking 标题判定召回率: {hit}/{len(titles)} = "
          f"{100 * hit / max(len(titles), 1):.1f}%（ground truth=全部是游戏内容）")
    miss = [t for t in sorted(titles) if not m.is_game_related(t)][:15]
    print("仍漏判样本:")
    for t in miss:
        print(f"   {t[:52]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="游戏专有名词词典")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--rule-only", action="store_true", help="跳过 LLM 判定")
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()
    if args.run:
        run(rule_only="--rule-only" in sys.argv)
    if args.eval:
        eval_recall()
    if not args.run and not args.eval:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
