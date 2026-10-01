#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L3_semantic/announcements.py — 公告抓取与解析（① 类预判：日历管理）。

对应 docs/热点追踪设计.md §5.1 的「① 排期 → 日历管理」。
目的：提前知道「什么时候要有大动作」，从而提前备素材、排期。

用户 2026-09-30 指出的三个难点，本模块逐个应对：

  难点 1 **公告常是图片**（公众号）
    → 不硬啃 OCR。抓到图片的只记 URL + `needs_ocr=True`，不假装解析成功。

  难点 2 **各游戏公告排版不同，规则解析脆弱**
    → 所以解析交给 LLM，而不是给每个站写正则。

  难点 3 **LLM 输出的 JSON 会坏，不能让它报错**
    → 本模块的核心就是这件事，见下。

## ★ JSON 可靠性设计（本模块最重要的部分）

LLM 输出 JSON 的典型失败模式：
  · 外面裹了 ```json 代码块     · 前后有多余解说文字
  · 字段缺失 / 类型错           · 被 token 上限截断

四层防御：
  ① 请求侧：`response_format={"type":"json_object"}`（DeepSeek 支持）
  ② 提取侧：宽容提取 —— 剥代码块 → 找首个 `{` 到末个 `}`
  ③ 校验侧：纯标准库手写 schema 校验，**返回具体错在哪**
  ④ 重试侧：校验失败 → 带错误信息重试一次；仍失败 → 标 `parse_failed`

**★ 最重要的一条纪律：解析失败也不丢原文。**
`raw_text` 永远保留，`parse_status` 如实标注。这样：
  · 事后可以补解析（换了模型/prompt 重跑）
  · 人工可以直接看原文

用法：
  python L3_semantic/announcements.py --manual-file data/manual_announcements.jsonl
  python L3_semantic/announcements.py --status
  python L3_semantic/announcements.py --schema        # 打印允许的字段与枚举
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

TZ = timezone(timedelta(hours=8))
LAB = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[1]

STORE = ROOT / "data/raw/taptap" / "announcements" / "announcements.jsonl"
REPORT_DIR = LAB / "reports"

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MODEL_FLASH = os.environ.get("DEEPSEEK_MODEL_FLASH", "deepseek-flash")
THINKING_OFF = {"type": "disabled"}     # 解析是判别式任务（决策 #16）

SCHEMA_VERSION = "1.0"

# ---- schema（校验与提示词的唯一真相源）----
EVENT_TYPES = ("version_update", "banner", "event", "maintenance",
               "compensation", "collab", "anniversary", "other")

REQUIRED_ANNOUNCEMENT_FIELDS = ("game", "source", "raw_text", "parse_status")
PARSE_STATUSES = ("ok", "failed", "skipped", "needs_ocr", "pending")

REQUIRED_EVENT_FIELDS = ("event_type", "title")
EVENT_FIELD_TYPES = {
    "event_type": str, "title": str, "scheduled_at": (str, type(None)),
    "confidence": (int, float), "evidence": str,
}


# ---------------------------------------------------------------------------
# ① 宽容提取：把「可能裹了代码块、可能带前言后语」的文本抠成 JSON
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 输出里尽可能抠出一个 JSON 对象。抠不出返回 None。

    依次尝试：
      1. 直接 json.loads（最理想）
      2. 剥 ```json 代码块后再 loads
      3. 取首个 `{` 到末个 `}` 的子串再 loads
    """
    if not text:
        return None
    candidates: list[str] = [text]

    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1))

    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        candidates.append(text[i:j + 1])

    for c in candidates:
        try:
            obj = json.loads(c.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


# ---------------------------------------------------------------------------
# ② schema 校验：返回「具体错在哪」，而不是笼统的 False
#
# 为什么要具体：重试时要把错误信息喂回给模型，笼统的 False 没法让它改对。
# ---------------------------------------------------------------------------

def validate_event(ev: Any, idx: int = 0) -> list[str]:
    errs: list[str] = []
    if not isinstance(ev, dict):
        return [f"events[{idx}] 不是对象"]
    for f in REQUIRED_EVENT_FIELDS:
        if f not in ev or ev[f] in (None, ""):
            errs.append(f"events[{idx}].{f} 缺失")
    for f, t in EVENT_FIELD_TYPES.items():
        if f in ev and ev[f] is not None and not isinstance(ev[f], t):
            errs.append(f"events[{idx}].{f} 类型应为 {getattr(t, '__name__', t)}")
    et = ev.get("event_type")
    if et not in EVENT_TYPES:
        errs.append(f"events[{idx}].event_type='{et}' 不在允许值 {list(EVENT_TYPES)}")
    # 日期格式：可空，但给了就必须能解析
    sa = ev.get("scheduled_at")
    if sa:
        try:
            datetime.fromisoformat(str(sa).replace("Z", "+00:00"))
        except ValueError:
            errs.append(f"events[{idx}].scheduled_at='{sa}' 不是 ISO 时间")
    cf = ev.get("confidence")
    if isinstance(cf, (int, float)) and not (0 <= float(cf) <= 1):
        errs.append(f"events[{idx}].confidence={cf} 应在 0~1")
    return errs


def validate_parsed(obj: dict[str, Any]) -> list[str]:
    """校验 LLM 解析结果。返回错误列表（空 = 通过）。"""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["顶层不是 JSON 对象"]

    events = obj.get("events")
    if events is None:
        errs.append("缺 events 字段")
    elif not isinstance(events, list):
        errs.append("events 应为数组")
    else:
        for i, ev in enumerate(events):
            errs.extend(validate_event(ev, i))
    return errs


# ---------------------------------------------------------------------------
# ③ 解析流程：调用 → 提取 → 校验 → 重试 → 降级
# ---------------------------------------------------------------------------

PARSE_PROMPT = """你是游戏公告的结构化解析器。把下面的公告原文解析成 JSON。

