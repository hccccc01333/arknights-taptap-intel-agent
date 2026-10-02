"""第三层评估（规格 §4 Detection Latency / §38 Trend Evaluation / §39 Clustering Evaluation）。

★ 这一模块的第一原则：**没有 ground truth 就不给数字**。

系统"发现得好不好"必须对照外部事实（人工标注、热搜上榜时间）才能算。
本机能算的只有**不依赖外部标注**的那几项：

- Detection Latency（§4）：`first_detected_at - started_at` —— 只要事件本身有时间就能算
- Cluster Coherence（§39 的无标注代理）：簇内成员的平均相似度 + 单例率 + 簇大小分布
- 体量与覆盖分布：多少事件够得上"证据充分"

依赖 ground truth 的（Precision / Recall / Lead Time / ARI / NMI）**全部要显式传入标注才计算**，
否则返回 `status="no_ground_truth"` 并列出需要什么 —— 绝不拿内部分数当外部真值自证。
用"系统自己的 hot_score 高"去证明"系统发现得准"是循环论证，是数字污染的一种。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

EVAL_VERSION = "1.0"


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace("+00:00", ""))
    except (ValueError, TypeError):
        return None


def _pct(values: Sequence[float], q: float) -> Optional[float]:
    """分位数（线性插值）。空序列返回 None，不返回 0。"""
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return None
    if len(vs) == 1:
        return vs[0]
    pos = q * (len(vs) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(vs) - 1)
    return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)


# ---------------------------------------------------------------- §4 检测延迟

def detection_latency(events: List[Dict[str, Any]],
                      members_by_event: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                      recognition_k: int = 3) -> Dict[str, Any]:
    """Detection Latency（§4）。

    两种口径，**必须分开报**：

    - `first_seen`：first_detected_at − started_at。
      ⚠️ **在批量重放历史数据上这个值结构性地等于 0** —— 整批数据一次入库，
      "创建事件"和"最早内容"是同一个时刻。此时它**不是指标，是常量**，
      报成"平均延迟 0 小时、系统极快"是自欺欺人 → 检测到该情形会显式标 `degenerate`。

    - `recognition`（真正在这种数据上有意义的口径）：
      started_at → **第 k 条内容到达**用了多久。语义是
      「信号出现后，多久才聚集到足以被称为一个事件的证据量」（默认 k=3）。
      这个数不依赖数据是不是实时采的，所以现在能用。

    started_at 是事件内**最早一条内容的发布时间**，不是"事件真实发生的时刻"
    （后者外部不可知）—— 所以两者都是「信号出现 → 系统侧」的时差，不是真正的发现提前量。
    真正的 Lead Time 要对照外部峰值时刻，见 `detection_eval`。
    """
    rows: List[Dict[str, Any]] = []
    for e in events:
        st, fd = _parse(e.get("started_at")), _parse(e.get("first_detected_at"))
        if not st or not fd:
            continue
        rows.append({"event_id": e.get("event_id"), "title": e.get("canonical_title"),
                     "hours": round((fd - st).total_seconds() / 3600.0, 2),
                     "content_count": e.get("content_count")})
    hours = [r["hours"] for r in rows]
    if not hours:
        return {"status": "no_timestamped_events", "n": 0}

    def _dist(vals: List[float]) -> Dict[str, Any]:
        return {"mean": round(sum(vals) / len(vals), 2),
                "p50": round(_pct(vals, 0.50) or 0.0, 2),
                "p90": round(_pct(vals, 0.90) or 0.0, 2),
                "max": round(max(vals), 2), "min": round(min(vals), 2)}

    out: Dict[str, Any] = {
        "status": "ok", "n": len(rows),
        "first_seen_hours": _dist(hours),
        "negative_count": sum(1 for h in hours if h < 0),
        "note": ("started_at 取事件内最早内容的发布时间；两种口径都是「信号出现→系统侧」的时差，"
                 "不是「事件真实发生→发现」的时差（后者外部不可知，需对照外部峰值）"),
    }
    # 批量重放退化：first_detected_at 恒等于 started_at → 该口径在此数据上无信息量
    if all(abs(h) < 1e-9 for h in hours):
        out["first_seen_degenerate"] = True
        out["first_seen_warning"] = (
            "全部为 0：批量重放历史快照时「创建事件」与「最早内容」同时刻，该口径结构性失效。"
            "要看真正的发现速度请用 recognition_hours；要有提前量必须对照外部峰值（detection_eval）")
    else:
        out["first_seen_degenerate"] = False
    rows.sort(key=lambda r: -r["hours"])
    out["slowest_first_seen"] = rows[:5]

    # —— recognition：第 k 条内容到达的时间 ——
    if members_by_event:
        rec_rows: List[Dict[str, Any]] = []
        for e in events:
            st = _parse(e.get("started_at"))
            ms = members_by_event.get(e.get("event_id")) or []
            if not st or not ms:
                continue
            times = sorted(t for t in (_parse(m.get("observed_at") or m.get("published_at"))
                                       for m in ms) if t)
            if not times:
                continue
            k = min(recognition_k, len(times))
            rec_rows.append({"event_id": e.get("event_id"), "title": e.get("canonical_title"),
                             "hours": round((times[k - 1] - st).total_seconds() / 3600.0, 2),
                             "k": k, "content_count": len(ms)})
        if rec_rows:
            rec_hours = [r["hours"] for r in rec_rows]
            rec_rows.sort(key=lambda r: -r["hours"])
            out["recognition_hours"] = _dist(rec_hours)
            out["recognition_k"] = recognition_k
            out["recognition_n"] = len(rec_rows)
            out["recognition_negative_count"] = sum(1 for h in rec_hours if h < 0)
            out["slowest_recognition"] = rec_rows[:5]
        else:
            out["recognition_hours"] = None
            out["recognition_note"] = "成员内容无可用时间戳"
    return out


# ------------------------------------------------- 时间分辨率体检（前置条件）

def temporal_resolution(members_by_event: Dict[str, List[Dict[str, Any]]],
                        min_span_hours: float = 1.0) -> Dict[str, Any]:
    """★ 前置条件体检：这批数据**有没有时间分辨率**？

    L3 有一半是时间序列（velocity / acceleration / burst / 生命周期 / 检测延迟）。
    如果所有内容的观测时刻是同一个瞬间（一次性快照抓取的典型特征），
    这些指标**全部结构性为 0**，且看起来"很正常"—— 这是最危险的假数字。

    所以先测：内容时间戳的跨度与去重后的时刻数。不达标就把依赖时间的指标**整体标为不可用**，
    而不是让它们输出一片 0 让人误以为"系统很快、没有异常"。
    """
    stamps: List[datetime] = []
    for ms in members_by_event.values():
        for m in ms:
            t = _parse(m.get("observed_at") or m.get("published_at"))
            if t:
                stamps.append(t)
    if not stamps:
        return {"status": "no_timestamps",
                "inert_metrics": ["velocity", "acceleration", "burst", "lifecycle",
                                  "detection_latency", "lead_time"]}
    lo, hi = min(stamps), max(stamps)
    span = (hi - lo).total_seconds() / 3600.0
    distinct = len({s.isoformat() for s in stamps})
    inert = ["velocity", "acceleration", "burst", "lifecycle", "detection_latency", "lead_time"]
    if span < min_span_hours or distinct <= 2:
        return {
            "status": "no_temporal_resolution", "n_contents": len(stamps),
            "span_hours": round(span, 3), "distinct_timestamps": distinct,
            "inert_metrics": inert,
            "warning": ("所有内容的观测时刻集中在同一瞬间（跨度 < %.1f 小时、去重后仅 %d 个时刻）。"
                        "此时 velocity/acceleration/burst/生命周期/检测延迟 全部结构性为 0，"
                        "**不是系统性能好，是这些数据上没有时间维度可测**。"
                        "必须由第一层连续采集（每 15min~1h）积累多个观测时点后才可信。")
                       % (min_span_hours, distinct),
            "usable_metrics": ["confidence", "content_count", "platform_count",
                               "diffusion(静态占比)", "engagement", "credibility", "novelty"],
        }
    return {"status": "ok", "n_contents": len(stamps), "span_hours": round(span, 2),
            "distinct_timestamps": distinct, "inert_metrics": [],
            "note": "有时间分辨率，时间类指标可测"}


# ------------------------------------------------- §39 聚类评估（需人工标注）

def _comb2(n: int) -> float:
    return n * (n - 1) / 2.0


def clustering_metrics(pred: Dict[str, str], truth: Dict[str, str]) -> Dict[str, Any]:
    """给定预测簇标与人工标注簇标，算 Pairwise P/R/F1、ARI、NMI、Purity。

    pred/truth: {content_id: cluster_label}。只在两者**都出现**的内容上评估
    （标注不会覆盖全量，缺的不算错，但会报 `covered` 覆盖率）。
    """
    ids = [i for i in pred if i in truth]
    n = len(ids)
    if n < 2:
        return {"status": "insufficient_labels", "n": n, "need": "≥2 条同时有预测标与人工标的内容"}

    # 列联表
    table: Dict[tuple, int] = Counter((pred[i], truth[i]) for i in ids)
    a_counts = Counter(pred[i] for i in ids)     # 预测簇大小
    b_counts = Counter(truth[i] for i in ids)    # 真实簇大小

    # —— Pairwise ——
    tp = fp = fn = 0.0
    for (p, t), c in table.items():
        tp += _comb2(c)
    for p, c in a_counts.items():
        fp += _comb2(c)
    for t, c in b_counts.items():
        fn += _comb2(c)
    fp -= tp          # 预测同簇但真实不同簇
    fn -= tp          # 真实同簇但预测不同簇
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    # —— ARI（调整兰德指数，随机一致的期望被扣掉）——
    c_n2 = _comb2(n)
    sum_a = sum(_comb2(c) for c in a_counts.values())
    sum_b = sum(_comb2(c) for c in b_counts.values())
    exp = sum_a * sum_b / c_n2 if c_n2 else 0.0
    denom = 0.5 * (sum_a + sum_b) - exp
    ari = (tp - exp) / denom if denom else 0.0

    # —— NMI（归一化互信息）——
    def _entropy(counter: Counter) -> float:
        total = sum(counter.values())
        if not total:
            return 0.0
        return -sum((c / total) * math.log(c / total) for c in counter.values() if c)

    h_p, h_t = _entropy(a_counts), _entropy(b_counts)
    mi = 0.0
    for (p, t), c in table.items():
        p_joint = c / n
        p_p = a_counts[p] / n
        p_t = b_counts[t] / n
        if p_joint:
            mi += p_joint * math.log(p_joint / (p_p * p_t))
    nmi = (2 * mi / (h_p + h_t)) if (h_p + h_t) else 0.0

    # —— Purity（每簇取最多数真实类，求和 / N；簇数多时会虚高，故与 ARI 一起看）——
    best_by_pred: Dict[str, Counter] = defaultdict(Counter)
    for i in ids:
        best_by_pred[pred[i]][truth[i]] += 1
    purity = sum(max(c.values()) for c in best_by_pred.values()) / n

    return {
        "status": "ok", "n": n,
        "clusters_pred": len(a_counts), "clusters_truth": len(b_counts),
        "pairwise_precision": round(prec, 4),
        "pairwise_recall": round(rec, 4),
        "pairwise_f1": round(f1, 4),
        "ari": round(ari, 4),
        "nmi": round(nmi, 4),
        "purity": round(purity, 4),
        "covered_ratio": round(n / max(1, len(pred)), 4),
        "note": "purity 随簇数增大而虚高，判聚类好坏以 ARI / pairwise F1 为准",
    }


def cluster_coherence(members_by_event: Dict[str, List[Dict[str, Any]]],
                      sims_by_event: Optional[Dict[str, List[float]]] = None) -> Dict[str, Any]:
    """§39 的**无标注代理**：不看外部真值，只看簇自己像不像一个事件。

    - 簇内平均相似度（event_content.similarity_score）
    - 单例率（1 条内容的事件占比）—— 这个数高说明聚类基本没聚起来
    - 簇大小分布、跨平台率
    它**不能替代** ARI/NMI，只用来在没有标注时监控"聚类有没有退化"。
    """
    sizes = [len(v) for v in members_by_event.values()]
    if not sizes:
        return {"status": "no_events"}
    singles = sum(1 for s in sizes if s == 1)
    total_contents = sum(sizes)
    multi = [s for s in sizes if s > 1] or [0]
    per_event_sim: Dict[str, float] = {}
    if sims_by_event:
        for eid, sims in sims_by_event.items():
            per_event_sim[eid] = round(sum(sims) / len(sims), 4) if sims else 0.0
    coh = list(per_event_sim.values())
    cross = sum(1 for v in members_by_event.values()
                if len({m.get("platform") for m in v if m.get("platform")}) > 1)
    # ★ 单例率按"事件数"算会吓人（大量 1 条内容的小事件），但按"内容数"看可能很不一样。
    #   两个都报 —— 只有前者会让人误判聚类完全没工作。
    in_multi = sum(s for s in sizes if s > 1)
    return {
        "status": "ok", "events": len(sizes),
        "singleton_ratio": round(singles / len(sizes), 4),
        "content_coverage_in_multi": round(in_multi / total_contents, 4) if total_contents else None,
        "size_p50": round(_pct(sizes, 0.50) or 0.0, 1),
        "size_p90": round(_pct(sizes, 0.90) or 0.0, 1),
        "size_max": max(sizes),
        "multi_event_max": max(multi),
        "cross_platform_ratio": round(cross / len(sizes), 4),
        "mean_intra_similarity": round(sum(coh) / len(coh), 4) if coh else None,
        "note": "无标注时代理指标；判聚类好坏仍需人工标（clustering_metrics）",
    }


# ------------------------------------------------- §38 检测评估（需外部真值）

def detection_eval(alerts: List[Dict[str, Any]],
                   truth: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Detection Precision / Recall / Lead Time（§38）。

    alerts: 系统告警列表，每项至少 {"event_key", "detected_at"}（event_key 用于与真值对齐）。
    truth:  外部真值 {"event_key": "external_peak_at(ISO)"} —— **人工记录的热搜上榜/爆发时刻**。

    没有 truth 就返回 no_ground_truth，**不拿系统自己的分数冒充准确率**。
    """
    if not truth:
        return {
            "status": "no_ground_truth",
            "alerts": len(alerts),
            "need": "人工记录的 {event_key: 外部爆发时刻} 对照表（如微博热搜进入 Top10 的时间）",
            "why_not_estimated": "用系统 hot_score 自证 precision 是循环论证，会产出无意义的漂亮数字",
        }
    hit_keys = set(truth) & {a.get("event_key") for a in alerts}
    prec = len(hit_keys) / len(alerts) if alerts else 0.0
    rec = len(hit_keys) / len(truth) if truth else 0.0
    leads: List[float] = []
    per: List[Dict[str, Any]] = []
    by_key = {a.get("event_key"): a for a in alerts}
    for k, peak in truth.items():
        a = by_key.get(k)
        if not a:
            continue
        d, p = _parse(a.get("detected_at")), _parse(peak)
        if not d or not p:
            continue
        mins = (p - d).total_seconds() / 60.0
        leads.append(mins)
        per.append({"event_key": k, "lead_minutes": round(mins, 1)})
    per.sort(key=lambda r: -r["lead_minutes"])
    out: Dict[str, Any] = {
        "status": "ok",
        "alerts": len(alerts), "ground_truth_events": len(truth),
        "precision": round(prec, 4), "recall": round(rec, 4),
        "f1": round(2 * prec * rec / (prec + rec), 4) if (prec + rec) else 0.0,
        "lead_time_minutes": {
            "n": len(leads),
            "mean": round(sum(leads) / len(leads), 1) if leads else None,
            "p50": round(_pct(leads, 0.50) or 0.0, 1) if leads else None,
            "max": round(max(leads), 1) if leads else None,
            "negative": sum(1 for l in leads if l < 0),
        },
        "best": per[:5],
    }
    if out["lead_time_minutes"]["negative"]:
        out["warning"] = "存在负 Lead Time = 系统晚于外部爆发才发现，说明该事件上系统没有提前量"
    return out


