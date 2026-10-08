"""Freshness regressions using only in-memory SQLite and mocked JSON reads."""

import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from runtime.pipeline_health import build_health, parse_time, TZ

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=TZ)
RECENT = "2026-10-07T11:50:00+08:00"
OLD = "2026-10-04T16:11:12+08:00"
CONNECT = sqlite3.connect


class PipelineHealthTests(unittest.TestCase):
    def health(self, *, times=None, missing=(), invalid_json=None, counts=None,
               broken_database=None, report_time=RECENT, report_items=None):
        times, counts = times or {}, counts or {}
        databases = {
            "l1_source_registry.sqlite3": ("metric_snapshot", "observed_at"),
            "l2_processed.sqlite3": ("content", "observed_at"),
            "l3_trend.sqlite3": ("trend_event", "last_updated_at"),
            "l4_intelligence.sqlite3": ("intelligence_analysis", "created_at"),
        }

        def connect(database_uri, **kwargs):
            self.assertIn("mode=ro", database_uri)
            self.assertTrue(kwargs.get("uri"))
            filename = next(name for name in databases if name in database_uri)
            if filename == broken_database:
                raise sqlite3.OperationalError("database is locked")
            connection = CONNECT(":memory:")
            table, column = databases[filename]
            suffix = ", status TEXT" if table == "trend_event" else ""
            connection.execute(f"CREATE TABLE {table} ({column} TEXT{suffix})")
            for _ in range(counts.get(filename, 1)):
                if table == "trend_event":
                    connection.execute(f"INSERT INTO {table} VALUES (?, 'active')",
                                       (times.get(filename, RECENT),))
                else:
                    connection.execute(f"INSERT INTO {table} VALUES (?)",
                                       (times.get(filename, RECENT),))
            return connection

        def read(path, **kwargs):
            if invalid_json == path.name:
                return "{broken"
            items = [1] if report_items is None else report_items
            return json.dumps({"generated_at": report_time,
                               "reports": items, "forming_all": items, "creatives": items})

        with patch.object(Path, "is_file", lambda path: path.name not in missing), \
                patch.object(Path, "read_text", read), \
                patch("runtime.pipeline_health.sqlite3.connect", side_effect=connect):
            return build_health(Path("unused-project"), now=NOW)

    def test_recent_inputs_are_ok(self):
        health = self.health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["warnings"], [])
        self.assertTrue(all(s["age_minutes"] == 10 for s in health["stages"]))

    def test_report_refresh_cannot_hide_old_trend_inputs(self):
        health = self.health(times={"l3_trend.sqlite3": OLD})
        stages = {s["key"]: s for s in health["stages"]}
        self.assertEqual(stages["communities"]["status"], "ok")
        self.assertEqual(stages["trend"]["status"], "stale")
        self.assertEqual(health["status"], "degraded")
        self.assertGreater(health["observation_lag_minutes"], 60)
        self.assertTrue(any("落后于清洗库" in w for w in health["warnings"]))

    def test_missing_inputs_do_not_create_databases(self):
        names = ("l1_source_registry.sqlite3", "l2_processed.sqlite3",
                 "l3_trend.sqlite3", "l4_intelligence.sqlite3")
        health = self.health(missing=names)
        self.assertEqual([s["status"] for s in health["stages"][:4]], ["missing"] * 4)

    def test_corrupt_report_is_reported_and_other_stages_survive(self):
        health = self.health(invalid_json="forming_report.json")
        self.assertEqual(health["stages"][4]["status"], "unavailable")
        self.assertEqual(health["stages"][0]["status"], "ok")

    def test_locked_database_does_not_break_the_response(self):
        health = self.health(broken_database="l3_trend.sqlite3")
        self.assertEqual(health["stages"][2]["status"], "unavailable")
        self.assertEqual(health["stages"][3]["status"], "ok")

    def test_recent_report_with_no_hotspots_is_valid(self):
        health = self.health(report_items=[])
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["stages"][4]["records"], 0)

    def test_empty_database_has_no_fabricated_watermark(self):
        health = self.health(counts={"l2_processed.sqlite3": 0})
        stage = health["stages"][1]
        self.assertEqual(stage["status"], "empty")
        self.assertIsNone(stage["latest_at"])
        self.assertIsNone(stage["age_minutes"])

    def test_invalid_timestamp_is_unknown(self):
        health = self.health(times={"l2_processed.sqlite3": "not-a-time"})
        self.assertEqual(health["stages"][1]["status"], "unknown")

    def test_legacy_creative_without_time_is_not_presented_as_recent(self):
        health = self.health(report_time=None)
        self.assertEqual(health["stages"][6]["key"], "creatives")
        self.assertEqual(health["stages"][6]["status"], "unknown")

    def test_future_timestamp_is_invalid(self):
        health = self.health(times={"l3_trend.sqlite3": "2026-10-08T12:00:00+08:00"})
        self.assertEqual(health["stages"][2]["status"], "invalid")

    def test_naive_historical_time_uses_shanghai(self):
        self.assertEqual(parse_time("2026-10-07T12:00:00"), NOW)
        self.assertEqual(parse_time("2026-10-07T04:00:00Z"), NOW)
        self.assertIsNone(parse_time(None))
        self.assertIsNone(parse_time("invalid"))

    def test_invalid_thresholds_are_rejected(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_health(now=NOW, max_age_minutes=value)


class HealthApiTests(unittest.TestCase):
    def test_health_requires_login_and_universe_preserves_data(self):
        from fastapi.testclient import TestClient
        from webapp.main import app, current_user
        client = TestClient(app)
        self.assertEqual(client.get("/api/pipeline-health").status_code, 401)
        snapshot = {"checked_at": NOW.isoformat(), "status": "degraded", "warnings": ["旧数据"]}
        with patch.dict(app.dependency_overrides, {current_user: lambda: {"role": "viewer"}}), \
                patch("webapp.services.pipeline_health", return_value=snapshot), \
                patch("webapp.universe.build_universe", return_value={"planets": [{"id": "real-event"}]}):
            self.assertEqual(client.get("/api/pipeline-health").json(), snapshot)
            data = client.get("/api/universe").json()
            self.assertEqual(data["planets"], [{"id": "real-event"}])
            self.assertEqual(data["pipelineHealth"], snapshot)


if __name__ == "__main__":
    unittest.main()
