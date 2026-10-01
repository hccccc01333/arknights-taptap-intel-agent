#!/usr/bin/env python3
"""T5 素材获取 —— 素材抽取（**纯代码的两类**）+ 溯源准入闸门。

设计依据：`docs/素材层设计.md`
  §4.1 原帖引用（纯代码）/ §4.2 热评金句（**选取**纯代码，**分类**需 LLM）
  §2   溯源是准入条件（没有溯源就不算素材）；§2.3 代码层强制
  §3   素材 Schema；§3.5 存 Thread 不存孤立评论
  §5   PII 边界（作者名可展示但**绝不入库**）

★ 本模块只做**不需要 LLM** 的两类：`original_post` / `hot_comment`。
  `meme`（梗）与 `remix_angle`（二创角度）需要 LLM → **显式标注缺失**，不假装有。
  这就是契约里的**分层降级**：LLM 不可用时出「可出的两类 + 说明缺哪两类」，
  而不是整任务失败（部分产出 + 显式标注 > 全部失败）。

⚠️ 实测踩坑（素材层设计 §3.5.7 记录，本次复核确认）：
  **原帖的点赞在 `ups`，评论的点赞才叫 `supports`** —— posts 的 `supports` 列**全是 0**。
  看错字段会得出「原帖点赞全 0」的错误结论。

用法：
  python 11情报Agent/materials.py --run            # 抽取并落盘
  python 11情报Agent/materials.py --status         # 看素材库现状
  python 11情报Agent/materials.py --run --per-topic 3
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
LAB = Path(__file__).resolve().parent
PLATFORM = ROOT / "02数据_platform"
MATERIALS_DIR = PLATFORM / "materials"

POSTS_CSV = PLATFORM / "discovery_posts.csv"
COMMENTS_CSV = PLATFORM / "discovery_comments.csv"
MATERIALS_JSONL = MATERIALS_DIR / "materials.jsonl"
THREADS_JSONL = MATERIALS_DIR / "threads.jsonl"
REPORT = LAB / "reports" / "materials_latest.md"

TZ = timezone(timedelta(hours=8))
SCHEMA_VERSION = "1.0"

# ---- 判据参数（全部可核对，非主观；改了要同步 docs/素材层设计.md §4）----
POST_MIN_TEXT = 8              # 原帖引用：标题或摘要 ≥ 8 字（太短无信息量）
COMMENT_MIN_TEXT = 10          # 金句长度窗口下界
COMMENT_MAX_TEXT = 120         # 金句长度窗口上界
PER_TOPIC = 5                  # 每话题取前 N 条原帖引用
PER_THREAD = 3                 # 每个 Thread 取前 N 条热评
TAPTAP_MOMENT_PREFIX = "https://www.taptap.cn/moment/"

# LLM 才能做、本模块**故意不做**的两类（显式降级，不假装）
LLM_ONLY_TYPES = ("meme", "remix_angle")
CODE_ONLY_TYPES = ("original_post", "hot_comment")

# 纯表情 / 纯符号 / 无信息回复（品质过滤）
_STRIP = re.compile(r"[\s#@\u200b]+")
_SYMBOL_ONLY = re.compile(r"^[\W_]+$")           # 去掉字词后只剩符号
LOW_INFO_PATTERNS = re.compile(
    r"^(\+1|顶|赞|好活|前排|沙发|哈哈哈+|hhh+|233+|6+|awsl|cy|插眼|。|\.|！|!)+$",
    re.IGNORECASE)
# PII：这些字段**绝不入库**（作者名可展示，但要哈希）
PII_FIELDS = ("author_name", "author_id", "user_name", "nickname")


# ============================================================ 工具

def _now() -> datetime:
    return datetime.now(TZ)


def text_len(s: str | None) -> int:
    """有效长度：去掉空白 / #话题标记 / @提及 / 零宽字符。"""
    return len(_STRIP.sub("", s or ""))


def is_low_info(s: str | None) -> bool:
    """纯表情 / 纯符号 / 无信息回复（+1、顶、哈哈哈…）。"""
    t = (s or "").strip()
    if not t:
        return True
    if _SYMBOL_ONLY.match(t):
        return True
    return bool(LOW_INFO_PATTERNS.match(t))


def topic_key_of(hashtag_id: str | None) -> str | None:
    """复用 `topic_tracker` 的 key 格式，两个系统因此可对齐。"""
    hid = (hashtag_id or "").strip()
    return f"hid:{hid}|page_view" if hid else None


