#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/tieba/crawl_tieba_forum.py — 贴吧**游戏吧内帖子**采集。

★ 为什么（2026-10-05 实测结论）：
  贴吧的**全站热点榜**（hottopic/browse/topicList）无需登录，但**kw 参数无效**
  —— 试了明日方舟/原神/崩铁/蛋仔/光遇/三角洲六个吧，返回的都是同一批全站内容。
  要进具体游戏吧，必须**登录态**（BDUSS cookie）。

  有了登录态后（实测）：
    无 cookie → /f?kw=xxx      403
    有 cookie → /f?kw=xxx      200，但只有 11KB 骨架页（内容靠 JS）
    有 cookie → /mo/q/m?kw=xxx 200，**400KB 完整 SSR 内容** ← 用这个

★ 为什么贴吧值得接：
  贴吧游戏吧是国内玩家讨论最密集的地方之一，结构与 TapTap 同构：
    帖子（观点）+ 回帖（讨论）+ 回帖数（热度）。
  而且**发帖时间精确到分钟**，比 TapTap 的天级时间粒度细得多。

★ 采集边界（用户 2026-10-05 定）：
  ✅ 帖子标题 + 回帖数（列表页，一轮 30 条/吧）
  ⚠️ 帖子正文：**默认不采**。贴吧 2026-10 改版后，
     网页版 /p/<id> 是 JS 空壳（7-11KB），移动端 /mo/q/p 返回的是**吧列表页**而非正文。
     已试过 /mo/q/newmoindex（返回"点赞我的吧"）、/f/commit/bawiki/getPostList（空）、
     浏览器访问（触发滑块验证）。**当前没有稳定的免验证正文路径**，
     所以默认只抓标题+回帖数；--with-content 可试探，但多数会返回空。
  ❌ 楼中楼回复（不采）

  理由：标题+回帖数已足够做热点识别（"爱国者参与侵略战争" 本身就是信号）。
  正文接口一旦稳定（贴吧改版或用登录态的小程序接口），打开 --with-content 即可。

★ 登录态配置：L1_data_source/collectors/tieba/.env 的 TIEBA_COOKIE（已 gitignore）
  获取：浏览器登录 tieba.baidu.com → F12 → Network → 任一请求 → 复制整行 Cookie

用法：
    python crawl_tieba_forum.py --kw 明日方舟
    python crawl_tieba_forum.py --watch --interval 900 --games 明日方舟,原神,蛋仔派对

输出：data/raw/tieba/forums/<游戏名>/posts.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import html
from urllib.parse import unquote

import requests

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_DIR = Path(__file__).resolve().parent
TZ_CN = timezone(timedelta(hours=8))

MOBILE_URL = "https://tieba.baidu.com/mo/q/m"
FNAME_API = "https://tieba.baidu.com/f/commit/share/fnameShareApi"
UA_MOBILE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
             "Referer": "https://tieba.baidu.com/"}

FIELDS = ["observed_at", "forum", "post_id", "title", "content", "reply_count",
          "activity_time", "first_seen_at", "rounds_seen", "url", "is_pinned"]


def now_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_cookie() -> str:
    """TIEBA_COOKIE：登录态（BDUSS）。没有就退化成只读全站榜。"""
    try:
        from dotenv import load_dotenv
        load_dotenv(CRAWLER_DIR / ".env")
    except ImportError:
        p = CRAWLER_DIR / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "TIEBA_COOKIE":
                    os.environ.setdefault(k.strip(), v.strip())
    return os.environ.get("TIEBA_COOKIE", "").strip()


def _headers(cookie: str) -> Dict[str, str]:
    h = dict(UA_MOBILE)
    if cookie:
        h["Cookie"] = cookie
    return h


def resolve_fid(kw: str, cookie: str) -> int:
    """游戏名 → 吧 fid（这个接口无需登录）。"""
    r = requests.get(FNAME_API, params={"ie": "utf-8", "fname": kw},
                     headers=_headers(cookie), timeout=15)
    try:
        return int(((r.json() or {}).get("data") or {}).get("fid") or 0)
    except (ValueError, TypeError):
        return 0


