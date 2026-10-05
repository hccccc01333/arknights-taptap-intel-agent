#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/taptap/crawl_taptap_community.py — TapTap 社区通道爬虫（帖子流 + 热评 + 帖子评论）。

与评分爬虫（crawl_taptap_reviews.py）平行的第二数据通道：
  社区公司视角的数据面 = 游戏社区的话题流，不只评分区。

接口（2026-09-28 抓包验证）：
  group/v1/recommend            全平台社区地图（app_id → group_id 映射来源，from 翻页）
  feed/v7/by-group              社区帖子流（group_id + from 翻页；moment.topic.summary=正文、
                                stat.comments/supports/pv_total、hot_comment_list 内嵌热评）
  moment-comment/v1/by-moment   帖子评论流（moment_id）

用法：
  python crawl_taptap_community.py --game wuthering-waves --max-posts 300 --comment-limit 20
输出（数据隔离目录）：
  <data_dir>/community/posts.csv      帖子（含热评 JSON）
  <data_dir>/community/comments.csv   帖子评论
  <data_dir>/community/community_checkpoint.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))  # pii_hash 在跨层共享目录
from pii_hash import hash_user_id  # noqa: E402  用户标识加盐哈希（与评分区/发现流共用同一套）

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_DIR = Path(__file__).resolve().parent
TZ_CN = timezone(timedelta(hours=8))
DEFAULT_GAME = "arknights"

BASE = "https://www.taptap.cn"
RECOMMEND_URL = f"{BASE}/webapiv2/group/v1/recommend"
GAME_GROUPS_URL = f"{BASE}/webapiv2/groups/game"   # 全量游戏社区清单（total≈2208）
FEED_URL = f"{BASE}/webapiv2/feed/v7/by-group"
COMMENT_URL = f"{BASE}/webapiv2/moment-comment/v1/by-moment"

POST_FIELDS = [
    "moment_id", "group_id", "app_id", "app_title", "author_name", "author_id_hash", "title", "summary",
    "comments", "supports", "ups", "pv_total", "publish_time",
    "hot_comment_count", "hot_comments_json", "crawled_at", "source_type",
    "first_seen_at", "last_seen_at", "last_comments", "monitor_state", "is_hot",
]
COMMENT_FIELDS = [
    "moment_id", "comment_id", "author_name", "author_id_hash", "content", "supports",
    "publish_time", "crawled_at",
]
# ★ thread 计数快照：爆火检测的数据基础（评论增速 = Δcomments/Δt）
SNAPSHOT_FIELDS = [
    "moment_id", "observed_at", "comments", "prev_comments", "comment_delta",
    "supports", "ups", "pv_total", "source_type",
]
# S1 全平台社区地图（两层落盘，设计见 docs/Agent-v2-架构设计.md §1）
GROUP_MAP_FIELDS = [
    "group_id", "app_id", "title", "has_official", "intro", "stat_json", "web_url",
    "last_seen_at", "source",
    # stat 四要素冗余成列（分类/优先级/热度排序直接 SQL 排）
    "favorite_count", "topic_count", "recent_topic_count", "official_topic_count",
]
# S1 第一层：板块分类（社区地图的骨架）
CATEGORY_FIELDS = ["group_id", "title", "icon", "has_official", "stat_json", "last_seen_at"]


def _cn_num(text: str) -> int:
    """「180.5 万」→ 1805000；「1.2 亿」→ 120000000。列要能排序，所以换算成整数。"""
    t = "".join((text or "").split())
    try:
        if t.endswith("万"):
            return int(float(t[:-1]) * 10000)
        if t.endswith("亿"):
            return int(float(t[:-1]) * 100000000)
        return int(float(t)) if t else 0
    except ValueError:
        return 0



def load_crawl_config() -> dict[str, Any]:
    """读 webapp 保存的采集参数（data/state/crawl_config.json）。

    ★ 参数在前端「数据源」页可调，落盘后爬虫下次运行自动生效 —— 这样调整
      监测窗口/翻页深度/评论预算不用改命令行。
    """
    import json as _json
    path = ROOT / "data" / "state" / "crawl_config.json"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return _json.load(f) or {}
    except (ValueError, OSError):
        print(f"[warn] 采集参数文件解析失败，忽略：{path}", file=sys.stderr)
        return {}


def now_cn_iso() -> str:
    return datetime.now(TZ_CN).isoformat(timespec="seconds")


def load_xua() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(CRAWLER_DIR / ".env")
    except ImportError:
        for line in (CRAWLER_DIR / ".env").read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() in ("TAPTAP_X_UA", "TAPTAP_COOKIE"):
                os.environ.setdefault(k.strip(), v.strip())
    xua = os.environ.get("TAPTAP_X_UA", "").strip()
    if not xua:
        print("缺少 TAPTAP_X_UA（L1_data_source/collectors/taptap/.env）", file=sys.stderr)
        raise SystemExit(2)
    return xua


