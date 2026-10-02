#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""trend_engine.evaluation 测试。

重点不是公式对不对，而是这条纪律：**没有 ground truth 就不许产出数字**。
用系统自己的 hot_score 去证明"系统发现得准"是循环论证 —— 测试要钉死这一点。
"""

from __future__ import annotations

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L3 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L3)
for p in (_L3, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from trend_engine.evaluation import (
    temporal_resolution,  # noqa: E402
    cluster_coherence, clustering_metrics, detection_eval, detection_latency, evaluate,
)


class TestDetectionLatency(unittest.TestCase):
    def test_computes_hours(self):
        evs = [{"event_id": "e1", "started_at": "2026-01-01T10:00:00",
                "first_detected_at": "2026-01-01T12:00:00", "content_count": 3}]
        r = detection_latency(evs)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["first_seen_hours"]["mean"], 2.0)

    def test_no_timestamps_is_not_zero(self):
        """缺时间戳 → 状态标记，绝不能返回 0 冒充延迟。"""
        r = detection_latency([{"event_id": "e1"}])
        self.assertEqual(r["status"], "no_timestamped_events")
        self.assertNotIn("latency_hours", r)

    def test_batch_replay_is_flagged_degenerate(self):
        """批量重放下 first_detected_at == started_at → 必须标退化，不能报"延迟 0 = 极快"。"""
        evs = [{"event_id": f"e{i}", "started_at": "2026-01-01T10:00:00",
                "first_detected_at": "2026-01-01T10:00:00"} for i in range(3)]
        r = detection_latency(evs)
        self.assertTrue(r["first_seen_degenerate"])
        self.assertIn("first_seen_warning", r)
        self.assertEqual(r["first_seen_hours"]["mean"], 0.0)

    def test_recognition_latency_uses_kth_content(self):
        evs = [{"event_id": "e1", "started_at": "2026-01-01T10:00:00",
                "first_detected_at": "2026-01-01T10:00:00"}]
        members = {"e1": [{"observed_at": "2026-01-01T10:00:00"},
                          {"observed_at": "2026-01-01T11:00:00"},
                          {"observed_at": "2026-01-01T13:00:00"}]}
        r = detection_latency(evs, members, recognition_k=3)
        self.assertFalse(r["first_seen_degenerate"] is None)
        self.assertAlmostEqual(r["recognition_hours"]["mean"], 3.0, places=2)
        self.assertEqual(r["recognition_k"], 3)

    def test_recognition_k_capped_by_content_count(self):
        """内容不足 k 条 → 用最后一条，不能报 None 也不能外推。"""
        evs = [{"event_id": "e1", "started_at": "2026-01-01T10:00:00",
                "first_detected_at": "2026-01-01T10:00:00"}]
        members = {"e1": [{"observed_at": "2026-01-01T10:30:00"}]}
        r = detection_latency(evs, members, recognition_k=3)
        self.assertAlmostEqual(r["recognition_hours"]["mean"], 0.5, places=2)

    def test_negative_exposed(self):
        """负延迟（发现早于最早内容）必须暴露，不能被 abs 掉。"""
        evs = [{"event_id": "e1", "started_at": "2026-01-01T12:00:00",
                "first_detected_at": "2026-01-01T10:00:00"}]
        r = detection_latency(evs)
        self.assertEqual(r["negative_count"], 1)
        self.assertLess(r["first_seen_hours"]["min"], 0)


class TestClusteringMetrics(unittest.TestCase):
    def test_perfect_agreement(self):
        labels = {f"c{i}": ("A" if i < 3 else "B") for i in range(6)}
        r = clustering_metrics(dict(labels), dict(labels))
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["ari"], 1.0, places=3)
        self.assertAlmostEqual(r["pairwise_precision"], 1.0, places=3)
        self.assertAlmostEqual(r["pairwise_recall"], 1.0, places=3)
        self.assertAlmostEqual(r["purity"], 1.0, places=3)

    def test_singletons_penalized(self):
        """全切成单例：recall 应该很低（真实同簇的都没聚起来）。"""
        truth = {f"c{i}": "A" for i in range(6)}
        pred = {f"c{i}": f"p{i}" for i in range(6)}
        r = clustering_metrics(pred, truth)
        self.assertEqual(r["pairwise_recall"], 0.0)

    def test_insufficient_labels(self):
        r = clustering_metrics({"a": "x"}, {"a": "x"})
        self.assertEqual(r["status"], "insufficient_labels")

    def test_only_overlap_evaluated(self):
        """标注只覆盖一部分 → 只在交集上算，并报覆盖率。"""
        pred = {f"c{i}": "A" for i in range(4)}
        truth = {"c0": "A", "c1": "A"}
        r = clustering_metrics(pred, truth)
        self.assertEqual(r["n"], 2)
        self.assertAlmostEqual(r["covered_ratio"], 0.5, places=3)


class TestClusterCoherence(unittest.TestCase):
    def test_singleton_ratio(self):
        members = {"e1": [{"platform": "weibo"}], "e2": [{"platform": "weibo"}, {"platform": "bili"}]}
        r = cluster_coherence(members, {"e1": [0.9], "e2": [0.8, 0.7]})
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["singleton_ratio"], 0.5, places=3)
        self.assertAlmostEqual(r["cross_platform_ratio"], 0.5, places=3)
        # 簇内均值再取平均：(0.9 + 0.75)/2 = 0.825
        self.assertAlmostEqual(r["mean_intra_similarity"], 0.825, places=3)
        # 按事件数单例率 50%，但按内容数 2/3 的内容其实在多成员事件里 —— 两个口径都要报
        self.assertAlmostEqual(r["content_coverage_in_multi"], 2 / 3, places=3)

    def test_no_events(self):
        self.assertEqual(cluster_coherence({})["status"], "no_events")


class TestNoGroundTruth(unittest.TestCase):
    """核心纪律：没有外部真值 → 不给 precision/recall/lead time。"""

    def test_detection_requires_truth(self):
        r = detection_eval([{"event_key": "k1", "detected_at": "2026-01-01T10:00:00"}])
        self.assertEqual(r["status"], "no_ground_truth")
        for k in ("precision", "recall", "lead_time_minutes"):
            self.assertNotIn(k, r)

    def test_detection_with_truth(self):
        alerts = [{"event_key": "k1", "detected_at": "2026-01-01T10:00:00"},
                  {"event_key": "k2", "detected_at": "2026-01-01T10:00:00"}]
        truth = {"k1": "2026-01-01T11:00:00", "k3": "2026-01-01T12:00:00"}
        r = detection_eval(alerts, truth)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["precision"], 0.5, places=3)   # k1 命中 / 2 次告警
        self.assertAlmostEqual(r["recall"], 0.5, places=3)       # k1 命中 / 2 个真值
        self.assertAlmostEqual(r["lead_time_minutes"]["mean"], 60.0, places=1)

    def test_negative_lead_time_warns(self):
        alerts = [{"event_key": "k1", "detected_at": "2026-01-01T12:00:00"}]
        r = detection_eval(alerts, {"k1": "2026-01-01T10:00:00"})
        self.assertIn("warning", r)

    def test_evaluate_marks_missing_truth(self):
        evs = [{"event_id": "e1", "started_at": "2026-01-01T10:00:00",
                "first_detected_at": "2026-01-01T11:00:00"}]
        out = evaluate(evs)
        self.assertEqual(out["clustering"]["status"], "no_ground_truth")
        self.assertEqual(out["detection"]["status"], "no_ground_truth")
        self.assertEqual(out["detection_latency"]["status"], "ok")   # 不依赖真值的照算


if __name__ == "__main__":
    unittest.main()


class TestTemporalResolution(unittest.TestCase):
    """时间分辨率体检：没有时间维度时必须把依赖时间的指标整体标为不可用。"""

    def test_snapshot_data_flagged(self):
        members = {"e1": [{"observed_at": "2026-01-01T10:00:00"} for _ in range(5)]}
        r = temporal_resolution(members)
        self.assertEqual(r["status"], "no_temporal_resolution")
        self.assertIn("velocity", r["inert_metrics"])
        self.assertIn("lead_time", r["inert_metrics"])
        self.assertEqual(r["distinct_timestamps"], 1)

    def test_two_distinct_stamps_still_inert(self):
        members = {"e1": [{"observed_at": "2026-01-01T10:00:00"},
                          {"observed_at": "2026-01-01T11:00:00"},
                          {"observed_at": "2026-01-01T11:00:00"}]}
        self.assertEqual(temporal_resolution(members)["status"], "no_temporal_resolution")

    def test_real_timeseries_ok(self):
        members = {"e1": [{"observed_at": f"2026-01-0{i}T10:00:00"} for i in range(1, 6)]}
        r = temporal_resolution(members)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["inert_metrics"], [])
        self.assertGreater(r["span_hours"], 24)

    def test_no_timestamps(self):
        self.assertEqual(temporal_resolution({"e1": [{}]})["status"], "no_timestamps")