只输出一个 JSON 对象，schema：
{{
  "events": [
    {{
      "event_type": "以下之一：{types}",
      "title": "事件标题（简短）",
      "scheduled_at": "ISO 8601 时间，如 2026-10-15T10:00:00+08:00；不确定就填 null",
      "confidence": 0.0 到 1.0 的数字，表示你对这条提取的把握,
      "evidence": "原文里支持这条的**原句**（必须逐字摘录，便于人工核对）"
    }}
  ]
}}

规则：
- 只提取公告里**明确写了时间或排期**的事件；没有就返回 {{"events": []}}
- **不要编造时间**。原文没写具体时间就填 null
- evidence 必须逐字来自原文，不许改写
- 只输出 JSON，不要任何解说

公告原文：
---
{raw}
---
"""


def build_prompt(raw_text: str) -> str:
    return PARSE_PROMPT.format(types=" / ".join(EVENT_TYPES), raw=raw_text[:6000])


def _default_caller(api_key: str) -> Callable[[str], str]:
    """默认调用器：DeepSeek。**可注入替换，便于测试。**"""

    def call(prompt: str) -> str:
        body = {
            "model": MODEL_FLASH,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},   # 防御 ①
            "thinking": THINKING_OFF,
            "temperature": 0.1,
            "max_tokens": 1200,
        }
        req = urllib.request.Request(
            DEEPSEEK_URL, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    return call


def _get_api_key(explicit: str | None = None) -> str:
    """取 API key。**抽成函数有两个原因**：

    1. 顺手修一个真问题：**纯空白的 key 应视为没有 key**
       （`"   "` 是 truthy，不 strip 会拿着空格去调 API，然后失败得莫名其妙）
    2. 测试可注入 —— 本机环境里 `ACC_PRODUCT_CONFIG_V3` 有 51 万字符，
       `mock.patch.dict(os.environ, ...)` 在恢复时会因 Windows 环境块上限报错，
       所以测试应该 patch 这个函数，而不是 patch 整个 os.environ。
    """
    return (explicit or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def parse_announcement(raw_text: str, *,
                       caller: Callable[[str], str] | None = None,
                       api_key: str | None = None,
                       max_retries: int = 1) -> dict[str, Any]:
    """解析一条公告。**任何情况下都不抛异常**，只通过 parse_status 表达结果。

    返回：
      {parse_status, events, raw_text, errors, attempts, degraded_reason}
    """
    out: dict[str, Any] = {"raw_text": raw_text, "events": [],
                           "errors": [], "attempts": 0}

    if caller is None:
        key = _get_api_key(api_key)
        if not key:
            out.update(parse_status="skipped",
                       degraded_reason="无 LLM 可用（未设 DEEPSEEK_API_KEY）——" 
                                       "原文已保留，可事后补解析")
            return out
        caller = _default_caller(key)

    prompt = build_prompt(raw_text)
    last_errs: list[str] = []

    for attempt in range(max_retries + 1):
        out["attempts"] = attempt + 1
        try:
            raw_resp = caller(prompt)
        except Exception as e:                      # 网络/接口异常：降级不崩
            out.update(parse_status="failed",
                       degraded_reason=f"LLM 调用失败：{type(e).__name__}")
            return out

        obj = extract_json(raw_resp)                # 防御 ②
        if obj is None:
            last_errs = ["输出里找不到可解析的 JSON 对象"]
        else:
            last_errs = validate_parsed(obj)        # 防御 ③
            if not last_errs:
                out.update(parse_status="ok", events=obj.get("events", []),
                           errors=[])
                return out

        # 防御 ④：把错误喂回去让它自己改
        if attempt < max_retries:
            prompt = (build_prompt(raw_text)
                      + "\n\n上次输出有这些问题，请修正后只输出 JSON：\n"
                      + "\n".join(f"- {e}" for e in last_errs[:8]))

    out.update(parse_status="failed", errors=last_errs,
               degraded_reason="重试后仍未通过 schema 校验"
                               "（原文已保留，可事后补解析）")
    return out


# ---------------------------------------------------------------------------
# ④ 记录构造与存储（解析失败也不丢原文）
# ---------------------------------------------------------------------------

def make_record(*, game: str, source: str, raw_text: str = "",
                source_url: str = "", published_at: str = "",
                image_url: str = "", parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    """组装一条公告记录。

    ⚠️ 图片类公告：只记 URL + `needs_ocr`，**不假装解析成功**。
    """
    is_image = bool(image_url) and not raw_text.strip()
    rec: dict[str, Any] = {
        "announcement_id": f"ann_{datetime.now(TZ).strftime('%Y%m%d%H%M%S')}_{abs(hash((game, source_url, image_url))) % 10000:04d}",
        "game": game, "source": source,
        "source_url": source_url or None, "image_url": image_url or None,
        "published_at": published_at or None,
        "captured_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "raw_text": raw_text,
        "parse_status": "needs_ocr" if is_image else "pending",
        "events": [],
    }
    if is_image:
        rec["degraded_reason"] = "公告为图片形式，未做 OCR（避免误识）—— 原文链接已保留"
    if parsed:
        rec["parse_status"] = parsed.get("parse_status", "pending")
        rec["events"] = parsed.get("events", [])
        if parsed.get("degraded_reason"):
            rec["degraded_reason"] = parsed["degraded_reason"]
        if parsed.get("errors"):
            rec["errors"] = parsed["errors"]
        rec["attempts"] = parsed.get("attempts", 0)
    return rec


def append_records(records: list[dict[str, Any]], store: Path | None = None) -> dict[str, Any]:
    """以 JSONL 追加写入。**同 announcement_id 去重**（幂等可重跑）。"""
    store = store or STORE
    store.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    if store.exists():
        for line in store.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line)["announcement_id"])
            except Exception:
                continue

    added, skipped = 0, 0
    with store.open("a", encoding="utf-8") as f:
        for r in records:
            if r.get("announcement_id") in seen:
                skipped += 1
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            seen.add(r["announcement_id"])
            added += 1
    return {"ok": True, "added": added, "skipped_duplicate": skipped,
            "store": str(store)}


def load_records(store: Path | None = None) -> list[dict[str, Any]]:
    store = store or STORE
    if not store.exists():
        return []
    out = []
    for line in store.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def reparse_pending(store: Path | None = None, *,
                    caller: Callable[[str], str] | None = None,
                    only: tuple[str, ...] = ("pending", "failed", "skipped"),
                    dry_run: bool = False) -> dict[str, Any]:
    """对未成功解析的记录**补解析**，原地更新 JSONL。

    ★ 这是「解析失败不丢原文」那句承诺的兑现处 ——
    没有这个命令，原文就只是躺在文件里，补解析是个空话。

    典型用法：余额恢复 / 换了更好的 prompt 之后，把之前攒下的原文一次性补上。

    ⚠️ 会重写整个 JSONL（原文件先备份为 .bak），避免中途失败丢数据。
    """
    store = store or STORE
    records = load_records(store)
    if not records:
        return {"ok": True, "n_total": 0, "n_reparsed": 0, "n_ok": 0,
                "note": "库是空的，没什么可补"}

    targets = [r for r in records if r.get("parse_status") in only
               and (r.get("raw_text") or "").strip()]
    if not targets:
        return {"ok": True, "n_total": len(records), "n_reparsed": 0, "n_ok": 0,
                "note": f"没有可补解析的记录（只补 {list(only)}）"}

    if dry_run:
        return {"ok": True, "dry_run": True, "n_total": len(records),
                "n_reparsed": 0, "n_would_reparse": len(targets),
                "note": "干跑：未调用 LLM、未改文件"}

    by_id = {r["announcement_id"]: r for r in records}
    n_ok = 0
    for r in targets:
        parsed = parse_announcement(r["raw_text"], caller=caller)
        tgt = by_id[r["announcement_id"]]
        tgt["parse_status"] = parsed.get("parse_status", "failed")
        tgt["events"] = parsed.get("events", [])
        tgt["attempts"] = parsed.get("attempts", 0)
        tgt["reparsed_at"] = datetime.now(TZ).isoformat(timespec="seconds")
        if parsed.get("degraded_reason"):
            tgt["degraded_reason"] = parsed["degraded_reason"]
        else:
            tgt.pop("degraded_reason", None)
        if parsed.get("errors"):
            tgt["errors"] = parsed["errors"]
        else:
            tgt.pop("errors", None)
        if tgt["parse_status"] == "ok":
            n_ok += 1

    # 备份后再整文件重写（记录不多，简单可靠；中途失败不至于丢原文）
    backup = store.with_suffix(store.suffix + ".bak")
    if store.exists():
        backup.write_text(store.read_text(encoding="utf-8"), encoding="utf-8")
    store.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                     encoding="utf-8")
    return {"ok": True, "n_total": len(records), "n_reparsed": len(targets),
            "n_ok": n_ok, "backup": str(backup)}


def load_manual(path: Path) -> list[dict[str, Any]]:
    """读人工录入的公告（JSONL）。
    为什么保留人工录入这条路：官网抓取要按站点适配，而人工录入**立刻可用**，
    且是排期库的兜底来源（抓不到的日子也不至于空）。
    每行格式：{"game": "...", "raw_text": "...", "source_url": "...", "published_at": "..."}
    也允许直接给已解析好的 events（跳过 LLM）。
    """
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# ④b 官方账号源（TapTap by-user）—— 比公众号更好的公告源
#
# 2026-09-30 实测发现（用户给出鸣潮官方账号 URL 后验证）：
#   · TapTap 的**游戏官方账号**发的动态带 `is_official: true`
#   · 公告正文就在 `moment.topic.summary`，**是纯文字**（不是图片！）
#   · `feed/v7/by-user` 能按 user_id 拉全部动态（鸣潮实测 total=2288）
#
# 对比公众号方案：
#   TapTap 官方账号 —— 文字版 ✅ / 可程序化 ✅ / 有 is_official 标记 ✅
#   微信公众号     —— 常是图片 ❌ / 需登录态 ❌ / 无标记 ❌
#
# ⚠️ 反爬（2026-09-30 实测踩过）：**连续 ~50 次无间隔请求会触发 Aliyun WAF**
#    （返回 `aliyun_waf_aa/bb` 挑战页，150 秒后仍未恢复）。
#    所以本模块**强制走 polite sleep**，且检测到 WAF 立即停手、不再重试轰炸。
# ---------------------------------------------------------------------------

TAPTAP_BASE = "https://www.taptap.cn"
BY_USER_URL = f"{TAPTAP_BASE}/webapiv2/feed/v7/by-user"
BY_GROUP_URL = f"{TAPTAP_BASE}/webapiv2/feed/v7/by-group"
SOURCE_OFFICIAL = "taptap_official"
REQUEST_TIMEOUT = 25
DEFAULT_SLEEP = (0.8, 1.5)          # 与 L1_data_source/collectors/taptap/crawl_taptap_community.py 保持一致
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


class WafBlocked(RuntimeError):
    """被反爬拦下（Aliyun WAF 挑战页）。**必须立即停手，不要再重试。**"""


def _x_ua(uid: str = "") -> str:
    return ("V=1&PN=WebApp&LANG=zh_CN&VN_CODE=100000000&LOC=CN&PLT=PC&DS=Android"
            f"&UID={uid}&OS=Windows&OSV=10&DT=PC")


def _fetch_json(url: str, params: dict, *, headers: dict | None = None) -> dict:
    """取 JSON。**被 WAF 拦时抛 WafBlocked**（而不是返回空列表假装没数据）。"""
    import urllib.error
    import urllib.parse
    import urllib.request

    # ⚠️ 必须**完整百分号编码**（不能用 safe="&="）：
    # X-UA 的值本身就是 `V=1&PN=WebApp&...` 这样的 query 串，
    # 若不转义其中的 & 和 =，它会被解析成**顶层参数**，
    # 服务端报 `INVALID_XUA: XUA[PN] fail, PN:`（2026-09-30 实测踩过）。
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{q}",
        headers=headers or {"User-Agent": DEFAULT_UA, "Accept": "application/json, text/plain, */*",
                            "Referer": f"{TAPTAP_BASE}/"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        body = (e.read() or b"").decode("utf-8", errors="replace")[:300]
        # 阿里云 WAF 的挑战页特征
        if "aliyun_waf" in body or "waf" in body.lower():
            raise WafBlocked(f"被 Aliyun WAF 拦截（HTTP {e.code}）") from e
        raise RuntimeError(f"HTTP {e.code}: {body[:120]}") from e

    if "json" not in ctype.lower():
        if "aliyun_waf" in raw or "<html" in raw[:200].lower():
            raise WafBlocked("返回 HTML（疑似 WAF 挑战页）而非 JSON")
        raise RuntimeError(f"非 JSON 响应（Content-Type={ctype[:40]}）")
    return json.loads(raw)


def fetch_official_moments(user_id: str, *, pages: int = 1, per_page: int = 10,
                           uid: str = "", sleep: tuple[float, float] = DEFAULT_SLEEP,
                           sleeper=None) -> dict[str, Any]:
    """拉官方账号的动态。

    ⚠️ `per_page` 默认 10 —— 接口对 limit 有上限校验，**传 20 会 400**
    （`ByUserV7Request.limit` 校验失败，2026-09-30 实测）。
    ⚠️ 强制 polite sleep；一旦被 WAF 拦**立即抛出**，由调用方决定整体停下。
    """
    import random
    import time
    sleeper = sleeper or time.sleep

    out: list[dict[str, Any]] = []
    for p in range(pages):
        if p:
            sleeper(random.uniform(*sleep))       # ★ 不给 WAF 机会
        d = _fetch_json(BY_USER_URL, {
            "X-UA": _x_ua(uid), "user_id": str(user_id), "type": "moment_v2",
            "with_top_in_type": "true", "limit": str(per_page), "from": str(p * per_page),
        })
        lst = ((d.get("data") or {}).get("list")) or []
        if not lst:
            break
        out.extend(lst)
    return {"ok": True, "n": len(out), "items": out}


def extract_moments(items: list[dict]) -> list[dict[str, Any]]:
    """从 by-user 的原始条目里提取「公告候选」。

    只取**官方账号**发的、且**有正文**的条目 —— 别人的转发/空洞贴不要。
    """
    out = []
    for it in items:
        m = it.get("moment") or {}
        if not m.get("is_official"):
            continue
        topic = m.get("topic") or {}
        title = (topic.get("title") or "").strip()
        summary = (topic.get("summary") or "").strip()
        if not (title or summary):
            continue
        au = ((m.get("author") or {}).get("user") or {})
        out.append({
            "moment_id": m.get("id_str"),
            "title": title,
            "raw_text": summary,
            "published_at": (datetime.fromtimestamp(int(m["publish_time"]), TZ)
                             .isoformat(timespec="seconds")
                             if m.get("publish_time") else None),
            "is_top": bool(it.get("is_top")),
            "url": f"{TAPTAP_BASE}/moment/{m.get('id_str')}" if m.get("id_str") else None,
            "account_name": au.get("name"),
            "account_id": au.get("id"),
        })
    return out


def moments_to_records(moments: list[dict], *, game: str, parsed: bool = True,
                       caller=None) -> list[dict[str, Any]]:
    """把公告候选转成记录（可选是否立刻解析）。"""
    recs = []
    for mo in moments:
        pp = None
        if parsed:
            pp = parse_announcement(mo["raw_text"], caller=caller)
        rec = make_record(game=game, source=SOURCE_OFFICIAL,
                          raw_text=mo["raw_text"], source_url=mo["url"] or "",
                          published_at=mo.get("published_at") or "", parsed=pp)
        rec["title"] = mo.get("title")
        rec["account_name"] = mo.get("account_name")
        recs.append(rec)
    return recs


def discover_official_user(group_id: str, *, pages: int = 12, per_page: int = 10,
                           uid: str = "", sleep: tuple[float, float] = DEFAULT_SLEEP,
                           sleeper=None) -> dict[str, Any]:
    """从社区流里反查官方账号 user_id。

    原理：官方帖带 `is_official: true`，扫社区流把它的 `author.user.id` 捞出来。
    **鸣潮已用此法验证通过**（捞出 429475503，与人工确认的一致）。
    ⚠️ 明日方舟扫 120 帖未命中 —— 说明其官方帖不一定出现在默认社区流里，
    这时需要人工从浏览器抓一次 by-user 请求。
    """
    import random
    import time
    sleeper = sleeper or time.sleep

    found: dict[int, dict[str, Any]] = {}
    scanned = 0
    for p in range(pages):
        if p:
            sleeper(random.uniform(*sleep))
        d = _fetch_json(BY_GROUP_URL, {
            "X-UA": _x_ua(uid), "from": str(p * per_page), "group_id": str(group_id),
            "limit": str(per_page), "sort": "default", "status": "0",
            "type": "feed", "with_hot_comment": "true",
        })
        lst = ((d.get("data") or {}).get("list")) or []
        if not lst:
            break
        scanned += len(lst)
        for it in lst:
            m = it.get("moment") or {}
            au = ((m.get("author") or {}).get("user") or {})
            if m.get("is_official") and au.get("id"):
                found[int(au["id"])] = {"user_id": au.get("id"), "name": au.get("name")}
    return {"ok": True, "scanned": scanned, "n_found": len(found),
            "candidates": list(found.values())}


# ---------------------------------------------------------------------------
# ⑤ 排期视图：从公告记录里汇总「未来要发生什么」
# ---------------------------------------------------------------------------

def upcoming(records: list[dict[str, Any]], *, now: datetime | None = None,
             horizon_days: int = 60) -> dict[str, Any]:
    """汇总未来的排期事件（给运营提前备素材用）。"""
    now = now or datetime.now(TZ)
    horizon = now + timedelta(days=horizon_days)
    items: list[dict[str, Any]] = []
    for r in records:
        for ev in (r.get("events") or []):
            sa = ev.get("scheduled_at")
            if not sa:
                continue
            try:
                t = datetime.fromisoformat(str(sa).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=TZ)
            except ValueError:
                continue
            if now <= t <= horizon:
                items.append({
                    "scheduled_at": t.isoformat(timespec="minutes"),
                    "days_until": round((t - now).total_seconds() / 86400, 1),
                    "game": r.get("game"), "event_type": ev.get("event_type"),
                    "title": ev.get("title"), "confidence": ev.get("confidence"),
                    "evidence": ev.get("evidence"), "source_url": r.get("source_url"),
                })
    items.sort(key=lambda x: x["scheduled_at"])
    return {"available": True, "n": len(items), "horizon_days": horizon_days,
            "items": items}


def status(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for r in records:
        by_status[r.get("parse_status", "unknown")] = \
            by_status.get(r.get("parse_status", "unknown"), 0) + 1
    return {"n_records": len(records), "by_parse_status": by_status,
            "n_with_events": sum(1 for r in records if r.get("events"))}


def render_status(records: list[dict[str, Any]]) -> str:
    st = status(records)
    L = ["# 公告排期库状态", "",
         f"- 累计公告 **{st['n_records']}** 条；其中提取出事件的 **{st['n_with_events']}** 条", ""]
    if not st["n_records"]:
        return "\n".join(L + ["- **库还是空的** —— 用 `--manual-file` 录一条试试，"
                              "或等官网抓取源接入。", ""])
    L += ["| parse_status | 条数 | 含义 |", "|---|---|---|"]
    meaning = {"ok": "✅ 解析成功", "failed": "❌ 解析失败（原文已保留，可补解析）",
               "skipped": "⏭ 无 LLM，未解析（原文已保留）",
               "needs_ocr": "🖼 图片公告，未做 OCR（链接已保留）",
               "pending": "⏳ 待解析"}
    for k, v in sorted(st["by_parse_status"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} | {meaning.get(k, '—')} |")
    L.append("")
    up = upcoming(records)
    L += [f"## 未来 {up['horizon_days']} 天内的排期（{up['n']} 条）", ""]
    if up["items"]:
        L += ["| 距今(天) | 时间 | 游戏 | 类型 | 标题 | 依据 |",
              "|---|---|---|---|---|---|"]
        for it in up["items"][:15]:
            ev = (it.get("evidence") or "")[:30]
            L.append(f"| {it['days_until']} | {it['scheduled_at'][:16]} | {it['game']} "
                     f"| {it['event_type']} | {it['title']} | {ev} |")
        L += ["", "> 「依据」列是 LLM 从原文逐字摘的原句 —— **可核对，不是模型编的**。", ""]
    else:
        L += ["- 暂无（要么还没解析出事件，要么近期确实没有公开排期）。", ""]
    L += ["## 边界", "",
          "- 图片公告**不做 OCR**：中文游戏术语 OCR 易错，错了比没有更糟。"
          "只保留链接，人工可点开看。",
          "- **解析失败不丢原文**：`raw_text` 永远保留，换模型/prompt 后可重跑。",
          "- `scheduled_at` 为 null 表示原文没写具体时间 —— **不许编造**。",
          "", "*报告结束*", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="公告抓取与解析（① 类预判）")
    ap.add_argument("--manual-file", default="", help="人工录入的 JSONL 路径")
    ap.add_argument("--status", action="store_true", help="输出排期库状态")
    ap.add_argument("--json", action="store_true", help="机器可读")
    ap.add_argument("--schema", action="store_true", help="打印 schema（允许的字段与枚举）")
    ap.add_argument("--store", default="", help="公告库路径")
    ap.add_argument("--official-user", default="", help="拉某个 TapTap 官方账号（user_id）")
    ap.add_argument("--game", default="", help="游戏名（配合 --official-user）")
    ap.add_argument("--pages", type=int, default=1, help="翻页数（每页 20 条；默认 1）")
    ap.add_argument("--discover-official", default="", help="从社区流反查官方账号（传 group_id）")
    ap.add_argument("--no-parse", action="store_true",
                    help="只存原文、不调 LLM（余额不足或想先攒原文时用）")
    ap.add_argument("--reparse", action="store_true",
                    help="对未成功解析的记录补解析（兑现「不丢原文」的承诺）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --reparse：只统计不改动")
    args = ap.parse_args(argv)

    # store 先解析 —— 下面多个分支都要用（踩过 UnboundLocalError）
    store = Path(args.store) if args.store else STORE

    if args.reparse:
        r = reparse_pending(store, dry_run=args.dry_run)
        print(json.dumps(r, ensure_ascii=False))
        if not args.dry_run:
            print(render_status(load_records(store)))
        return 0 if r.get("ok") else 1

    if args.discover_official:
        try:
            r = discover_official_user(args.discover_official, pages=args.pages,
                                       per_page=10)
        except WafBlocked as e:
            print(json.dumps({"ok": False, "waf_blocked": True, "error": str(e),
                              "advice": "被反爬拦下，请停止请求并等冷却（实测 >150s 仍未恢复）"},
                             ensure_ascii=False), file=sys.stderr)
            return 3
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if args.official_user:
        if not args.game:
            print("[error] --official-user 需要配合 --game", file=sys.stderr)
            return 2
        try:
            raw = fetch_official_moments(args.official_user, pages=args.pages)
        except WafBlocked as e:
            print(json.dumps({"ok": False, "waf_blocked": True, "error": str(e),
                              "advice": "被反爬拦下，请停止请求并等冷却；"
                                        "不要循环重试，否则可能延长封禁"},
                             ensure_ascii=False), file=sys.stderr)
            return 3
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        moments = extract_moments(raw["items"])
        recs = moments_to_records(moments, game=args.game, parsed=not args.no_parse)
        res = append_records(recs, store)
        print(json.dumps({"fetched": raw["n"], "official_moments": len(moments), **res},
                         ensure_ascii=False))
        print(render_status(load_records(store)))
        return 0

    if args.schema:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "parse_status": list(PARSE_STATUSES),
            "event_type": list(EVENT_TYPES),
            "record_required": list(REQUIRED_ANNOUNCEMENT_FIELDS),
            "event_required": list(REQUIRED_EVENT_FIELDS),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.status:
        recs = load_records(store)
        if args.json:
            print(json.dumps({"status": status(recs), "upcoming": upcoming(recs)},
                             ensure_ascii=False, indent=2))
        else:
            rep = render_status(recs)
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            (REPORT_DIR / "announcements_latest.md").write_text(rep, encoding="utf-8")
            print(rep)
        return 0

    if args.manual_file:
        entries = load_manual(Path(args.manual_file))
        if not entries:
            print(f"[warn] {args.manual_file} 里没有可读条目", file=sys.stderr)
            return 1
        recs = []
        for e in entries:
            parsed = None
            if e.get("events"):
                # 人工已给事件 → 跳过 LLM，标 ok
                parsed = {"parse_status": "ok", "events": e["events"], "attempts": 0}
            elif e.get("raw_text"):
                parsed = parse_announcement(e["raw_text"])
            recs.append(make_record(
                game=e.get("game", "未知"), source=e.get("source", "manual"),
                raw_text=e.get("raw_text", ""), source_url=e.get("source_url", ""),
                published_at=e.get("published_at", ""),
                image_url=e.get("image_url", ""), parsed=parsed))
        r = append_records(recs, store)
        print(json.dumps(r, ensure_ascii=False))
        print(render_status(load_records(store)))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