# ---------------------------------------------------------------- 汇总入口

def evaluate(events: List[Dict[str, Any]],
             members_by_event: Optional[Dict[str, List[Dict[str, Any]]]] = None,
             sims_by_event: Optional[Dict[str, List[float]]] = None,
             cluster_truth: Optional[Dict[str, str]] = None,
             detection_truth: Optional[Dict[str, str]] = None,
             alerts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """一次算完所有能算的。缺 ground truth 的项显式标 no_ground_truth。"""
    out: Dict[str, Any] = {"eval_version": EVAL_VERSION}
    if members_by_event:
        out["temporal_resolution"] = temporal_resolution(members_by_event)
    out["detection_latency"] = detection_latency(events, members_by_event)
    if members_by_event:
        out["cluster_coherence"] = cluster_coherence(members_by_event, sims_by_event)
    else:
        out["cluster_coherence"] = {"status": "no_member_index"}
    if cluster_truth:
        pred = {}
        for eid, ms in (members_by_event or {}).items():
            for m in ms:
                pred[m.get("content_id") or m.get("id")] = eid
        out["clustering"] = clustering_metrics(pred, cluster_truth)
    else:
        out["clustering"] = {"status": "no_ground_truth",
                             "need": "人工标注 {content_id: 事件标}（同一事件的内容给同一个标）"}
    out["detection"] = detection_eval(alerts or [], detection_truth)
    return out
