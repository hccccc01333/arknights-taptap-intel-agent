# -*- coding: utf-8 -*-
"""回填测试：games 档案 / L4 知识 / 伪造 L3、L4 库的定向回填 + 幂等性。

真实数据纪律：
  - games/<key>.json 入 Entity Knowledge 时**必须剥离平台账号 id**（§55 PII 闸门）
  - L3 全部事件未闭合 → 长期 Trend 0 条是**正确结果**（§19），不放宽判据
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_L5 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_L5)
for _p in (_ROOT, _L5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory.store import MemoryStore  # noqa: E402
from memory import ingest  # noqa: E402


class IngestTestBase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(self.path)
        self.store = MemoryStore(self.path)

    def tearDown(self):
        self.store.close()
        if os.path.exists(self.path):
            os.remove(self.path)


class TestGamesIngest(IngestTestBase):
    def test_real_games_registry(self):
        out = ingest.ingest_games(self.store)
        self.assertGreaterEqual(out["ingested"], 2)      # arknights + wuthering-waves
        ent = self.store.candidates("entity")
        subjects = {e["subject"] for e in ent}
        self.assertIn("arknights", subjects)

    def test_platform_account_ids_stripped(self):
        """§55 回归：games 档案里的平台账号 id 不得以任何形态入记忆。"""
        ingest.ingest_games(self.store)
        for e in self.store.candidates("entity"):
            blob = json.dumps(e["payload"], ensure_ascii=False)
            # 明日方舟档案真实存在的账号 uid：weibo 6441486180、官方账号 16966303。
            # group_id=53933 是社群 id（公开、非个人数据，且只出现在自由文本 notes 里），允许保留。
            for raw_id in ("6441486180", "16966303"):
                self.assertNotIn(raw_id, blob)
            # 但档案级事实必须在：别名（检索靠它）
            self.assertTrue(e["payload"].get("aliases"))


class TestTaptapAssetsIngest(IngestTestBase):
    def test_assets_seeded_as_candidate_p2(self):
        out = ingest.ingest_taptap_assets(self.store)
        self.assertGreaterEqual(out["ingested"], 14)     # 14 资产 + 创意类型 + 动机 + 目标集
        row = self.store.db.query_one(
            "SELECT * FROM knowledge_item WHERE subject='game_detail'")
        self.assertEqual((row["tier"], row["authority"], row["human_verified"]),
                         ("candidate", "P2", 0))     # 公开形态描述 ≠ 内部验证（如实定级）
        # 可被检索到（relevance_agent 有权读 business）
        hits = self.store.candidates("business", limit=100)
        self.assertTrue(any(h["subject"] == "game_detail" for h in hits))


def _make_l3_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE trend_event(
        event_id TEXT, canonical_title TEXT, title_source TEXT, event_type TEXT,
        primary_topic TEXT, status TEXT, started_at TEXT, first_detected_at TEXT,
        last_updated_at TEXT, peak_at TEXT, ended_at TEXT, lifecycle TEXT,
        hot_score REAL, momentum_score REAL, confidence_score REAL, opportunity_score REAL,
        novelty_score REAL, velocity_score REAL, acceleration_score REAL, burst_score REAL,
        diffusion_score REAL, engagement_score REAL, source_diversity_score REAL,
        credibility_score REAL, content_count INTEGER, platform_count INTEGER,
        primary_platform TEXT, entity_ids TEXT, platforms TEXT,
        representative_content_id TEXT, split_flag INTEGER, parent_event_id TEXT,
        metadata TEXT, engine_version TEXT)""")
    base = dict(title_source="rule", primary_topic="t", started_at="2026-09-01T00:00:00+08:00",
                first_detected_at="2026-09-01T00:00:00+08:00",
                last_updated_at="2026-09-01T00:00:00+08:00", peak_at=None,
                hot_score=0.5, momentum_score=0.5, confidence_score=0.6,
                opportunity_score=0.1, novelty_score=0.5, velocity_score=0.1,
                acceleration_score=0.0, burst_score=0.0, diffusion_score=0.2,
                engagement_score=0.3, source_diversity_score=0.5, credibility_score=0.5,
                content_count=10, platform_count=1, primary_platform="taptap",
                entity_ids='["arknights"]', platforms='["taptap"]',
                representative_content_id="x", split_flag=0, parent_event_id=None,
                metadata="{}", engine_version="v1")
    rows = [
        dict(base, event_id="evt_closed", canonical_title="已结束联动", event_type="collab",
             status="active", ended_at="2026-09-03T00:00:00+08:00", lifecycle="SUBSIDED"),
        dict(base, event_id="evt_live", canonical_title="进行中联动", event_type="collab",
             status="active", ended_at=None, lifecycle="DECLINING"),
        dict(base, event_id="evt_merged", canonical_title="被合并的", event_type="collab",
             status="merged", ended_at=None, lifecycle="DECLINING"),
    ]
    cols = list(rows[0].keys())
    conn.executemany(
        f"INSERT INTO trend_event({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [tuple(r[c] for c in cols) for r in rows])
    conn.commit()
    conn.close()


class TestL3Ingest(IngestTestBase):
    def test_routing_closed_active_merged(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(path)
        _make_l3_db(path)
        try:
            out = ingest.ingest_l3_trends(self.store, l3_db=path)
            self.assertEqual(out["ingested"], 1)          # 只有闭合事件进长期
            self.assertEqual(out["short_term"], 1)        # 进行中的进短期
            self.assertEqual(out["merged_skipped"], 1)    # 合并的不重复入记忆
            self.assertEqual([t["event_id"] for t in self.store.candidates("trend")],
                             ["evt_closed"])
            stm = self.store.short_term("active_trend")
            self.assertEqual(stm[0]["payload"]["event_id"], "evt_live")
        finally:
            os.remove(path)

    def test_missing_db_honest(self):
        out = ingest.ingest_l3_trends(self.store, l3_db="Z:/nope.sqlite3")
        self.assertEqual(out["ingested"], 0)
        self.assertIn("不存在", out["skipped"][0]["reason"])


def _make_l4_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE intelligence_creative(
        idea_id TEXT, analysis_id TEXT, event_id TEXT, creative_type TEXT,
        idea_name TEXT, score REAL, passed INTEGER, source_refs TEXT, payload TEXT)""")
    conn.execute("""CREATE TABLE human_feedback(
        feedback_id TEXT, idea_id TEXT, event_id TEXT, decision TEXT,
        reason TEXT, created_at TEXT)""")
    conn.execute("INSERT INTO intelligence_creative VALUES(?,?,?,?,?,?,?,?,?)",
                 ("idea_1", "anl_1", "evt_1", "ugc", "投稿挑战", 0.8, 1, "{}",
                  json.dumps({"concept": "UGC 挑战", "growth_mechanism": "排行刺激投稿",
                              "primary_metric": "ugc_count"}, ensure_ascii=False)))
    conn.execute("INSERT INTO intelligence_creative VALUES(?,?,?,?,?,?,?,?,?)",
                 ("idea_2", "anl_1", "evt_1", "content", "专题", 0.5, 0, "{}", "{}"))
    conn.execute("INSERT INTO human_feedback VALUES(?,?,?,?,?,?)",
                 ("fb_1", "idea_1", "evt_1", "adopt", "机制清晰", "2026-10-01"))
    conn.execute("INSERT INTO human_feedback VALUES(?,?,?,?,?,?)",
                 ("fb_2", "idea_2", "evt_1", "reject", "太贵了", "2026-10-01"))
    conn.commit()
    conn.close()


class TestL4Ingest(IngestTestBase):
    def test_creatives_and_feedback_roundtrip(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(path)
        _make_l4_db(path)
        try:
            out = ingest.ingest_l4(self.store, l4_db=path)
            self.assertEqual(out["creatives"], 2)         # 被拒的也入（§10）
            self.assertEqual(out["decisions"], 2)
            rows = {r["title"]: r for r in self.store.candidates("creative")}
            adopted = rows["投稿挑战"]
            self.assertEqual((adopted["human_decision"], adopted["tier"]),
                             ("approve", "verified"))     # L4 adopt → verified（§19）
            rejected = rows["专题"]
            self.assertEqual(rejected["human_decision"], "reject")
            self.assertEqual(rejected["tier"], "agent_generated")
            decisions = self.store.candidates("decision")
            codes = {d["reason_code"] for d in decisions}
            self.assertIn("TOO_EXPENSIVE", codes)         # §17 taxonomy 归一
            self.assertTrue(any(d["source_ref"].startswith("l4.human_feedback:")
                                for d in decisions))
        finally:
            os.remove(path)

    def test_idempotent_rerun(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.remove(path)
        _make_l4_db(path)
        try:
            ingest.ingest_l4(self.store, l4_db=path)
            n1 = self.store.db.table_count("creative_memory")
            d1 = self.store.db.table_count("decision_memory")
            ingest.ingest_l4(self.store, l4_db=path)
            self.assertEqual(self.store.db.table_count("creative_memory"), n1)
            self.assertEqual(self.store.db.table_count("decision_memory"), d1)
        finally:
            os.remove(path)

    def test_missing_db_honest(self):
        out = ingest.ingest_l4(self.store, l4_db="Z:/nope.sqlite3")
        self.assertEqual(out["creatives"], 0)
        self.assertIn("不存在", out["skipped"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
