#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/weibo/crawl_weibotop.py — weibotop.cn 微博热搜历史采集。

★ 为什么用它而不是直接调微博 API（2026-10-05 发现）：
  weibotop.cn 是第三方微博热搜聚合站（7000 万+ 数据点，6 年运行）。
  它比微博自己的 hotSearch API **多三个维度**：
    ① 情绪分类 + 强度（愤怒/惊讶/焦虑/喜悦，0-100）
       → 已做完情绪分析，不用我们自己再跑 LLM
    ② 在榜时长（<1小时 / 3小时 / 14小时）
       → 直接可用于 forming/rising/peaking 判定
    ③ 上榜次数（反复上榜 = 持续性热点）
  加上基础字段：排名 / 热度值 / 关联实体（人/地点/事件）/ 微博链接。

  ⚠ 这是第三方聚合站，数据可能延迟几分钟。适合当「丰富的雷达」，
    微博自己的 API 当「即时验证」。

★ 采集方式：Playwright 渲染页面 → 解析 ARIA 树（Next.js SSR，无独立 API）。
  需要浏览器环境。裸 requests 拿到的 RSC 数据解析不稳定。

用法：
    python crawl_weibotop.py                       # 单轮采集
    python crawl_weibotop.py --watch --interval 600 # 常驻
    python crawl_weibotop.py --games-only           # 只保留游戏相关

输出（data/raw/weibo/）：
  weibotop_hot.csv   热搜快照（含情绪/在榜时长/上榜次数/关联实体）
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
TZ_CN = timezone(timedelta(hours=8))
URL = "https://www.weibotop.cn/"