def material_id(kind: str, moment_id: str, comment_id: str | None = None) -> str:
    """稳定 id（= 幂等键的一部分）：同素材重跑不会产生新 id。"""
    raw = f"{kind}|{moment_id}|{comment_id or ''}"
    return f"m_{kind[:4]}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def moment_url(moment_id: str) -> str:
    return f"{TAPTAP_MOMENT_PREFIX}{moment_id}"


# ============================================================ 准入闸门

def assert_traceable(material: dict[str, Any]) -> None:
    """**没有溯源就不算素材**（素材层设计 §2.3，原样实现）。

    不通过时抛 ValueError —— 调用方应当**丢弃并计数**，而不是打回整批。
    """
    p = material.get("provenance") or {}
    if not p.get("moment_id"):
        raise ValueError(f"素材缺 moment_id，无法溯源，丢弃：{material.get('material_id')}")
    if not str(p.get("url", "")).startswith(TAPTAP_MOMENT_PREFIX):
        raise ValueError(f"素材 url 不是可打开的 TapTap 链接：{p.get('url')}")

    if p.get("comment_id") is not None and not str(p["comment_id"]).strip():
        raise ValueError(f"评论类素材的 comment_id 为空：{material.get('material_id')}")


def assert_no_pii(material: dict[str, Any]) -> None:
    """作者名可展示，但**绝不入库**（素材层设计 §5）。"""
    for f in PII_FIELDS:
        if f in material:
            raise ValueError(f"素材含 PII 字段 {f}：{material.get('material_id')}"
                             "（应用 author_hash 代替）")
        for v in (material.get("provenance") or {}).values():
            if isinstance(v, dict) and f in v:
                raise ValueError(f"provenance 含 PII 字段 {f}")


def admit(material: dict[str, Any]) -> tuple[bool, str]:
    """准入：溯源 + PII 两道闸门。返回 (是否准入, 原因)。"""
    try:
        assert_traceable(material)
        assert_no_pii(material)
        return True, "ok"
    except ValueError as e:
        return False, str(e)


# ============================================================ 读数据

def read_csv(path: Path | str | None = None) -> list[dict[str, str]]:
    path = Path(path) if path else Path(POSTS_CSV)
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


# ============================================================ 抽取：原帖引用

def extract_original_posts(posts: Iterable[dict[str, str]], *,
                           per_topic: int | None = None,
                           now: datetime | None = None) -> dict[str, Any]:
    """原帖引用（§4.1，**纯代码**）。

    判据：该话题下按 **ups**（原帖点赞）降序，同分看 comments（讨论度）；
         标题或摘要 ≥8 字；剔除纯表情/符号；同 moment_id 只出一次。
    """
    per_topic = PER_TOPIC if per_topic is None else per_topic
    now = now or _now()
    by_topic: dict[str | None, list[dict[str, str]]] = {}
    for p in posts:
        mid = (p.get("moment_id") or "").strip()
        if not mid:
            continue
        by_topic.setdefault(topic_key_of(p.get("hashtag_id")), []).append(p)

    out: list[dict[str, Any]] = []
    emitted: dict[str, dict[str, Any]] = {}      # ★ moment_id → 已产出（**跨话题全局去重**）
    dropped: list[dict[str, Any]] = []
    for key, group in by_topic.items():
        # 选取判据：ups 降序 → comments 降序
        ranked = sorted(group, key=lambda r: (-_int(r.get("ups")), -_int(r.get("comments"))))
        kept = 0
        for r in ranked:
            if kept >= per_topic:
                break
            title = (r.get("title") or "").strip()
            summary = (r.get("summary") or "").strip()
            body = title if text_len(title) >= text_len(summary) else summary
            mid = (r.get("moment_id") or "").strip()
            # ★ 同一帖子在多个话题下命中 → **只出一次**，把别的话题记进 multi_topic_keys
            if mid in emitted:
                if key and key not in emitted[mid]["multi_topic_keys"]:
                    emitted[mid]["multi_topic_keys"].append(key)
                continue
            if max(text_len(title), text_len(summary)) < POST_MIN_TEXT:
                dropped.append({"moment_id": mid, "reason": "文字过短（<8 字）"})
                continue
            if is_low_info(body):
                dropped.append({"moment_id": mid, "reason": "纯表情/符号"})
                continue
            m: dict[str, Any] = {
                "material_id": material_id("original_post", mid),
                "type": "original_post",
                "topic_key": key,
                "topic_title": (r.get("hashtag_title") or "").strip() or None,
                "thread_id": mid,
                "self_contained": False,          # 原帖引用依赖上下文（见 §3.5.3）
                "text": body,
                "has_title": bool(title),
                "multi_topic_keys": [],           # 跨话题命中的其它话题（见上方去重分支）
                "provenance": {
                    "moment_id": mid,
                    "comment_id": None,
                    "url": moment_url(mid),
                    "source_channel": "S6_feed_by_hashtag" if key else "S5_discover_feed",
                    "source_type": (r.get("source_type") or "").strip() or None,
                    "captured_at": (r.get("crawled_at") or "").strip() or now.isoformat(timespec="seconds"),
                    "metrics": {"ups": _int(r.get("ups")), "comments": _int(r.get("comments")),
                                "pv_total": _int(r.get("pv_total"))},
                    "author_hash": (r.get("author_id_hash") or "").strip() or None,
                },
            }
            out.append(m)
            emitted[mid] = m
            kept += 1

    return {"materials": out, "dropped": dropped,
            "n_topics": len([k for k in by_topic if k]),
            "n_unattributed": len(by_topic.get(None, [])),
            "n_multi_topic": sum(1 for m in out if m["multi_topic_keys"])}


