import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_v2.engine import run_agent
from agent_v2.ingest import ingest, normalize, timestamp
from agent_v2.store import Store
from agent_v2.tools import ResearchTools


def sample(title="新版本玩家合作玩法上线", observed="2026-10-07T10:00:00+08:00", platform="bilibili", external="BVsample"):
    return normalize({"title": title, "description": "玩家可分享地图并组队挑战。", "bvid": external,
                      "url": "https://www.bilibili.com/video/" + external, "observed_at": observed,
                      "play": "100", "pubdate": "1791324000"}, platform, "source.csv")


def final(evidence_id):
    return {"summary": "新合作玩法提供了玩家交流与地图分享的候选方向，传播规模尚待验证。", "assessments": [{
        "title": "新版本玩家合作玩法上线", "verdict": "related", "evidence_ids": [evidence_id],
        "summary": "来源描述了分享地图与组队挑战；尚不能确定是否已形成跨平台热点。",
        "facts": [{"quote": "玩家可分享地图并组队挑战。", "evidence_id": evidence_id}],
        "inferences": ["地图交流可能满足 TapTap 玩家找同伴与攻略的需求。"],
        "unknowns": ["传播规模和素材授权未确认。"],
        "taptap_fit": {"audience": "合作玩法玩家", "motivation": "找同伴与地图攻略", "reason": "玩家讨论与组队需求可由社区内容承接。"},
        "creatives": [{"title": "玩家地图交流帖", "audience": "合作玩法玩家", "placement": "相关游戏社区",
                       "user_action": "分享地图并回复组队需求", "steps": ["核查玩法来源", "发布地图交流帖"],
                       "copy": "这张地图，你会找谁一起挑战？", "materials": [{"description": "原内容玩法说明", "evidence_id": evidence_id, "rights_status": "待确认"}],
                       "timing": "核实版本后评估上线", "risks": ["玩法与素材需核查"], "measurement": "记录独立参与人数与有效回复，不预设增长率"}]}]}


class ScriptedModel:
    model = "test-model"

    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def decide(self, messages, definitions):
        name, arguments = next(self.decisions)
        return {"model": self.model, "tool_calls": [{"id": f"call_{len(messages)}", "name": name, "arguments": arguments}],
                "usage": {"total_tokens": 10}}


