"""Read-only freshness checks for the actual inputs and outputs of the pipeline.

These are data watermarks, not proof that a scheduled task ran successfully.
The check time and report generation time never renew an upstream watermark.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
DEFAULT_MAX_AGE_MINUTES = 60

_DATABASES = (
    ("ingested", "采集入库观察", "l1_source_registry.sqlite3",
     "SELECT COUNT(*), MAX(observed_at) FROM metric_snapshot"),
    ("processed", "清洗库观察", "l2_processed.sqlite3",
     "SELECT COUNT(*), MAX(observed_at) FROM content"),
    ("trend", "趋势事件采用的观察", "l3_trend.sqlite3",
     "SELECT COUNT(*), MAX(last_updated_at) FROM trend_event WHERE status='active'"),
    ("analysis", "六层链情报分析库", "l4_intelligence.sqlite3",
     "SELECT COUNT(*), MAX(created_at) FROM intelligence_analysis"),
)
_REPORTS = (
    ("forming", "外部热点快照", "forming_report.json", "forming_all"),
    ("communities", "社区报告产物", "community_reports.json", "reports"),
    ("creatives", "增长创意产物", "growth_creatives.json", "creatives"),
)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # Historical local database timestamps omit an offset; interpret in Shanghai.
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def _stage(key: str, label: str, filename: str, latest: Any, count: int,
           now: datetime, max_age_minutes: float, *, empty_is_valid: bool = False) -> dict[str, Any]:
    timestamp = parse_time(latest)
    age = (now - timestamp).total_seconds() / 60 if timestamp else None
    status = ("empty" if count == 0 and not empty_is_valid else "unknown" if timestamp is None else
              "invalid" if age < -5 else "stale" if age > max_age_minutes else "ok")
    return {"key": key, "label": label, "file": filename, "status": status,
            "records": count, "latest_at": timestamp.isoformat() if timestamp else None,
            "age_minutes": round(max(0, age), 1) if age is not None else None}


def _unavailable(key: str, label: str, filename: str, status: str,
                 detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "file": filename, "status": status,
            "records": None, "latest_at": None, "age_minutes": None, "detail": detail}


def build_health(root: Path | str | None = None, *, now: datetime | None = None,
                 max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES) -> dict[str, Any]:
    if not math.isfinite(max_age_minutes) or max_age_minutes <= 0:
        raise ValueError("max_age_minutes must be positive and finite")
    base = Path(root) if root is not None else ROOT
    state = base / "data" / "state"
    now = now or datetime.now(TZ)
    now = now.replace(tzinfo=TZ) if now.tzinfo is None else now.astimezone(TZ)
    stages: list[dict[str, Any]] = []
    for key, label, filename, query in _DATABASES:
        path = state / filename
        if not path.is_file():
            stages.append(_unavailable(key, label, filename, "missing", "尚未建立数据库"))
            continue
        connection = None
        try:
            # mode=ro prevents accidental database creation or schema changes.
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=1)
            count, latest = connection.execute(query).fetchone()
            stages.append(_stage(key, label, filename, latest, count, now, max_age_minutes))
        except (sqlite3.Error, OSError) as error:
            stages.append(_unavailable(key, label, filename, "unavailable", str(error)))
        finally:
            if connection is not None:
                connection.close()
    for key, label, filename, items_key in _REPORTS:
        path = state / filename
        if not path.is_file():
            stages.append(_unavailable(key, label, filename, "missing", "尚未生成产物"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get(items_key), list):
                raise ValueError("产物结构无效")
            stages.append(_stage(key, label, filename, data.get("generated_at"),
                                 len(data[items_key]), now, max_age_minutes, empty_is_valid=True))
        except (OSError, ValueError) as error:
            stages.append(_unavailable(key, label, filename, "unavailable", str(error)))

    warnings: list[str] = []
    for stage in stages:
        status, label = stage["status"], stage["label"]
        if status == "stale":
            warnings.append(f"{label}截至 {stage['latest_at']}，已超过"
                            f" {max_age_minutes:g} 分钟的新鲜度检查阈值。")
        elif status != "ok":
            reason = {"empty": "没有记录", "unknown": "缺少有效时间戳",
                      "invalid": "时间戳晚于检查时间，请核对时钟",
                      "missing": "产物缺失", "unavailable": "暂时无法读取"}[status]
            warnings.append(f"{label}：{reason}。")

    by_key = {s["key"]: s for s in stages}
    l2_at = parse_time(by_key["processed"]["latest_at"])
    l3_at = parse_time(by_key["trend"]["latest_at"])
    lag = (l2_at - l3_at).total_seconds() / 60 if l2_at and l3_at else None
    if lag is not None and lag > max_age_minutes:
        warnings.append("趋势事件采用的观察时间落后于清洗库，"
                        "请核查趋势处理是否接上了新增数据。")
    return {"checked_at": now.isoformat(timespec="seconds"),
            "max_age_minutes": max_age_minutes,
            "status": "ok" if not warnings else "degraded",
            "stages": stages, "warnings": warnings,
            "observation_lag_minutes": round(max(0, lag), 1) if lag is not None else None,
            "note": "检查时间不代表数据更新时间；水位只描述现存数据，"
                    "不能单独证明任务停跑或全网覆盖。阈值可调，尚未按各渠道校准。"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--max-age-minutes", type=float, default=DEFAULT_MAX_AGE_MINUTES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        health = build_health(args.root, max_age_minutes=args.max_age_minutes)
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
    else:
        print(f"数据链检查 · {health['checked_at']} · {health['status']}")
        for stage in health["stages"]:
            print(f"  {stage['label']}: {stage['status']} / "
                  f"{stage['latest_at'] or '时间未知'} / {stage['records']} 条")
        for warning in health["warnings"]:
            print(f"  提示：{warning}")
        print(health["note"])


if __name__ == "__main__":
    main()