def fetch_forum(kw: str, cookie: str, page: int = 1) -> List[Dict[str, Any]]:
    """抓一个吧的帖子列表（SSR HTML → 解析 /p/<id> 链接）。"""
    fid = resolve_fid(kw, cookie)
    params: Dict[str, Any] = {"kw": kw}
    if fid:
        params["kz"] = fid
    if page > 1:
        params["pn"] = page
    r = requests.get(MOBILE_URL, params=params, headers=_headers(cookie), timeout=25)
    r.raise_for_status()
    text = r.text
    if "安全验证" in text:
        raise RuntimeError("触发百度安全验证（滑块）——登录态可能已过期")

    out: List[Dict[str, Any]] = []
    seen = set()
    # ★ ti_time（最后活动时间）在这个 <a>/p/<id> 链接的**外面**（属于上一帖的块尾），
    #   所以不能只在 <a>...</a> 内部找 —— 改成就近取：找该 pid 之后最近的一个 ti_time。
    all_times = [(m.start(), m.group(1).strip())
                 for m in re.finditer(r'class="ti_time"[^>]*>([^<]+)<', text)]
    def _time_near(pos: int) -> str:
        after = [t for p, t in all_times if p > pos]
        return after[0] if after else ""

    for m in re.finditer(r'<a[^>]+href="([^"]*?/p/(\d+)[^"]*)"[^>]*>(.*?)</a>',
                         text, re.DOTALL):
        url, pid, inner = m.group(1), m.group(2), m.group(3)
        if pid in seen:
            continue
        title = " ".join(
            re.sub(r"<[^>]+>", " ", inner).split())
        title = re.sub(r"^(置顶|精华|热帖)\s*", "", title).strip()
        if len(title) < 2:
            continue
        seen.add(pid)
        if url.startswith("http"):
            full_url = url
        elif url.startswith("/"):
            full_url = "https://tieba.baidu.com" + url
        else:
            full_url = f"https://tieba.baidu.com/p/{pid}"
        # ti_time = 楼主最后活动时间（HH:MM）。★ 不是发帖时间，见文件头说明
        activity_time = _time_near(m.start())
        # 标题尾部常带回帖数（"标题 111"）——它是帖子级热度的直接值
        m = re.search(r"\s+(\d{1,5})$", title)
        reply_count = int(m.group(1)) if m else 0
        if m:
            title = title[:m.start()].strip()
        out.append({
            "post_id": pid,
            "title": title[:100],
            "reply_count": reply_count,
            "activity_time": activity_time,
            "url": full_url,
            "is_pinned": "true" if "置顶" in inner else "false",
        })
    return out


POST_URL = "https://tieba.baidu.com/mo/q/p"


def fetch_post_content(post_id: str, kw: str, fid: int, cookie: str,
                       sleep: float = 2.5, max_chars: int = 800) -> str:
    """抓单个帖子正文。

    ★ 关键（实测踩坑记录）：
      - 网页版 /p/<id> 是 JS 空壳（7KB~11KB），拿不到正文
      - **移动版 /mo/q/p?pid=<id>&kw=<吧名>&kz=<fid> 返回 400KB 完整 SSR**
      - 连续请求会被限流断连（ConnectionReset），**必须限速**（默认 2.5s）
      - 只取首帖正文（楼主），不取回复（回复不采 —— 用户 2026-10-05 定）
    """
    time.sleep(sleep)                       # 限速：贴吧对密集请求直接断连
    r = requests.get(POST_URL,
                     params={"pid": post_id, "kw": kw, "kz": fid, "spm": 1},
                     headers=_headers(cookie), timeout=25)
    r.raise_for_status()
    text = r.text
    if "安全验证" in text:
        raise RuntimeError("触发百度安全验证（登录态可能过期）")
    for pat in (r'<div[^>]+class="d-content-first"[^>]*>(.*?)</div>',
                r'class="d-content"[^>]*>(.*?)</div>',
                r'class="[^"]*content-first[^"]*"[^>]*>(.*?)</div>'):
        m = re.search(pat, text, re.DOTALL)
        if m:
            body = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
            body = " ".join(body.split())
            if len(body) > 10:
                return body[:max_chars]
    # ★ 实测结论（2026-10-05，逐一验证过）：
    #   网页版 /p/<id>              → JS 空壳 7-11KB，无正文
    #   桌面版 /p/<id>?see_lz=1     → 同样空壳
    #   移动版 /mo/q/p?pid=&kw=&kz=  → 200 但 366-414KB 里**不含正文结构**
    #                                  （无 d_post_content / pb-content-wrap）
    #   /mo/q/newmoindex?pid=        → 返回"点赞我的吧"列表
    #   桌面 UA + 移动路径            → 同样无正文结构
    #   浏览器访问                    → 触发百度滑块验证
    #
    #   结论：贴吧对**不执行 JS 的客户端返回降级页面**，正文需要完整浏览器环境。
    #   在无头/脚本客户端里拿不到 —— 这是服务端策略，不是选择器写错。
    #   所以返回空，**不猜、不伪造正文**。
    #
    #   若日后需要正文，两个可行方向：
    #     ① 改用 Playwright 真实浏览器（带 JS 执行 + 完整指纹）逐帖打开
    #     ② 走贴吧小程序接口（需小程序侧的签名/登录态）
    return ""