def load_cookie() -> str:
    """登录态 Cookie（可选）。

    ★ 为什么需要：不登录时 groups/game 深翻到 from≥1010 就 400（实测），
      只能拿到前 ~1016 个社区；登录后翻页上限更高。
    ★ 怎么拿：浏览器登录 taptap.cn → F12 → Network → 任一 webapiv2 请求 →
      复制 Request Headers 里的整条 Cookie → 填进 .env 的 TAPTAP_COOKIE。
      .env 已在 .gitignore 里，不会进仓库。
    ★ 边界：用自己的账号、只采公开内容、频率照旧限速；被 403/429 就停。
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(CRAWLER_DIR / ".env")
    except ImportError:
        pass
    return os.environ.get("TAPTAP_COOKIE", "").strip()


class CommunityCrawler:
    def __init__(self, x_ua: str, sleep_min: float = 0.8, sleep_max: float = 1.5,
                 index_categories: int = 30, cookie: str = ""):
        self.x_ua = x_ua
        self.sleep_min, self.sleep_max = sleep_min, sleep_max
        self.index_categories = index_categories   # S1 索引：扫几个分类
        self.cookie = cookie
        self.logged_in = bool(cookie)
        self.game_total: int | None = None
        self.index_incomplete: tuple[int, int] | None = None
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Origin": BASE,
            }
        )
        if cookie:
            self.s.headers["Cookie"] = cookie

    def polite_sleep(self) -> None:
        time.sleep(random.uniform(self.sleep_min, self.sleep_max))

    def _get(self, url: str, params: dict[str, Any], referer: str, retries: int = 3) -> dict[str, Any] | None:
        last_err = ""
        for attempt in range(retries):
            try:
                r = self.s.get(
                    url,
                    params={"X-UA": self.x_ua, **params},
                    headers={"Referer": referer},
                    timeout=20,
                )
                if r.status_code != 200:
                    print(f"[warn] {url.rsplit('/', 1)[-1]} HTTP {r.status_code} (attempt {attempt + 1})", file=sys.stderr)
                    last_err = f"HTTP {r.status_code}"
                else:
                    return r.json()
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {str(e)[:60]}"
                print(f"[warn] {url.rsplit('/', 1)[-1]} {last_err} (attempt {attempt + 1})", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
        print(f"[error] 重试 {retries} 次仍失败：{last_err}", file=sys.stderr)
        return None

    # ---- group 映射：app_id -> group_id（recommend 翻页，带缓存） ----
    def resolve_group_id(self, app_id: int, cache_path: Path, preset: int | None = None, max_pages: int = 150) -> int | None:
        if preset:
            return int(preset)
        if cache_path.exists():
            mp = json.loads(cache_path.read_text(encoding="utf-8"))
            if str(app_id) in mp:
                return int(mp[str(app_id)])
        else:
            mp = {}
        # HTML 自寻（快，先试）
        if not preset:
            g = self.discover_group_id(app_id, f"{BASE}/app/{app_id}/topic")
            if g:
                mp[str(app_id)] = g
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(mp, ensure_ascii=False, indent=2), encoding="utf-8")
                return g
        frm = 0
        pages = 0
        while pages < max_pages:
            d = self._get(RECOMMEND_URL, {"from": frm}, f"{BASE}/forum") or {}
            lst = (d.get("data") or {}).get("list") or []
            if not lst:
                break
            pages += 1
            for g in lst:
                aid = str(g.get("app_id"))
                gid = g.get("id")
                if aid and gid:
                    mp[aid] = int(gid)
            if str(app_id) in mp:
                break
            frm += 20
            time.sleep(0.3)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(mp, ensure_ascii=False, indent=2), encoding="utf-8")
        gid = mp.get(str(app_id))
        if not gid:
            print(
                f"[error] recommend 清单 {pages} 页内未找到 app_id={app_id} 的 group_id；"
                f"请在浏览器打开该游戏社区页抓一次 feed 请求，把 group_id 写入游戏档案的 group_id 字段",
                file=sys.stderr,
            )
        return gid

    # ---- group_id 自寻：游戏社区页 HTML 链接挖掘 + feed 验证 ----
    def discover_group_id(self, app_id: int, referer: str) -> int | None:
        """从 /app/<id>/topic 页 HTML 挖 /group/<id> 链接，用 feed 接口验证归属。"""
        try:
            r = self.s.get(f"{BASE}/app/{app_id}/topic", headers={"User-Agent": self.s.headers["User-Agent"], "Referer": f"{BASE}/"}, timeout=20)
        except requests.RequestException:
            return None
        candidates = []
        for gid in re.findall(r"/group/(\d{3,})", r.text):
            if gid not in candidates:
                candidates.append(gid)
        for gid in candidates[:5]:
            items = self.fetch_feed_page(int(gid), 0, f"{BASE}/app/{app_id}/topic")
            for it in items[:1]:
                if ((it.get("moment") or {}).get("app") or {}).get("id") == app_id:
                    print(f"[group] HTML 自寻命中：app_id={app_id} -> group_id={gid}")
                    return int(gid)
            self.polite_sleep()
        return None

    # ---- 帖子流 ----
    def fetch_feed_page(self, group_id: int, from_off: int, referer: str, feed_type: str = "feed") -> list[dict[str, Any]]:
        d = self._get(
            FEED_URL,
            {"from": from_off, "group_id": group_id, "limit": 10, "sort": "default",
             "status": 0, "type": feed_type},
            referer,
        )
        return (d or {}).get("data", {}).get("list") or []

    # ---- 帖子评论（全量分页：limit 上限 20，from 按返回条数递增，空页即止）----
    def fetch_comments(self, moment_id: str, group_id: int, limit: int, referer: str) -> list[dict[str, Any]]:
        d = self._get(
            COMMENT_URL,
            {"moment_id": moment_id, "sort": "rank", "order": "desc",
             "regulate_all": "false", "group_id": group_id, "limit": min(limit, 50)},
            referer,
        )
        return (d or {}).get("data", {}).get("list") or []

    def fetch_all_comments(self, moment_id: str, group_id: int, referer: str,
                           max_pages: int = 10, known_ids: set[str] | None = None,
                           also_latest: bool = True) -> tuple[list[dict[str, Any]], int]:
        """抓一个帖子的顶层评论。返回 (新解析的评论行, 接口调用次数)。

        分页规律（2026-10-04 实测）：limit 上限 20；from 按**实际返回条数**递增；
        返回空页即到尾；data.total 可与 stat.comments 对账（后者含楼中楼）。

        ★ also_latest：除「按赞排序」外**再拉一遍「按时间排序」**。
          按赞只告诉你历史上谁被认可，按时间才告诉你"现在谁还在说话"——
          爆火期的新评论全部在时间序里，两条通道缺一不可。
        """
        out: list[dict[str, Any]] = []
        calls = 0
        known = known_ids or set()

        def _drain(sort: str, order: str, pages: int) -> None:
            nonlocal calls
            frm = 0
            for _ in range(pages):
                d = self._get(
                    COMMENT_URL,
                    {"moment_id": moment_id, "sort": sort, "order": order,
                     "regulate_all": "false", "group_id": group_id,
                     "limit": 20, "from": frm},
                    referer,
                )
                calls += 1
                lst = (d or {}).get("data", {}).get("list") or []
                if not lst:
                    break
                crawled_at = now_cn_iso()
                for ci in lst:
                    c = parse_comment(ci, moment_id, crawled_at)
                    if c and c["comment_id"] not in known:
                        known.add(c["comment_id"])
                        out.append(c)
                frm += len(lst)
                self.polite_sleep()

        _drain("rank", "desc", max_pages)
        if also_latest:
            _drain("time", "desc", min(3, max_pages))
        return out, calls

    def comment_budget_pages(self, comment_count: int, cfg: Dict[str, Any]) -> int:
        """分级抓全策略（用户 2026-10-04 定）：按帖子评论数决定抓几页。

          < 300   → 抓 100%（盖楼到 300 也不现实，但值得试）
          300~1000→ 前 N 页 + 记未抓完，之后每轮增量补
          > 1000  → 只抓前 3 页（热评+最新），每轮增量补
        一次 1 页 = 20 条。页数上限仍受全局 --comment-pages-per-post 约束。
        """
        cap = int(cfg.get("comment_pages_per_post", 10))
        if comment_count <= 0:
            return 0
        if comment_count < 300:
            need = -(-comment_count // 20)       # 向上取整
        elif comment_count <= 1000:
            need = 5
        else:
            need = 3
        return max(1, min(need, cap))

    # ---- S1 全平台社区地图：分类 + 推荐位 + **全量游戏社区索引** ----
    def crawl_group_map(self, out_csv: Path, cat_csv: Path, cache_path: Path,
                        max_pages: int = 3, max_pages_index: int = 6) -> int:
        """落盘 S1 三层，核心产出是第三层的 app_id → group_id 全量索引。

        实测（2026-10-04）：
          ① group/v1/list        板块分类（12 个，带 total/next_page）
          ② group/v1/recommend   推荐位社区（固定 20 个，无分页参数）
          ③ **discover-categories/v2/feed-list 分类流** —— 每条帖子的
             moment.group 就带 {app_id, group_id, title}，按 category_id 深翻可
             建出「有哪些游戏社区 + 每个社区的门牌号」索引（实测 30 分类 × 6 页
             → 86 个社区）。★ 这层才是 S2 爬单游戏社区的寻址基础。
        """
        existing = load_existing(out_csv, "group_id")
        mp: dict[str, int] = {}
        if cache_path.exists():
            mp = json.loads(cache_path.read_text(encoding="utf-8"))

        # —— 第一层：板块分类 ——
        cats = load_existing(cat_csv, "group_id")
        d = self._get(f"{BASE}/webapiv2/group/v1/list", {"from": 0, "limit": 20}, f"{BASE}/forum") or {}
        crawled_at = now_cn_iso()
        for g in ((d.get("data") or {}).get("list") or []):
            gid = str(g.get("id") or "")
            if gid:
                cats[gid] = {
                    "group_id": gid, "title": (g.get("title") or "").strip(),
                    "icon": g.get("icon") or "",
                    "has_official": "true" if g.get("has_official") else "false",
                    "stat_json": json.dumps(g.get("stat") or {}, ensure_ascii=False),
                    "last_seen_at": crawled_at,
                }
        save_csv(cat_csv, cats, CATEGORY_FIELDS)
        print(f"[S1] 板块分类 {len(cats)} 个 -> {cat_csv}")

        # —— 第二层：推荐位社区 ——
        seen = 0
        for frm in range(0, max(1, max_pages) * 20, 20):
            dd = self._get(RECOMMEND_URL, {"from": frm}, f"{BASE}/forum") or {}
            lst = (dd.get("data") or {}).get("list") or []
            if not lst:
                break
            for g in lst:
                gid = str(g.get("id") or "")
                if not gid:
                    continue
                seen += 1
                existing[gid] = {
                    "group_id": gid,
                    "app_id": str(g.get("app_id") or ""),
                    "title": (g.get("title") or "").strip(),
                    "has_official": "true" if g.get("has_official") else "false",
                    "intro": (g.get("intro") or "").strip()[:200],
                    "stat_json": json.dumps(g.get("stat") or {}, ensure_ascii=False),
                    "web_url": g.get("web_url") or "",
                    "last_seen_at": crawled_at, "source": "recommend",
                }
                if g.get("app_id") and g.get("id"):
                    mp[str(g["app_id"])] = int(g["id"])
            if lst and len(lst) < 20:
                break   # recommend 固定 20 个推荐位，翻页无新内容
            self.polite_sleep()
        save_csv(out_csv, existing, GROUP_MAP_FIELDS)
        print(f"[S1] 推荐社区 {len(existing)} 个（推荐位上限 20）-> {out_csv}")

        # —— 第三层：全量游戏社区索引（分类深翻，核心产出）——
        idx_new = self._crawl_game_index(existing, mp, max_pages_index)
        save_csv(out_csv, existing, GROUP_MAP_FIELDS)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(mp, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[S1] 游戏社区索引：累计 {len(existing)} 个社区（本轮新增 {idx_new}）→ {out_csv}")
        return len(existing)

    # ---- S1 补齐层：按 app_id 区间枚举（低段老游戏 groups/game 拿不到）----
    def crawl_app_id_range(self, index: dict[str, dict[str, Any]], mp: dict[str, int],
                           lo: int, hi: int, max_probe: int, skip_known: bool = False) -> int:
        """app_id 是从 1 起的连续号；groups/game 深翻到 from≥1010 就 400，
        结果是 app_id<208 这段老游戏全缺（钢琴块2/部落冲突/神庙逃亡…都在里面）。

        补法：GET /app/<id>/topic，页面 HTML 里带 /group/<group_id>，
        顺带拿到游戏名。一次请求解决 app_id → group_id 映射。
        ★ 已索引的 app_id 也会重探（覆盖写）：解析规则修过之后，
          老行里的空值需要被刷新，否则脏数据会一直留在索引里。
          想省请求用 --probe-skip-known。
        """
        import re as _re
        crawled_at = now_cn_iso()
        found = 0
        probed = 0
        for aid in range(max(1, lo), hi + 1):
            key = str(aid)
            if skip_known and key in mp:
                continue                      # 已有映射且要求跳过
            if probed >= max_probe:
                print(f"[S1-probe] 达到探测上限 {max_probe}，{lo}~{hi} 未扫完")
                break
            probed += 1
            try:
                r = self.s.get(f"{BASE}/app/{aid}/topic",
                               headers={"Referer": f"{BASE}/"}, timeout=15)
            except Exception:
                continue
            if r.status_code != 200 or len(r.text) < 5000:
                self.polite_sleep()
                continue
            gids = set(_re.findall(r"/group/([0-9]+)", r.text))
            if len(gids) != 1:
                self.polite_sleep()
                continue
            gid = gids.pop()
            mt = _re.search(r"<title>([^<]+)</title>", r.text)
            name = (mt.group(1).split(" - ")[0].strip() if mt else f"app_{aid}")
            if name in ("TapTap", "易玩", ""):
                self.polite_sleep()
                continue
            # 页面文案形如「180.5 万关注·682 帖子」（meta 里同款）
            st = _re.search(r"([0-9.]+\s*[万亿]?)\s*关注\s*[·・]?\s*([0-9.]+\s*[万亿]?)\s*帖子", r.text)
            index[gid] = {
                "group_id": gid, "app_id": key, "title": name,
                "has_official": "", "intro": "",
                "stat_json": json.dumps({
                    "favorite_count": _cn_num(st.group(1)) if st else 0,
                    "topic_count": _cn_num(st.group(2)) if st else 0,
                }, ensure_ascii=False),
                "web_url": f"/app/{aid}/topic",
                "last_seen_at": crawled_at, "source": "app_id_enum",
                "favorite_count": _cn_num(st.group(1)) if st else 0,
                "topic_count": _cn_num(st.group(2)) if st else 0,
                "recent_topic_count": 0, "official_topic_count": 0,
            }
            mp[key] = int(gid)
            found += 1
            self.polite_sleep()
        print(f"[S1-probe] app_id {lo}~{hi}：探测 {probed} 个，新增 {found} 个社区")
        return found

    # ---- S1 核心层：全量游戏社区清单（groups/game 真分页，total=2208）----
    def _crawl_game_index(self, index: dict[str, dict[str, Any]],
                          mp: dict[str, int], max_pages: int) -> int:
        """app_id → group_id 全量索引：S2 进任何社区的寻址基础。

        接口 groups/game?from=N&limit=20（用户 2026-10-04 提供，2026-10-04 实测）：
          · data.total = 2208（全量，含已下架与非游戏条目），from 真分页
          · 每条带 stat 四要素（关注数 favorite_count / 帖子量 topic_count /
            近期活跃 recent_topic_count / 官方声量 official_topic_count）
            → 既做分类与优先级，也做热度排序
          · **深翻边界（实测）**：from ≤ 1000 正常，从 from=1010 起返回 HTTP 400，
            所以无翻页权限时只能拿到前 ~1016 个活跃社区。要补齐剩下的，
            需登录态或换 from 上限更高的入口 —— 这里如实停在服务端给的上限。
        """
        crawled_at = now_cn_iso()
        new_count = 0
        total = None
        frm = 0
        hit_wall = False
        for _ in range(max_pages):
            d = self._get(GAME_GROUPS_URL, {"from": frm, "limit": 20}, f"{BASE}/games") or {}
            data = d.get("data") or {}
            lst = data.get("list") or []
            total = data.get("total", total)
            if not lst:
                hit_wall = True
                break
            for g in lst:
                gid = str(g.get("id") or "")
                aid = str(g.get("app_id") or "")
                if not gid or not aid:
                    continue
                stat = g.get("stat") or {}
                index[gid] = {
                    "group_id": gid,
                    "app_id": aid,
                    "title": (g.get("title") or "").strip(),
                    "has_official": "true" if g.get("has_official") else "false",
                    "intro": (g.get("intro") or "").strip()[:200],
                    "stat_json": json.dumps(stat, ensure_ascii=False),
                    "web_url": g.get("web_url") or f"/app/{aid}/topic",
                    "last_seen_at": crawled_at,
                    "source": "groups/game",
                    # 四要素冗余成列：分类/排序时可直接 SQL 排，不用解 JSON
                    "favorite_count": stat.get("favorite_count") or 0,
                    "topic_count": stat.get("topic_count") or 0,
                    "recent_topic_count": stat.get("recent_topic_count") or 0,
                    "official_topic_count": stat.get("official_topic_count") or 0,
                }
                new_count += 1
                mp[aid] = int(gid)
            frm += 20
            self.polite_sleep()
        self.game_total = total
        if hit_wall and total and new_count < int(total or 0):
            self.index_incomplete = (new_count, total)
            if not self.logged_in:
                print(f"[S1] 注意：无登录态，深翻到 from={frm} 被服务端拒绝（400）；"
                      f"已索引 {new_count}/{total}。配置 TAPTAP_COOKIE 可继续深翻")
            else:
                print(f"[S1] 注意：深翻到 from={frm} 触顶；已索引 {new_count}/{total}"
                      f"（登录态下仍有服务端上限）")
        return new_count


def parse_post(item: dict[str, Any], group_id: int, crawled_at: str, source_type: str = "feed") -> dict[str, Any] | None:
    m = item.get("moment") or {}
    mid = m.get("id_str")
    if not mid:
        return None
    topic = m.get("topic") or {}
    stat = m.get("stat") or {}
    author_obj = (m.get("author") or {}).get("user") or m.get("author") or {}
    author = author_obj.get("name", "")
    hot = m.get("hot_comment_list") or []
    pub = m.get("publish_time") or m.get("created_time")
    return {
        "moment_id": str(mid),
        "group_id": group_id,
        "app_id": (m.get("app") or {}).get("id", ""),
        # 社区名以 group.title 为准（app.title 在下架游戏上会变成「该游戏已下架」）
        "app_title": ((m.get("group") or {}).get("title") or (m.get("app") or {}).get("title") or ""),
        "author_name": author,
        "author_id_hash": hash_user_id(author_obj.get("id")),
        "title": (topic.get("title") or "").strip(),
        "summary": (topic.get("summary") or "").strip(),
        "comments": int(stat.get("comments") or 0),
        "supports": int(stat.get("supports") or 0),
        "ups": int(stat.get("ups") or 0),
        "pv_total": int(stat.get("pv_total") or 0),
        "publish_time": pub or "",
        "hot_comment_count": len(hot),
        "hot_comments_json": json.dumps(hot, ensure_ascii=False),
        "crawled_at": crawled_at,
        "source_type": source_type,
    }


def flatten_contents(node: Any) -> str:
    """评论正文富文本拍平。

    实测两种形态：
      (a) 评分区/社区早期：contents = {"text": "..."} 或纯字符串
      (b) 发现流/S6：contents = {"json": [{"type":"paragraph","children":[{"text":"..."},
                                           {"type":"tap_emoji","children":[{"text":"[表情_斜眼笑]"}]}]}]}
    递归收集所有 text 节点；表情占位保留（对情绪判断有信息量）。
    """
    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, str):
            if n:
                out.append(n)
        elif isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            t = n.get("text")
            if isinstance(t, str) and t:
                out.append(t)
            for k in ("json", "children", "content", "contents"):
                v = n.get(k)
                if isinstance(v, (list, dict, str)):
                    walk(v)

    walk(node)
    return "".join(out).strip()


def _author_obj(obj: dict[str, Any]) -> dict[str, Any]:
    """作者对象：author（发现流）→ author.user（评分区/社区）。"""
    a = obj.get("author")
    if isinstance(a, dict):
        if a.get("name") or a.get("id"):
            return a
        u = a.get("user")
        if isinstance(u, dict):
            return u
    return {}


def _author_name(obj: dict[str, Any]) -> str:
    """作者名：author.user.name（评分区/社区）→ author.name（发现流帖子评论）。"""
    for src in (obj.get("author"), (obj.get("author") or {}).get("user")):
        if isinstance(src, dict) and src.get("name"):
            return str(src["name"])
    return ""


def parse_comment(item: dict[str, Any], moment_id: str, crawled_at: str) -> dict[str, Any] | None:
    # 实测：评论对象直接在 item 顶层（无 comment 包裹）；正文在 contents（dict 或 str）
    c = item.get("comment") or item
    cid = c.get("id_str") or c.get("id")
    if not cid:
        return None
    author = _author_name(item) or _author_name(c)
    aobj = _author_obj(item) or _author_obj(c)
    rawc = c.get("contents")
    content = flatten_contents(rawc) if rawc is not None else ""
    if not content:
        content = flatten_contents(c.get("content") or c.get("summary") or "")
    stat = c.get("stat") or {}
    supports = stat.get("supports") or stat.get("likes") or c.get("ups") or 0
    return {
        "moment_id": moment_id,
        "comment_id": str(cid),
        "author_name": author,
        "author_id_hash": hash_user_id(aobj.get("id")),
        "content": str(content).strip(),
        "supports": int(supports or 0),
        "publish_time": c.get("publish_time") or c.get("created_time") or c.get("updated_time") or "",
        "crawled_at": crawled_at,
    }


def load_existing(path: Path, key_field: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {str(r.get(key_field)): r for r in csv.DictReader(f) if r.get(key_field)}


def save_csv(path: Path, rows: dict[str, dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        rows.values(),
        key=lambda r: int(r.get("publish_time", 0)) if str(r.get("publish_time", "")).isdigit() else 0,
        reverse=True,
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({k: r.get(k, "") for k in fields})


def ts_of(v: Any) -> int:
    """发布时间字段（unix 秒）→ int；非数字返回 0。"""
    return int(v) if str(v or "").isdigit() else 0


def _now_unix() -> int:
    return int(time.time())


def _post_age_days(row: dict[str, Any]) -> float:
    pt = str(row.get("publish_time") or "")
    if not pt.isdigit():
        return 0.0
    return max(0.0, (_now_unix() - int(pt)) / 86400)


def load_last_snapshots(path: Path) -> dict[str, dict[str, Any]]:
    """moment_id -> 最新一条快照（用于算 delta）。"""
    if not path.exists():
        return {}
    last: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            last[str(r.get("moment_id"))] = r
    return last


def load_snapshot_series(path: Path) -> dict[str, list[dict[str, Any]]]:
    """moment_id -> 全部快照（按时间升序）。算增速/衰减要整条曲线，不止最后一次。"""
    if not path.exists():
        return {}
    series: dict[str, list[dict[str, Any]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            series.setdefault(str(r.get("moment_id")), []).append(r)
    for v in series.values():
        v.sort(key=lambda x: x.get("observed_at") or "")
    return series


def growth_rate(series: List[dict[str, Any]]) -> float:
    """评论增速（条/小时）。至少两次采样且间隔 >=10 分钟才算，否则 0。

    ★ 新帖靠这个判「是否起飞」；单次采样或间隔太短都算不出来（不假装有数据）。
    """
    if len(series) < 2:
        return 0.0
    try:
        t0 = datetime.fromisoformat(series[0]["observed_at"])
        t1 = datetime.fromisoformat(series[-1]["observed_at"])
    except (ValueError, KeyError, TypeError):
        return 0.0
    hours = (t1 - t0).total_seconds() / 3600
    if hours < 1 / 6:          # <10 分钟：样本间隔太短，不给结论
        return 0.0
    c0 = int(series[0].get("comments") or 0)
    c1 = int(series[-1].get("comments") or 0)
    return (c1 - c0) / hours


def append_snapshots(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in SNAPSHOT_FIELDS})


def run(args: argparse.Namespace) -> int:
    cfg = load_crawl_config()
    for k in ("fresh_hours", "max_age_days", "max_pages", "elite_pages",
              "max_comment_calls", "comment_pages_per_post", "hot_ups_threshold",
              "sleep_min", "sleep_max"):
        if k in cfg and cfg[k] is not None:
            setattr(args, k, type(getattr(args, k))(cfg[k]))
    if cfg:
        print(f"[cfg] 采用 data/state/crawl_config.json（前端设置）"
              f" 窗口 {args.fresh_hours}h / 翻页 {args.max_pages} / 预算 {args.max_comment_calls}")

    sys.path.insert(0, str(ROOT / "games"))
    from game_profile import load as load_profile  # noqa: PLC0415

    if args.app_ids:
        return run_multi(args)

    prof = load_profile(args.game)
    data_dir = Path(args.data_dir or prof.get("data_dir") or "")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    community_dir = data_dir / "community"
    community_dir.mkdir(parents=True, exist_ok=True)
    app_id = int(prof.get("app_id") or 0)
    referer = f"{BASE}/app/{app_id}/topic"
    print(f"[game] {prof.get('name')} app_id={app_id} data_dir={data_dir}")

    cw = CommunityCrawler(load_xua(), args.sleep_min, args.sleep_max,
                         args.index_categories, load_cookie())
    if args.auth_check:
        print(f"[auth] 登录态: {'已配置 Cookie' if cw.logged_in else '未配置（深翻上限约 1016 个社区）'}")
        d = cw._get(GAME_GROUPS_URL, {"from": 0, "limit": 20}, f"{BASE}/games") or {}
        total = (d.get("data") or {}).get("total")
        print(f"[auth] S1 接口可达: {'是' if total else '否'}（total={total}）")
        probe = cw._get(GAME_GROUPS_URL, {"from": 1200, "limit": 20}, f"{BASE}/games")
        print(f"[auth] 深翻探测 from=1200: {'可翻页（登录态生效）' if (probe or {}).get('data') else '被拒（仍是访客权限）'}")
        return 0

    # ---------- S1 全平台社区地图（板块分类 + 推荐社区两层，兼顾 group_id 映射缓存）----------
    if args.map_pages > 0:
        cw.crawl_group_map(community_dir / "community_map.csv",
                           community_dir / "community_categories.csv",
                           community_dir / "group_map.json",
                           args.map_pages, max_pages_index=args.index_pages)

    if args.probe_hi >= args.probe_lo and args.probe_lo > 0:
        _map_csv = community_dir / "community_map.csv"
        _cache = community_dir / "group_map.json"
        _index = load_existing(_map_csv, "group_id")
        _mp = json.loads(_cache.read_text(encoding="utf-8")) if _cache.exists() else {}
        cw.crawl_app_id_range(_index, _mp, args.probe_lo, args.probe_hi, args.probe_max,
                               args.probe_skip_known)
        save_csv(_map_csv, _index, GROUP_MAP_FIELDS)
        _cache.parent.mkdir(parents=True, exist_ok=True)
        _cache.write_text(json.dumps(_mp, ensure_ascii=False, indent=2), encoding="utf-8")

    group_id = cw.resolve_group_id(app_id, community_dir / "group_map.json", preset=int(prof["group_id"]) if prof.get("group_id") else None)
    if not group_id:
        print(f"[stop] 未在 recommend 全平台清单中找到 app_id={app_id} 的社区", file=sys.stderr)
        return 3
    print(f"[group] group_id={group_id}")

    posts_path = community_dir / "posts.csv"
    comments_path = community_dir / "comments.csv"
    snapshots_path = community_dir / "thread_snapshots.csv"
    posts = load_existing(posts_path, "moment_id")
    comments = load_existing(comments_path, "comment_id")
    last_snap = load_last_snapshots(snapshots_path)
    # 老库兼容：补新列默认值
    for p in posts.values():
        p.setdefault("first_seen_at", p.get("crawled_at", ""))
        p.setdefault("last_seen_at", "")
        p.setdefault("last_comments", "")
        p.setdefault("monitor_state", "active")
    print(f"[existing] posts={len(posts)} comments={len(comments)} snapshots={len(last_snap)}")

    stats = {"pages": 0, "fetched": 0, "added": 0, "comment_calls": 0,
             "comments_added": 0, "snapshots": 0, "refreshed": 0}
    cp_path = community_dir / "community_checkpoint.json"
    frm = 0
    if cp_path.exists() and args.resume:
        frm = int(json.loads(cp_path.read_text(encoding="utf-8")).get("last_from") or 0)
        print(f"[resume] last_from={frm}")
    empty_pages = 0
    feed_types = [t.strip() for t in args.types.split(",") if t.strip()]
    cur_type_idx = 0
    observed_at = now_cn_iso()
    # ★ 新帖窗口：by-group 没有时间序参数（实测 sort 只认 default），
    #   所以"按时间发现新帖"只能靠反复轮询首页 + 过滤发布时间。
    #   窗口太宽会把推荐流里的老帖当新帖收进来，太窄会漏掉低频社区的新帖。
    fresh_cutoff = (time.time() - args.fresh_hours * 3600) if args.fresh_hours > 0 else 0
    stale_skipped = 0
    snap_rows: list[dict[str, Any]] = []
    new_ids: list[str] = []
    grown_ids: list[str] = []

    # ---------- S2 帖子流：发现新 thread + 刷新计数（快照每轮必写）----------
    # ★ 停止条件只看"本轮翻了多少页"，不看库内帖子总数：
    #   库是累积的（每轮都往里加），拿总数当条件会导致跑第二轮直接不执行，
    #   计数快照不刷新 → 爆火检测失效。
    while stats["pages"] < args.max_pages and cur_type_idx < len(feed_types):
        cur_type = feed_types[cur_type_idx]
        if stats["pages"] > 0 and frm > 0 and empty_pages >= args.empty_pages:
            cur_type_idx += 1
            if cur_type_idx >= len(feed_types):
                break
            cur_type = feed_types[cur_type_idx]
            frm = 0
            empty_pages = 0
            print(f"[switch] 切换到流类型：{cur_type}")
        items = cw.fetch_feed_page(group_id, frm, referer, cur_type)
        stats["pages"] += 1
        if not items:
            print(f"[stop] {cur_type} from={frm} 空页")
            cur_type_idx += 1
            if cur_type_idx >= len(feed_types):
                break
            frm = 0
            empty_pages = 0
            continue
        page_added = 0
        for it in items:
            row = parse_post(it, group_id, observed_at, cur_type)
            if not row:
                continue
            if args.max_posts and stats["added"] >= args.max_posts:
                break
            stats["fetched"] += 1
            old = posts.get(row["moment_id"])
            if old is None:
                # 窗口外的旧帖：推荐流会反复推老帖，不收（精华流另行处理）
                if fresh_cutoff and (ts_of(row["publish_time"]) or 0) < fresh_cutoff:
                    stale_skipped += 1
                    continue
                row["first_seen_at"] = observed_at
                row["monitor_state"] = "active"
                row["is_hot"] = 1 if (ts_of(row["ups"]) or int(row["ups"] or 0)) >= args.hot_ups_threshold else 0
                posts[row["moment_id"]] = row
                stats["added"] += 1
                page_added += 1
                new_ids.append(row["moment_id"])
            else:
                old["last_seen_at"] = observed_at
                old["comments"] = row["comments"]      # 计数以本轮为准
                old["supports"] = row["supports"]
                old["ups"] = row["ups"]
                old["pv_total"] = row["pv_total"]
                old["is_hot"] = 1 if int(row["ups"] or 0) >= args.hot_ups_threshold else 0
            cur = posts[row["moment_id"]]
            prev = int(cur.get("last_comments") or last_snap.get(row["moment_id"], {}).get("comments") or 0)
            delta = int(row["comments"]) - prev
            snap_rows.append({"moment_id": row["moment_id"], "observed_at": observed_at,
                              "comments": row["comments"], "prev_comments": prev,
                              "comment_delta": delta, "supports": row["supports"],
                              "ups": row["ups"], "pv_total": row["pv_total"],
                              "source_type": cur_type})
            cur["last_comments"] = row["comments"]
            if old is not None and delta > 0:
                grown_ids.append(row["moment_id"])   # closed 帖重新增长也会被打开监测
        append_snapshots(snapshots_path, snap_rows)
        stats["snapshots"] += len(snap_rows)
        snap_rows = []
        cp_path.write_text(json.dumps({"last_from": frm + 10, "updated_at": now_cn_iso()}, ensure_ascii=False), encoding="utf-8")
        print(f"[page] from={frm} got={len(items)} added={page_added} total={len(posts)}")
        if page_added == 0:
            empty_pages += 1
        else:
            empty_pages = 0
        frm += 10
        cw.polite_sleep()

    # ---------- 精华流：独立小批量（精华≠新帖，不参与新帖发现，但要抓）----------
    if args.elite_pages > 0:
        elite_added = 0
        for ep in range(args.elite_pages):
            items = cw.fetch_feed_page(group_id, ep * 10, referer, "elite")
            if not items:
                break
            for it in items:
                row = parse_post(it, group_id, now_cn_iso(), "elite")
                if not row:
                    continue
                if row["moment_id"] not in posts:
                    row["first_seen_at"] = now_cn_iso()
                    row["monitor_state"] = "active"
                    posts[row["moment_id"]] = row
                    elite_added += 1
            cw.polite_sleep()
        print(f"[S2-elite] 精华帖 {elite_added} 条（{args.elite_pages} 页）")

    # ---------- S3 评论：新帖抓全，老帖涨了才补（预算优先给增量大的）----------
    comments_by_moment: dict[str, set[str]] = {}
    for c in comments.values():
        comments_by_moment.setdefault(c["moment_id"], set()).add(c["comment_id"])

    def age(mid: str) -> float:
        return _post_age_days(posts[mid])

    # ★ 预算优先级：达点赞阈值的 hot 帖 > 其他新帖（按评论数）
    fresh_new = [m for m in new_ids if m in posts]
    fresh_new.sort(key=lambda m: (0 if posts[m].get("is_hot") == "1" else 1,
                                  -int(posts[m]["comments"] or 0)))
    grown = sorted({m for m in grown_ids if m in posts},
                   key=lambda m: -int(posts[m].get("last_comments") or 0))
    plan: list[tuple[str, str]] = []   # (moment_id, 模式)
    for m in fresh_new:
        if age(m) <= args.max_age_days:
            plan.append((m, "full"))
    for m in grown:
        if m not in fresh_new and age(m) <= args.max_age_days:
            plan.append((m, "refresh"))
    print(f"[S3] 待抓评论：新帖 {len(fresh_new)} + 增量 {len(grown)}，预算 {args.max_comment_calls} 次调用")

    for mid, mode in plan:
        if stats["comment_calls"] >= args.max_comment_calls:
            print(f"[S3] 预算用尽，剩余 {len(plan)} 帖下轮再补")
            break
        new_rows, used = cw.fetch_all_comments(
            mid, group_id, referer,
            max_pages=args.comment_pages_per_post if mode == "full" else 2,
            known_ids=comments_by_moment.get(mid, set()))
        stats["comment_calls"] += used
        for c in new_rows:
            c.setdefault("crawled_at", now_cn_iso())
            comments[c["comment_id"]] = c
            stats["comments_added"] += 1
        posts[mid]["monitor_state"] = "active"       # 拉过评论（无论新旧）都视为重新活跃
        print(f"[S3] {mode} moment={mid} +{len(new_rows)} 评论（调用 {used}）")
        cw.polite_sleep()

    # 超龄且无增长的 thread 关监测（数据保留，只是不再耗预算）
    closed = 0
    for m, p in posts.items():
        if p.get("monitor_state") == "active" and age(m) > args.max_age_days:
            p["monitor_state"] = "closed"
            closed += 1

    save_csv(posts_path, posts, POST_FIELDS)
    save_csv(comments_path, comments, COMMENT_FIELDS)
    print(f"[S2] 窗口外旧帖跳过 {stale_skipped} 条（窗口 {args.fresh_hours}h）")
    print(f"[done] posts={len(posts)}(新{stats['added']}) comments={len(comments)}(+{stats['comments_added']}) "
          f"snapshots+{stats['snapshots']} comment_calls={stats['comment_calls']} closed={closed}")
    return 0


def run_multi(args: argparse.Namespace) -> int:
    cfg = load_crawl_config()
    for k in ("fresh_hours", "max_age_days", "max_pages", "elite_pages",
              "max_comment_calls", "comment_pages_per_post", "hot_ups_threshold",
              "sleep_min", "sleep_max"):
        if k in cfg and cfg[k] is not None:
            setattr(args, k, type(getattr(args, k))(cfg[k]))
    if cfg:
        print(f"[cfg] 采用 data/state/crawl_config.json（前端设置）"
              f" 窗口 {args.fresh_hours}h / 翻页 {args.max_pages} / 预算 {args.max_comment_calls}")


    """多游戏社区采集（演示用）：按 app_id 从 S1 索引寻址，逐个采。

    每个游戏独立数据目录 data/raw/taptap/communities/<app_id>/，
    目录结构与单游戏一致（posts.csv / comments.csv / thread_snapshots.csv）。
    """
    cw = CommunityCrawler(load_xua(), args.sleep_min, args.sleep_max)
    idx_csv = ROOT / "data" / "raw" / "taptap" / "community" / "community_map.csv"
    index = load_existing(idx_csv, "app_id")
    wanted = [x.strip() for x in args.app_ids.split(",") if x.strip()]
    base_dir = ROOT / "data" / "raw" / "taptap" / "communities"
    total_posts = total_comments = 0
    for aid in wanted:
        row = index.get(aid)
        if not row or not row.get("group_id"):
            print(f"[skip] app_id={aid} 不在 S1 索引里（先跑 S1 索引）", file=sys.stderr)
            continue
        gid = int(row["group_id"])
        name = row.get("title") or aid
        d = base_dir / aid
        d.mkdir(parents=True, exist_ok=True)
        referer = f"{BASE}/app/{aid}/topic"
        print(f"[game] {name} app_id={aid} group_id={gid}")

        posts = load_existing(d / "posts.csv", "moment_id")
        comments = load_existing(d / "comments.csv", "comment_id")
        last_snap = load_last_snapshots(d / "thread_snapshots.csv")
        before_posts, before_cmts = len(posts), len(comments)
        observed_at = now_cn_iso()
        fresh_cut = time.time() - args.fresh_hours * 3600 if args.fresh_hours > 0 else 0
        new_ids, snap_rows = [], []

        for page in range(args.max_pages):
            items = cw.fetch_feed_page(gid, page * 10, referer, "feed")
            if not items:
                break
            for it in items:
                row = parse_post(it, gid, observed_at, "feed")
                if not row:
                    continue
                mid = row["moment_id"]
                old = posts.get(mid)
                if old is None:
                    if fresh_cut and (ts_of(row["publish_time"]) or 0) < fresh_cut:
                        continue
                    row["first_seen_at"] = observed_at
                    row["monitor_state"] = "active"
                    posts[mid] = row
                    new_ids.append(mid)
                else:
                    old["last_seen_at"] = observed_at
                    old["comments"] = row["comments"]
                    old["supports"] = row["supports"]
                    old["ups"] = row["ups"]
                    old["pv_total"] = row["pv_total"]
                cur = posts[mid]
                prev = int(cur.get("last_comments") or last_snap.get(mid, {}).get("comments") or 0)
                snap_rows.append({"moment_id": mid, "observed_at": observed_at,
                                  "comments": row["comments"], "prev_comments": prev,
                                  "comment_delta": row["comments"] - prev,
                                  "supports": row["supports"], "ups": row["ups"],
                                  "pv_total": row["pv_total"], "source_type": "feed"})
                cur["last_comments"] = row["comments"]
            cw.polite_sleep()
        append_snapshots(d / "thread_snapshots.csv", snap_rows)

        # 评论：新帖抓全 + 增长帖补增量，预算按游戏均分
        by_moment = {}
        for c in comments.values():
            by_moment.setdefault(c["moment_id"], set()).add(c["comment_id"])
        budget = max(5, args.max_comment_calls // max(1, len(wanted)))
        spent = 0
        targets = sorted(new_ids, key=lambda m: -int(posts[m]["comments"] or 0))
        for mid in targets:
            if spent >= budget:
                break
            rows_new, used = cw.fetch_all_comments(mid, gid, referer,
                                                    max_pages=4, known_ids=by_moment.get(mid, set()))
            spent += used
            for c in rows_new:
                c.setdefault("crawled_at", now_cn_iso())
                comments[c["comment_id"]] = c
        for m in list(posts):
            if posts[m].get("monitor_state") == "active" and _post_age_days(posts[m]) > args.max_age_days:
                posts[m]["monitor_state"] = "closed"

        save_csv(d / "posts.csv", posts, POST_FIELDS)
        save_csv(d / "comments.csv", comments, COMMENT_FIELDS)
        dp = len(posts) - before_posts
        dc = len(comments) - before_cmts
        total_posts += dp
        total_comments += dc
        print(f"[done] {name}: posts={len(posts)}(+{dp}) comments={len(comments)}(+{dc}) "
              f"snapshots+{len(snap_rows)} 调用{spent}")
    print(f"[multi] 合计新增 {total_posts} 帖 / {total_comments} 评论")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="TapTap 社区通道爬虫（S1 社区地图 / S2 帖子发现 / S3 评论全量+增量）")
    p.add_argument("--game", default=DEFAULT_GAME, help="游戏档案 key（games/<key>.json）")
    p.add_argument("--app-ids", default="",
                   help="按 app_id 逗号分隔直采多个游戏（从 S1 索引寻址 group_id，"
                        "不需要每款游戏都建档案；演示多社区采集用）")
    p.add_argument("--data-dir", default="", help="数据目录（默认取档案 data_dir）")
    p.add_argument("--max-posts", type=int, default=0,
                   help="本轮最多新增多少新帖（0 = 不限，只受 --max-pages 控制；"
                        "注意库内总数是累积的，不要用它当停止条件）")
    p.add_argument("--max-pages", type=int, default=12, help="推荐流翻页上限（新帖靠轮询累积，不必深翻）")
    p.add_argument("--elite-pages", type=int, default=2, help="精华流单独抓几页（0 = 不抓；精华≠新帖，量小即可）")
    p.add_argument("--fresh-hours", type=int, default=72, help="只收发布时间在最近 N 小时内的帖（0 = 不限）")
    p.add_argument("--hot-ups-threshold", type=int, default=50,
                   help="点赞阈值：帖子点赞达到此值就抓全评论并标重点监测（优先给预算）")
    p.add_argument("--types", default="feed", help="流类型（逗号分隔）：feed 推荐流 / elite 精华流 / top_feed 置顶。"
                   "★ 主通道只留 feed：实测 by-group 无时间序参数（sort 只认 default），"
                   "新帖靠反复轮询首页累积；elite 是历史精华，会淹没新帖，单独小批量抓")
    p.add_argument("--empty-pages", type=int, default=3, help="连续 N 页无新增才停")
    p.add_argument("--resume", action="store_true", help="从 checkpoint 续爬")
    p.add_argument("--map-pages", type=int, default=3, help="S1 推荐位翻页数（0 = 本轮不抓地图）")
    p.add_argument("--index-categories", type=int, default=30, help="S1 索引：扫描几个发现流分类（建 app_id→group_id 全量映射）")
    p.add_argument("--auth-check", action="store_true", help="只检查登录态与 S1 索引能翻多深，不做别的")
    p.add_argument("--probe-lo", type=int, default=0, help="按 app_id 枚举补齐：起始 id（groups/game 深翻拿不到低段）")
    p.add_argument("--probe-hi", type=int, default=0, help="按 app_id 枚举补齐：结束 id（含）")
    p.add_argument("--probe-max", type=int, default=200, help="按 app_id 枚举补齐：本轮最多探测多少个 id")
    p.add_argument("--probe-skip-known", action="store_true", help="跳过索引里已有的 app_id（省请求，但不刷新旧值）")
    p.add_argument("--index-pages", type=int, default=6, help="S1 索引：每个分类深翻几页")
    p.add_argument("--max-comment-calls", type=int, default=150, help="本轮评论接口调用预算（新帖优先，其次增量大的）")
    p.add_argument("--comment-pages-per-post", type=int, default=10, help="新帖全量抓评论的页数上限（每页 20 条）")
    p.add_argument("--max-age-days", type=int, default=14, help="超过 N 天且无增长的帖子停止监测（数据保留）")
    p.add_argument("--sleep-min", type=float, default=0.8)
    p.add_argument("--sleep-max", type=float, default=1.5)
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
