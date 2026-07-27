#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""将待标 review 切成多片，并行开多条标注线；完成后合并进主表。"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "02数据"
ANN = ROOT / "03标注结果"
CLEAN = DATA / "processed" / "reviews_clean.csv"
MAIN_CSV = ANN / "annotations_v1_4.csv"
SHARD_DIR = ANN / "shards"
TZ = timezone(timedelta(hours=8))
SCRIPT = DATA / "annotate_reviews.py"


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("review_id") and not row.get("error"):
                done.add(str(row["review_id"]))
    return done


def prepare_shards(n_shards: int) -> list[Path]:
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    # 清掉旧分片 id 列表（保留已写部分结果以便续跑）
    clean_ids = [
        str(r["review_id"])
        for r in csv.DictReader(CLEAN.open(encoding="utf-8-sig"))
    ]
    done = load_done(MAIN_CSV)
    # 分片文件里已成功的也算 done
    for p in SHARD_DIR.glob("annotations_v1_4_shard*.csv"):
        done |= load_done(p)
    pending = [rid for rid in clean_ids if rid not in done]
    print(f"[prepare] clean={len(clean_ids)} done={len(done)} pending={len(pending)} shards={n_shards}")
    paths: list[Path] = []
    if not pending:
        return paths
    for i in range(n_shards):
        chunk = pending[i::n_shards]
        path = SHARD_DIR / f"ids_shard_{i:02d}.txt"
        path.write_text("\n".join(chunk) + ("\n" if chunk else ""), encoding="utf-8")
        paths.append(path)
        print(f"  shard{i:02d}: {len(chunk)} -> {path.name}")
    meta = {
        "prepared_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "n_shards": n_shards,
        "pending": len(pending),
        "done_before": len(done),
    }
    (SHARD_DIR / "prepare_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return paths


def launch_workers(id_files: list[Path], sleep: float, model: str, effort: str) -> list[subprocess.Popen]:
    procs: list[subprocess.Popen] = []
    for i, idf in enumerate(id_files):
        n_ids = sum(1 for ln in idf.read_text(encoding="utf-8").splitlines() if ln.strip())
        if n_ids == 0:
            print(f"[skip] shard{i:02d} empty")
            continue
        out = SHARD_DIR / f"annotations_v1_4_shard{i:02d}.csv"
        ckpt = SHARD_DIR / f"checkpoint_shard{i:02d}.json"
        log = SHARD_DIR / f"worker_{i:02d}.log"
        cmd = [
            sys.executable,
            "-u",
            str(SCRIPT),
            "--ids-file",
            str(idf),
            "--output",
            str(out),
            "--checkpoint",
            str(ckpt),
            "--model",
            model,
            "--reasoning-effort",
            effort,
            "--sleep",
            str(sleep),
        ]
        log_f = log.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            cwd=str(DATA),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(proc)
        print(f"[launch] shard{i:02d} pid={proc.pid} n={n_ids} out={out.name}")
        time.sleep(0.5)  # 错峰启动，减轻瞬时打满 API
    return procs


def merge_shards() -> Path:
    """合并主表 + 各分片，按 review_id 保留最后成功行。"""
    rows: dict[str, dict] = {}
    fieldnames: list[str] | None = None
    sources = [MAIN_CSV] + sorted(SHARD_DIR.glob("annotations_v1_4_shard*.csv"))
    sources += sorted(SHARD_DIR.glob("annotations_v1_4_retry*.csv"))
    for src in sources:
        if not src.exists():
            continue
        with src.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                fieldnames = list(reader.fieldnames)
            for row in reader:
                rid = str(row.get("review_id") or "")
                if not rid:
                    continue
                if row.get("error"):
                    # 仅当尚无成功行时保留失败记录
                    if rid not in rows or rows[rid].get("error"):
                        rows[rid] = row
                    continue
                rows[rid] = row
    if not fieldnames:
        raise SystemExit("无字段可合并")
    # 探针与实评一并写入
    out = MAIN_CSV
    tmp = MAIN_CSV.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for rid in sorted(rows.keys(), key=lambda x: (x.startswith("probe_"), x)):
            w.writerow({k: rows[rid].get(k, "") for k in fieldnames})
    tmp.replace(out)
    ok = sum(1 for r in rows.values() if not r.get("error"))
    fail = sum(1 for r in rows.values() if r.get("error"))
    print(f"[merge] -> {out} unique={len(rows)} ok={ok} fail={fail}")
    return out


def wait_workers(procs: list[subprocess.Popen]) -> int:
    if not procs:
        return 0
    print(f"[wait] {len(procs)} workers …")
    codes = []
    while True:
        alive = [p for p in procs if p.poll() is None]
        for i in range(len(procs)):
            ckpt = SHARD_DIR / f"checkpoint_shard{i:02d}.json"
            if ckpt.exists():
                try:
                    data = json.loads(ckpt.read_text(encoding="utf-8"))
                    print(
                        f"  shard{i:02d}: {data.get('progress', '?')}/{data.get('total', '?')} "
                        f"ok={data.get('ok')} fail={data.get('fail')} alive={procs[i].poll() is None}"
                    )
                except Exception:
                    pass
        if not alive:
            break
        time.sleep(60)
    for p in procs:
        codes.append(p.wait())
    bad = sum(1 for c in codes if c not in (0, None))
    print(f"[workers done] exit_codes={codes} bad={bad}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="Parallel shard annotate for v1.4")
    ap.add_argument("--shards", type=int, default=5, help="并行标注线数量，默认 5")
    ap.add_argument("--sleep", type=float, default=0.08)
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument("--reasoning-effort", default="high", choices=["high", "max"])
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--no-wait", action="store_true", help="只拉起 worker 不阻塞等待")
    args = ap.parse_args()

    if args.merge_only:
        merge_shards()
        return 0

    id_files = prepare_shards(args.shards)
    if args.prepare_only:
        return 0
    if not id_files:
        print("无待标，直接合并")
        merge_shards()
        return 0

    procs = launch_workers(id_files, args.sleep, args.model, args.reasoning_effort)
    if args.no_wait:
        print("[no-wait] workers launched; 稍后运行: python run_annotate_shards.py --merge-only")
        return 0
    bad = wait_workers(procs)
    merge_shards()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