class V2CoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.item = sample()
        self.store.upsert_evidence(self.item)
        self.store.conn.commit()
        self.eid = self.item["evidence_id"]

    def tearDown(self):
        self.store.close()

    def test_same_content_new_observation_preserves_history(self):
        later = sample(observed="2026-10-07T11:00:00+08:00")
        later["metrics"]["views"] = 120
        self.assertFalse(self.store.upsert_evidence(later))
        self.assertFalse(self.store.upsert_evidence(later))
        observations = self.store.evidence([self.eid])[0]["observations"]
        self.assertEqual(len(observations), 2)
        self.assertEqual([o["metrics"]["views"] for o in observations], [100, 120])

    def test_search_hit_uses_original_platform_identity(self):
        searched = normalize({"title": self.item["title"], "item_id": "BVsample", "platform": "bili", "observed_at": "2026-10-07T10:00:00+08:00"}, "agent", "search.csv")
        self.assertEqual(searched["evidence_id"], self.eid)
        self.assertEqual(searched["platform"], "bilibili")
        self.assertEqual(searched["kind"], "search")

    def test_old_observation_cannot_overwrite_new_text(self):
        old = sample(title="旧标题", observed="2026-10-06T09:00:00+08:00")
        self.store.upsert_evidence(old)
        self.assertEqual(self.store.evidence([self.eid])[0]["title"], self.item["title"])

    def test_no_observation_time_cannot_be_fabricated(self):
        self.assertIsNone(normalize({"title": "消息"}, "weibo", "source.csv"))
        self.assertIsNone(timestamp("202610"))
        self.assertEqual(timestamp("2026-10-07T10:00:00"), "2026-10-07T02:00:00+00:00")

    def test_literal_search_does_not_expand_wildcard(self):
        self.assertEqual(self.store.candidates(query="%"), [])

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            ResearchTools(self.store).call("execute_shell", {"command": "anything"})

    def test_network_budget_is_enforced_before_calling_connector(self):
        with self.assertRaises(ValueError):
            ResearchTools(self.store, network_budget=0).call("search_sources", {"query": "游戏"})

    def test_finish_requires_reading_the_actual_evidence(self):
        tools = ResearchTools(self.store)
        with self.assertRaises(ValueError):
            tools.validate(final(self.eid))
        tools.call("read_evidence", {"evidence_ids": [self.eid]})
        self.assertEqual(len(tools.validate(final(self.eid))), 1)

    def test_fabricated_quote_cannot_pass(self):
        tools = ResearchTools(self.store)
        tools.call("read_evidence", {"evidence_ids": [self.eid]})
        output = final(self.eid)
        output["assessments"][0]["facts"][0]["quote"] = "官方宣布下载量达到一亿。"
        with self.assertRaises(ValueError):
            tools.validate(output)

    def test_fabricated_material_reference_cannot_pass(self):
        tools = ResearchTools(self.store)
        tools.call("read_evidence", {"evidence_ids": [self.eid]})
        output = final(self.eid)
        output["assessments"][0]["creatives"][0]["materials"][0]["evidence_id"] = "invented"
        with self.assertRaises(ValueError):
            tools.validate(output)

    def test_irrelevant_event_cannot_emit_action_creatives(self):
        tools = ResearchTools(self.store)
        tools.call("read_evidence", {"evidence_ids": [self.eid]})
        output = final(self.eid)
        output["assessments"][0]["verdict"] = "irrelevant"
        with self.assertRaises(ValueError):
            tools.validate(output)

    def test_real_tool_loop_persists_event_creative_and_trace(self):
        run_id = self.store.create_run("研究合作玩法")
        model = ScriptedModel([("read_evidence", {"evidence_ids": [self.eid]}), ("finish_research", final(self.eid))])
        result = run_agent(self.store, run_id, model)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["usage"]["total_tokens"], 20)
        self.assertEqual(self.store.overview()["counts"]["creative"], 1)
        event = self.store.get_event(result["result"]["event_ids"][0])
        self.assertEqual(event["evidence"][0]["body"], self.item["body"])
        self.assertEqual(len(event["versions"]), 1)

    def test_bad_reference_consumes_budget_without_publishing_fake_success(self):
        run_id = self.store.create_run("研究")
        result = run_agent(self.store, run_id, ScriptedModel([("finish_research", final("invented"))]), max_turns=1)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(self.store.overview()["counts"]["event"], 0)
        self.assertTrue(any(step["kind"] == "validation_error" for step in result["steps"]))

    def test_model_failure_is_retained_without_rule_creative(self):
        class Failed:
            def decide(self, *_):
                raise RuntimeError("provider unavailable")
        run_id = self.store.create_run("研究")
        result = run_agent(self.store, run_id, Failed())
        self.assertEqual(result["status"], "failed")
        self.assertIn("provider unavailable", result["error"])
        self.assertEqual(self.store.overview()["counts"]["creative"], 0)

    def test_tool_call_budget_prevents_extra_execution(self):
        run_id = self.store.create_run("研究")
        result = run_agent(self.store, run_id, ScriptedModel([("read_evidence", {"evidence_ids": [self.eid]})]), max_tools=0)
        self.assertEqual(result["status"], "budget_exhausted")

    def test_event_update_keeps_stable_id_and_history(self):
        run1 = self.store.create_run("第一次研究")
        event_id = self.store.save_assessment(run1, final(self.eid)["assessments"][0])
        run2 = self.store.create_run("跟踪更新")
        tools = ResearchTools(self.store)
        tools.call("read_event", {"event_id": event_id})
        result = final(self.eid)
        result["assessments"][0]["event_id"] = event_id
        result["assessments"][0]["title"] = "合作玩法讨论更新"
        tools.validate(result)
        self.assertEqual(self.store.save_assessment(run2, result["assessments"][0]), event_id)
        self.assertEqual(len(self.store.get_event(event_id)["versions"]), 2)

    def test_ingest_repeated_file_is_idempotent_without_changing_v1(self):
        root = Path("unused-project")
        path = root / "data/raw/weibo/hot_search.csv"
        csv_text = "observed_at,word,url\n2026-10-07T10:00:00+08:00,新话题,https://s.weibo.com/weibo?q=test\n"
        with patch("agent_v2.ingest.source_files", return_value=[("weibo", path)]), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "stat", return_value=SimpleNamespace(st_size=100,st_mtime_ns=123)), \
             patch.object(Path, "open", side_effect=lambda **_: io.StringIO(csv_text)):
            first = ingest(self.store, root)
            second = ingest(self.store, root)
        self.assertEqual(first["inserted_evidence"], 1)
        self.assertEqual(second["inserted_evidence"], 0)
        self.assertEqual(second["unchanged_files"], 1)


if __name__ == "__main__":
    unittest.main()