def collect(games: List[str], cookie: str, with_content: bool = False,
            content_top: int = 10, content_sleep: float = 3.0) -> Dict[str, Any]:
    # ★ 每轮重读 agent 提案（原来只在 main() 读一次——采集器长跑几天
    #   不重启，新吧提案要等几天才生效。现在下一轮就开爬）
    try:
        _wl = json.loads((ROOT / "data" / "state" / "agent_watchlist.json")
                         .read_text(encoding="utf-8"))
        for x in _wl.get("proposals", []):
            if (x.get("type") == "tieba_forum"
                    and x.get("expires_at", "") > datetime.now(TZ_CN).isoformat(timespec="seconds")
                    and x.get("name") not in games):
                games.append(x["name"])
                print(f"[watchlist] 本轮并入 agent 提案吧：{x['name']}", flush=True)
    except Exception:
        pass
    if not cookie:
        print("[warn] 无 TIEBA_COOKIE，贴吧吧内帖子需登录态；"
              "请配置 L1_data_source/collectors/tieba/.env", file=sys.stderr)
        return {"ok": False, "reason": "no_cookie"}

    stamp = now_iso()
    total, detail = 0, {}
    for kw in games:
        try:
            posts = fetch_forum(kw, cookie)
        except Exception as e:
            print(f"[warn] {kw} 失败：{type(e).__name__}: {str(e)[:60]}", file=sys.stderr)
            detail[kw] = f"失败: {str(e)[:40]}"
            continue
        if not posts:
            detail[kw] = "0 条"
            continue
        d = ROOT / "data" / "raw" / "tieba" / "forums" / kw
        d.mkdir(parents=True, exist_ok=True)
        path = d / "posts.csv"
        # ★ first_seen：我们**第一次**看到这个帖的时刻。
        #   列表页只给 ti_time（最后活动时间，HH:MM），给不了发帖时间（见文件头），
        #   所以"这个帖是不是刚发的"只能靠**跨轮对比**：第一次见到 = 新出现。
        first_seen: Dict[str, str] = {}
        seen_count: Dict[str, int] = {}
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    pid_ = r.get("post_id", "")
                    if not pid_:
                        continue
                    first_seen.setdefault(pid_, r.get("first_seen_at")
                                          or r.get("observed_at") or "")
                    try:
                        seen_count[pid_] = int(r.get("rounds_seen") or 0)
                    except ValueError:
                        seen_count[pid_] = 0
        known = set(first_seen)
        fresh = [p for p in posts if p["post_id"] not in known]
        for p in posts:
            p["first_seen_at"] = first_seen.get(p["post_id"], stamp)
            p["rounds_seen"] = seen_count.get(p["post_id"], 0) + 1
        # ★ 写全量快照（不是只写新增）：posts.csv 只反映"现在"，供详情/人工查看。
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for p in posts:
                p.update({"observed_at": stamp, "forum": kw})
                w.writerow({k: p.get(k, "") for k in FIELDS})
        # ★ 同时追加历史流（append-only，2026-10-05 补）：
        #   快照每轮覆盖，回帖数的时间序列只存在于这里 ——
        #   forming 的"回帖增速"判据完全依赖它（同 B站 hot_videos 的 append 模式）。
        hpath = d / "history.csv"
        if not hpath.exists():
            with hpath.open("w", encoding="utf-8-sig", newline="") as f:
                csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore").writeheader()
        with hpath.open("a", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            for p in posts:
                w.writerow({k: p.get(k, "") for k in FIELDS})
        # 正文：只抓每吧回帖数最高的 N 个（省请求、防限流）
        n_body = 0
        if with_content:
            fid = resolve_fid(kw, cookie)
            targets = [p for p in fresh if p.get("reply_count")] or fresh
            for p in sorted(targets, key=lambda x: -int(x.get("reply_count") or 0))[:content_top]:
                try:
                    p["content"] = fetch_post_content(p["post_id"], kw, fid, cookie,
                                                        sleep=content_sleep)
                    if p["content"]:
                        n_body += 1
                except Exception as e:
                    print(f"[warn] {kw}/{p['post_id']} 正文失败：{str(e)[:50]}",
                          file=sys.stderr)
            # 补写（正文拿到后重写这些行）
            with path.open("a", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
                for p in targets[:content_top]:
                    if p.get("content"):
                        p.setdefault("observed_at", stamp)
                        p.setdefault("forum", kw)
                        w.writerow({k: p.get(k, "") for k in FIELDS})

        detail[kw] = f"{len(posts)} 条（新增 {len(fresh)}，正文 {n_body}）"
        total += len(posts)
        top = sorted(posts, key=lambda x: -int(x.get("reply_count") or 0))[:3]
        detail[kw] += " | 最热: " + "; ".join(
            f"{p['title'][:18]}({p['reply_count']}评)" for p in top)
    return {"ok": True, "observed_at": stamp, "forums": detail, "total": total}



def _single_instance_lock(marker: str) -> bool:
    """★ 单实例锁：已有同脚本 --watch 实例在跑就退出（防人工重启与看门狗
    自动重启赛跑产生双实例——双实例会双写 CSV + 双倍请求（2026-10-06 事故）。"""
    import psutil
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (not (p.info["name"] or "").lower().startswith("python")
                    or p.info["pid"] == me):
                continue
            cl = " ".join(p.info["cmdline"] or [])
            if marker in cl and "--watch" in cl:
                print(f"[lock] 已有 watch 实例 pid={p.info['pid']}，本实例退出")
                return False
        except Exception:
            pass
    return True

def main() -> int:
    ap = argparse.ArgumentParser(description="贴吧游戏吧内帖子采集（需登录态）")
    ap.add_argument("--games", default="",
                    help="游戏名（逗号分隔，对应吧名）；留空 = 爬 data/raw/tieba/forums 下全部已有吧")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=900)
    ap.add_argument("--with-content", action="store_true",
                    help="额外抓正文（默认关闭：贴吧正文路由改版，当前多数取不到）")
    ap.add_argument("--content-top", type=int, default=10,
                    help="每吧抓正文的前 N 个（按回帖数排序，默认 10）")
    ap.add_argument("--content-sleep", type=float, default=2.5,
                    help="正文请求间隔秒（贴吧限流敏感，别低于 2）")
    args = ap.parse_args()

    games = [g.strip() for g in args.games.split(",") if g.strip()]
    if not games:
        # ★ 默认 = 已有的吧目录（人工维护的游戏名单，2026-10-06 修正：
        #   原默认只有"明日方舟"，watch 重启不带 --games 时 29 个吧悄悄缩成 1 个）
        fdir = ROOT / "data" / "raw" / "tieba" / "forums"
        games = sorted(d.name for d in fdir.iterdir() if d.is_dir()) if fdir.is_dir() else []
        if not games:
            games = ["明日方舟"]
    # ★ 搜索 agent 的监控提案并入（agent 发现话题扩散到的新吧）
    try:
        _wl = json.loads((ROOT / "data" / "state" / "agent_watchlist.json")
                         .read_text(encoding="utf-8"))
        for x in _wl.get("proposals", []):
            if (x.get("type") == "tieba_forum"
                    and x.get("expires_at", "") > datetime.now(TZ_CN).isoformat(timespec="seconds")
                    and x.get("name") not in games):
                games.append(x["name"])
    except Exception:
        pass
    cookie = load_cookie()
    if not cookie:
        print("[error] 未找到 TIEBA_COOKIE。请配置 "
              "L1_data_source/collectors/tieba/.env 后重跑", file=sys.stderr)
        return 3

    def one_round():
        r = collect(games, cookie,
                    with_content=args.with_content,
                    content_top=args.content_top,
                    content_sleep=args.content_sleep)
        print(json.dumps(r, ensure_ascii=False), flush=True)

    if not args.watch:
        one_round()
        return 0
    if not _single_instance_lock("crawl_tieba_forum"):
        return 0
    # ★ 健壮循环（2026-10-06）：collect 一崩整条 watch 就死（微博 7 小时事故同款），
    #   统一走 robust_watch：异常隔离 + 退避 + 死亡可见
    import sys as _sys
    _here = Path(__file__).resolve().parent
    for _p in (_here, _here.parent):
        if str(_p) not in _sys.path:
            _sys.path.insert(0, str(_p))
    from robust_watch import run_forever
    return run_forever(name="tieba", fn=one_round, interval=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
