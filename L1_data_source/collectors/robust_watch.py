#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""L1_data_source/collectors/robust_watch.py — 常驻采集循环（自愈版）。

★ 为什么需要它（2026-10-05 事故复盘）：
  各采集器原来的 watch 循环是裸的 —— `collect()` 抛异常就整个进程死掉，
  **一次网络抖动 = 该渠道永久停摆**。实测微博采集器 7 小时没采到任何数据
  （ConnectionResetError → 进程退出），而且没有任何告警，是人工检查才发现。

  这三条纪律：
    ① 异常不外溢：单轮失败打日志、计失败数、继续下一轮
    ② 退避重试：连续失败会拉长间隔（上限 max_backoff），不给对方添压力
    ③ 死亡可见：连续失败超阈值 → 退出码非 0 + 明确日志（让 supervisor 能发现）

用法：
    from robust_watch import run_forever
    run_forever(name="weibo", fn=collect, interval=600,
                max_backoff=1800, fail_threshold=20)
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Optional

TZ_CN = timezone(timedelta(hours=8))


def _log(name: str, level: str, msg: str) -> None:
    ts = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{name}] {level} {msg}", file=sys.stderr, flush=True)


def run_forever(name: str, fn: Callable[[], Dict[str, Any]],
                interval: int, max_backoff: int = 1800,
                fail_threshold: int = 20,
                max_rounds: int = 0) -> int:
    """常驻跑 fn()，异常自愈。

    fn 必须**自己吞掉单页/单源的异常**并返回统计 dict —— 这里的兜底是最后一道。
    """
    rounds = ok_count = fail_count = 0
    cur_interval = interval
    consecutive_fail = 0

    while True:
        rounds += 1
        started = time.time()
        try:
            result = fn()
            ok_count += 1
            consecutive_fail = 0
            cur_interval = interval          # 成功后回到正常节奏
            _log(name, "ok", f"第 {rounds} 轮完成（{time.time()-started:.1f}s）："
                              f"{json.dumps(result, ensure_ascii=False)[:160]}")
        except KeyboardInterrupt:
            _log(name, "stop", "收到中断，正常退出")
            return 0
        except Exception as e:                      # ★ 关键：不外溢
            fail_count += 1
            consecutive_fail += 1
            _log(name, "ERROR", f"第 {rounds} 轮失败（连续第 {consecutive_fail} 次）："
                                f"{type(e).__name__}: {str(e)[:120]}")
            if consecutive_fail <= 2:
                _log(name, "debug", traceback.format_exc()[-500:])
            if consecutive_fail >= fail_threshold:
                _log(name, "FATAL", f"连续 {consecutive_fail} 轮失败，超过阈值 {fail_threshold}，"
                                   f"退出让 supervisor 拉起（当前共成功 {ok_count} 轮）")
                return 2
            # 退避：连续失败时逐步拉长间隔，给对方留恢复余地
            cur_interval = min(max_backoff, int(cur_interval * 1.5))
            _log(name, "warn", f"下一轮间隔拉长到 {cur_interval}s")

        if max_rounds and rounds >= max_rounds:
            break
        # 分片睡眠：Ctrl+C 能立刻响应，不会睡满整个 interval
        slept = 0.0
        while slept < cur_interval:
            time.sleep(min(1.0, cur_interval - slept))
            slept += 1.0

    _log(name, "done", f"共 {rounds} 轮，成功 {ok_count}，失败 {fail_count}")
    return 0


if __name__ == "__main__":
    print(__doc__)