# ============================================================ 抽取：热评金句

def extract_hot_comments(comments: Iterable[dict[str, str]],
                         posts_by_moment: dict[str, dict[str, str]], *,
                         per_thread: int | None = None,
                         now: datetime | None = None) -> dict[str, Any]:
    """热评金句（§4.2）—— **只做「选取」，不做「分类」**。

    ★ 分工（设计明确）：
      · 哪些评论**值得进候选池** → 代码（**帖子内** supports 降序 + 10–120 字窗口 + 品质过滤）
      · 它们是**金句**还是**段子** → **LLM 仲裁**（我们标注 `classification: pending_llm`）
    """
    per_thread = PER_THREAD if per_thread is None else per_thread
    now = now or _now()
    by_thread: dict[str, list[dict[str, str]]] = {}
    for c in comments:
        mid = (c.get("moment_id") or "").strip()
        if mid:
            by_thread.setdefault(mid, []).append(c)

    out: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for mid, group in by_thread.items():
        # 帖子内排序（**不跨帖统计** —— 见 §4.3 实测翻案）；同分用长度兜底（长一点信息多）
        ranked = sorted(group, key=lambda c: (-_int(c.get("supports")),
                                              -text_len(c.get("content"))))
        kept = 0
        for c in ranked:
            if kept >= per_thread:
                break
            cid = (c.get("comment_id") or "").strip()
            body = (c.get("content") or "").strip()
            n = text_len(body)
            if not cid:
                dropped.append({"moment_id": mid, "comment_id": None, "reason": "缺 comment_id"})
                continue
            if n < COMMENT_MIN_TEXT or n > COMMENT_MAX_TEXT:
                dropped.append({"moment_id": mid, "comment_id": cid,
                                "reason": f"长度 {n} 不在 {COMMENT_MIN_TEXT}–{COMMENT_MAX_TEXT}"})
                continue
            if is_low_info(body):
                dropped.append({"moment_id": mid, "comment_id": cid, "reason": "无信息回复"})
                continue
            post = posts_by_moment.get(mid) or {}
            tkey = topic_key_of(post.get("hashtag_id"))
            out.append({
                "material_id": material_id("hot_comment", mid, cid),
                "type": "hot_comment",
                "topic_key": tkey,
                "topic_title": (post.get("hashtag_title") or "").strip() or None,
                "thread_id": mid,
                "self_contained": False,          # 金句带回 Thread 才有语境（§3.5.2）
                "text": body,
                "classification": "pending_llm",  # ★ 金句 vs 段子 = 语义判断，不假装已分好
                "provenance": {
                    "moment_id": mid,
                    "comment_id": cid,
                    "url": moment_url(mid),
                    "source_channel": "S3_moment_comment",
                    "source_type": (c.get("source_type") or "").strip() or None,
                    "captured_at": (c.get("crawled_at") or "").strip() or now.isoformat(timespec="seconds"),
                    "metrics": {"supports": _int(c.get("supports"))},
                    "thread_context": "ok" if post else "missing",   # 帖不在库里 → 语境缺失
                    "author_hash": None,              # 评论侧只有 name，**不入库**
                },
            })
            kept += 1

    return {"materials": out, "dropped": dropped, "n_threads": len(by_thread),
            "n_thread_context_missing": sum(
                1 for m in out if m["provenance"]["thread_context"] == "missing")}