FIELDS = [
    "observed_at", "rank", "word", "hot_value", "hot_label",
    "emotion", "emotion_intensity", "on_board_duration",
    "on_board_count", "related_entities", "weibo_url",
    "prev_hot_value", "hot_delta", "trend_rate", "is_new",
]


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_game_terms() -> List[str]:
    terms: set[str] = set()
    gdir = ROOT / "games"
    if gdir.is_dir():
        for fn in gdir.glob("*.json"):
            try:
                d = json.loads(fn.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            for k in ("name", "key"):
                if d.get(k):
                    terms.add(str(d[k]).lower())
            for a in (d.get("aliases") or []):
                if a:
                    terms.add(str(a).lower())
    return sorted(terms, key=len, reverse=True)


GAME_TERMS = load_game_terms()


def is_game_related(text: str) -> bool:
    low = (text or "").lower()
    return any(t in low for t in GAME_TERMS)


class WeiboTopCollector:
    """需要浏览器环境（Playwright），从 weibotop.cn 的 SSR 页面解析热搜数据。"""

    def __init__(self, out_dir: str = "data/raw/weibo") -> None:
        self.out_dir = ROOT / out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _parse_snapshot(self, snapshot: str) -> Optional[Dict[str, Any]]:
        """从 ARIA 树的一段文本中解析热搜条目。

        格式示例（来自 domSnapshot）：
          generic: 3
          generic: 中国代表团亚运闭幕式亮相
          link "亚运会": ...
          generic: 69.9万
          generic "在榜时长":
          generic: 3小时
        """
        rank = re.search(r"generic:\s*(\d+)\s*\n", snapshot)
        word = re.search(r"generic:\s*([^\n]+)\n", snapshot)
        # 情绪
        emo = re.search(r'"(\w+)\s*\(强度:\s*(\d+)\)"', snapshot)
        # 热度（xx.x万）
        hot = re.search(r"([\d,.]+万?)\s*\n", snapshot)
        # 在榜时长
        dur = re.search(r"在榜时长.*?generic:\s*(.+)", snapshot, re.DOTALL)
        # 上榜次数
        cnt = re.search(r"上榜次数.*?generic:\s*(\d+)次", snapshot, re.DOTALL)
        # 链接
        url = re.search(r'/url:\s*(\S+weibo\S+)', snapshot)
        # 标签（人/地点/事件）
        tags = re.findall(r'generic:\s*([^\n]{1,12})\n', snapshot)

        if not rank or not word:
            return None
        return {
            "rank": int(rank.group(1)),
            "word": word.group(1).strip()[:50],
            "emotion": emo.group(1) if emo else "",
            "emotion_intensity": int(emo.group(2)) if emo else 0,
            "hot_value": hot.group(1).strip() if hot else "",
            "on_board_duration": dur.group(1).strip()[:12] if dur else "",
            "on_board_count": int(cnt.group(1)) if cnt else 1,
            "weibo_url": url.group(1).strip() if url else "",
            "related_tags": [t.strip() for t in tags[:3] if len(t.strip()) > 1][:3],
        }

    # ---------- Playwright 抓取（需要浏览器） ----------
    def collect_via_browser(self) -> Dict[str, Any]:
        """用浏览器渲染页面并解析热搜数据。返回结构化列表。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"error": "需要 playwright：pip install playwright && playwright install chromium"}

        stamp = now_iso()
        items: List[Dict[str, Any]] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 Chrome/138.0.0.0")
            page.goto(URL, timeout=30000)
            page.wait_for_timeout(3000)  # 等 JS 渲染

            # 从 DOM 提取每个热搜条目
            entries = page.evaluate("""() => {
                const results = [];
                // 微博热搜条目通常是 heading(h2/h3) + 后面的兄弟元素
                const headings = document.querySelectorAll('h2');
                for (const h of headings) {
                    const text = h.textContent?.trim();
                    if (!text || text.length < 2) continue;
                    const parent = h.closest('[class]');
                    if (!parent) continue;
                    const parentText = parent.textContent || '';
                    // 找热度（xx.x万 或 纯数字）
                    const hotMatch = parentText.match(/([\\d.]+万?)\\s*$/);
                    // 找情绪
                    const emoMatch = parentText.match(/(愤怒|惊讶|焦虑|喜悦|悲伤|恐惧|厌恶)\\s*\\(强度:\\s*(\\d+)\\)/);
                    // 找在榜时长
                    const durMatch = parentText.match(/在榜时长[\\s\\S]*?(\\d+小时|<1小时)/);
                    // 找上榜次数
                    const cntMatch = parentText.match(/上榜次数[\\s\\S]*?(\\d+)次/);
                    // 找微博链接
                    const link = parent.querySelector('a[href*="s.weibo.com"]');
                    // 找标签（人/地点/事件）
                    const tags = [...parent.querySelectorAll('a[href*="/celebrity/"], a[href*="/city/"], a[href*="/country/"], a[href*="/search?q="]')]
                        .map(a => a.textContent?.trim()).filter(t => t && t.length > 1 && t.length < 15);
                    results.push({
                        rank: results.length + 1,
                        word: text,
                        hot: hotMatch ? hotMatch[1] : '',
                        emotion: emoMatch ? emoMatch[1] : '',
                        emotion_intensity: emoMatch ? parseInt(emoMatch[2]) : 0,
                        duration: durMatch ? durMatch[1] : '',
                        count: cntMatch ? parseInt(cntMatch[1]) : 1,
                        url: link ? link.href : '',
                        tags: [...new Set(tags)].slice(0, 3),
                    });
                }
                return results;
            }""")
            browser.close()

        for e in entries:
            e["observed_at"] = stamp
            e["game_related"] = is_game_related(e.get("word", "") + " " + e.get("tags", ""))

        self._write(entries, stamp)
        game_hits = [e["word"] for e in entries if e["game_related"]]
        return {"observed_at": stamp, "total": len(entries),
                "game_related": len(game_hits), "game_words": game_hits[:15]}

    # ---------- 纯 requests 降级（从 HTML 里解析 RSC 数据，不稳定但零依赖） ----------
    def collect_via_requests(self) -> Dict[str, Any]:
        import requests
        stamp = now_iso()
        r = requests.get(URL, headers={"User-Agent": UA}, timeout=15)
        chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', r.text, re.DOTALL)
        full = "".join(chunks).replace("\\u002F", "/")

        # 提取模式：中文词 + 热度值（万为单位，≥10万）
        pairs = re.findall(
            r'"([\u4e00-\u9fffA-Za-z0-9]{2,30})"\s*,\s*"?([\d.]+万)"?', full)
        seen = set()
        items = []
        for w, h in pairs:
            if w in seen or len(w) < 2:
                continue
            seen.add(w)
            hot = float(h.replace("万", "")) * 10000 if "万" in h else float(h)
            if hot < 100000:
                continue
            items.append({"rank": len(items) + 1, "word": w,
                         "hot_value": f"{hot/10000:.1f}万", "observed_at": stamp})

        game_hits = [i["word"] for i in items if is_game_related(i["word"])]
        self._write(items, stamp)
        return {"observed_at": stamp, "total": len(items),
                "game_related": len(game_hits), "game_words": game_hits[:10],
                "note": "requests 降级模式（无浏览器），字段不完整"}

    def _write(self, items: List[Dict[str, Any]], stamp: str) -> None:
        path = self.out_dir / "weibotop_hot.csv"
        new = not path.exists()
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            for r in items:
                r["observed_at"] = stamp
                w.writerow({k: r.get(k, "") for k in FIELDS})


def main() -> int:
    ap = argparse.ArgumentParser(description="weibotop.cn 微博热搜历史采集（含情绪分析/在榜时长）")
    ap.add_argument("--out-dir", default="data/raw/weibo")
    ap.add_argument("--games-only", action="store_true")
    ap.add_argument("--sleep", type=float, default=2.0)
    args = ap.parse_args()

    c = WeiboTopCollector(args.out_dir)
    # 先试浏览器方式（数据完整），失败降级到 requests
    try:
        r = c.collect_via_browser()
        if r.get("error"):
            raise RuntimeError(r["error"])
        print(json.dumps(r, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[warn] 浏览器采集失败（{str(e)[:60]}），降级到 requests 模式")
        r = c.collect_via_requests()
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())