# ============================================================ Thread（检索单元）

def build_threads(posts: Iterable[dict[str, str]],
                  comments: Iterable[dict[str, str]]) -> tuple[list[dict[str, Any]], int]:
    """★ 存 Thread，不存孤立评论（§3.5.7）—— 检索与生成的基本单元，保住语境。"""
    by_moment: dict[str, dict[str, Any]] = {}
    for p in posts:
        mid = (p.get("moment_id") or "").strip()
        if not mid:
            continue
        by_moment[mid] = {
            "thread_id": mid,
            "topic_key": topic_key_of(p.get("hashtag_id")),
            "topic_title": (p.get("hashtag_title") or "").strip() or None,
            "title": (p.get("title") or "").strip() or None,
            "summary": (p.get("summary") or "").strip() or None,
            "metrics": {"ups": _int(p.get("ups")), "comments": _int(p.get("comments"))},
            "comment_ids": [],
        }
    orphan_comments = 0
    for c in comments:
        mid = (c.get("moment_id") or "").strip()
        cid = (c.get("comment_id") or "").strip()
        if not mid or not cid:
            continue
        if mid not in by_moment:
            orphan_comments += 1                       # 帖不在库（只在评论流里）→ 语境缺失
            by_moment[mid] = {"thread_id": mid, "topic_key": None, "topic_title": None,
                              "title": None, "summary": None, "metrics": {},
                              "comment_ids": [], "post_missing": True}
        by_moment[mid]["comment_ids"].append(cid)
    for t in by_moment.values():
        t["n_comments"] = len(t["comment_ids"])
    return sorted(by_moment.values(), key=lambda t: -t["n_comments"]), orphan_comments


# ============================================================ 落盘

def write_jsonl(path: Path | str | None, rows: list[dict[str, Any]]) -> int:
    path = Path(path) if path else Path(MATERIALS_JSONL)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def run(*, per_topic: int | None = None, per_thread: int | None = None,
        posts_csv: Path | str | None = None, comments_csv: Path | str | None = None,
        materials_path: Path | str | None = None,
        threads_path: Path | str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """抽取一轮并落盘。**先一律过准入闸门，不通过的丢弃并计数。**"""
    now = now or _now()
    posts = read_csv(posts_csv or POSTS_CSV)
    comments = read_csv(comments_csv or COMMENTS_CSV)
    posts_by_moment = {(p.get("moment_id") or "").strip(): p for p in posts
                       if (p.get("moment_id") or "").strip()}

    rp = extract_original_posts(posts, per_topic=per_topic, now=now)
    rc = extract_hot_comments(comments, posts_by_moment, per_thread=per_thread, now=now)
    threads, orphan = build_threads(posts, comments)

    raw = rp["materials"] + rc["materials"]
    admitted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for m in raw:
        ok, why = admit(m)
        (admitted if ok else rejected).append(m if ok else
                                             {"material_id": m.get("material_id"),
                                              "type": m.get("type"), "reason": why})

    n_m = write_jsonl(materials_path or MATERIALS_JSONL, admitted)
    n_t = write_jsonl(threads_path or THREADS_JSONL, threads)

    by_type: dict[str, int] = {}
    for m in admitted:
        by_type[m["type"]] = by_type.get(m["type"], 0) + 1

    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "n_materials": n_m, "n_threads": n_t,
        "by_type": by_type,
        "missing_types": list(LLM_ONLY_TYPES),
        "missing_reason": "需 LLM：梗=两轴语义分类、二创角度=生成；当前无 key（402）→ 分层降级，只出代码可做的两类",
        "n_dropped_by_rule": len(rp["dropped"]) + len(rc["dropped"]),
        "n_rejected_by_gate": len(rejected),
        "rejected_samples": rejected[:5],
        "n_topics": rp["n_topics"],
        "n_unattributed_posts": rp["n_unattributed"],
        "n_thread_context_missing": rc["n_thread_context_missing"],
        "n_orphan_comments": orphan,
        "traceable_rate": 1.0 if not rejected else round(len(admitted) / max(len(raw), 1), 4),
        "sources": {"posts": str(Path(posts_csv or POSTS_CSV).name),
                    "comments": str(Path(comments_csv or COMMENTS_CSV).name),
                    "n_posts": len(posts), "n_comments": len(comments)},
        "path": str(materials_path or MATERIALS_JSONL),
    }
    return result


def status(*, materials_path: Path | str | None = None,
           threads_path: Path | str | None = None) -> str:
    mp = Path(materials_path) if materials_path else MATERIALS_JSONL
    tp = Path(threads_path) if threads_path else THREADS_JSONL
    lines = ["素材库状态（T5 素材获取）", ""]
    if not mp.exists():
        lines.append(f"  {mp.name} 不存在（还没跑过）")
        return "\n".join(lines)
    rows = [json.loads(l) for l in mp.read_text(encoding="utf-8").splitlines() if l.strip()]
    lines.append(f"  {mp}  共 {len(rows)} 条素材")
    by: dict[str, int] = {}
    for r in rows:
        by[r["type"]] = by.get(r["type"], 0) + 1
    for k, v in sorted(by.items()):
        lines.append(f"    {k:16} {v}")
    for t in LLM_ONLY_TYPES:
        if t not in by:
            lines.append(f"    {t:16} 0（需 LLM，未生成）")
    if tp.exists():
        n = sum(1 for l in tp.read_text(encoding="utf-8").splitlines() if l.strip())
        lines.append(f"  {tp.name}  共 {n} 个 Thread")
    n_topics = len({r.get("topic_key") for r in rows if r.get("topic_key")})
    lines.append(f"  覆盖话题 {n_topics} 个")
    return "\n".join(lines)


def report_md(res: dict[str, Any]) -> str:
    L = [f"# 素材库报告 · {res['generated_at'][:10]}", "",
         f"- 素材 **{res['n_materials']}** 条 ｜ Thread **{res['n_threads']}** 个",
         f"- 来源：{res['sources']['posts']}（{res['sources']['n_posts']} 行）· "
         f"{res['sources']['comments']}（{res['sources']['n_comments']} 行）",
         f"- 覆盖话题 {res['n_topics']} 个 ｜ 未归属话题的帖 {res['n_unattributed_posts']} 条",
         f"- 判据丢弃 {res['n_dropped_by_rule']} 条 ｜ **准入闸门拒绝 {res['n_rejected_by_gate']} 条**"
         f"（没有溯源就不算素材）",
         f"- 溯源达标率 **{res['traceable_rate']}**", "",
         "## 分类型", "",
         "| 类型 | 条数 | 说明 |", "|---|---|---|",
         "| `original_post` 原帖引用 | "
         f"{res['by_type'].get('original_post', 0)} | 纯代码（ups 降序 + ≥8 字） |",
         "| `hot_comment` 热评金句（候选） | "
         f"{res['by_type'].get('hot_comment', 0)} | 纯代码选取；**金句/段子分类待 LLM** |",
         "| `meme` 梗 | 0 | ⬜ 需 LLM（两轴分类 + 跨社区发现） |",
         "| `remix_angle` 二创角度 | 0 | ⬜ 需 LLM（必须） |", "",
         f"> **分层降级**：{res['missing_reason']}", ""]
    if res["rejected_samples"]:
        L += ["## 准入闸门拒绝样例", ""]
        for r in res["rejected_samples"]:
            L.append(f"- `{r['material_id']}`：{r['reason'][:90]}")
        L.append("")
    return "\n".join(L)


# ============================================================ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T5 素材获取 —— 素材抽取（纯代码两类）")
    ap.add_argument("--run", action="store_true", help="抽取并落盘")
    ap.add_argument("--status", action="store_true", help="看素材库现状")
    ap.add_argument("--per-topic", type=int, default=PER_TOPIC)
    ap.add_argument("--per-thread", type=int, default=PER_THREAD)
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    if args.status:
        print(status())
        return 0
    if args.run:
        res = run(per_topic=args.per_topic, per_thread=args.per_thread)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report_md(res), encoding="utf-8")
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"[素材获取] {res['generated_at'][:16]}  落盘 {res['n_materials']} 条素材"
                  f" + {res['n_threads']} 个 Thread")
            for k, v in sorted(res["by_type"].items()):
                print(f"  {k:16} {v}")
            print(f"  未生成（需 LLM）：{'、'.join(res['missing_types'])}")
            print(f"  判据丢弃 {res['n_dropped_by_rule']} ｜ 准入拒绝 {res['n_rejected_by_gate']}"
                  f" ｜ 溯源达标率 {res['traceable_rate']}")
            print(f"  → {res['path']}")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
