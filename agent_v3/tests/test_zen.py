"""Contract/failure tests; fake transport is never used for production results."""
import copy
import unittest
from unittest.mock import patch

from L4_intelligence.intelligence.llm import LLMUnavailable
from agent_v3.opencode_zen import OpenCodeModel, StructuredDeliveryError, DEFAULT_MODEL, free_model, configuration, provider_error
from agent_v3.model import failure, gate, GatedModel, set_zen_quota, zen_quota, QUOTA_REASON
from agent_v3.store import Store
from agent_v3 import work
from agent_v3.discovery import scan, queue
from agent_v3.pipeline import process
from agent_v3.tests.test_v3 import source, output, approve_test_topic
from agent_v3.tests.test_continuity import analysis
from agent_v3.intelligence import native_contract, FINISH
from jsonschema import Draft202012Validator, ValidationError


def catalog():
    return {"providers": [{"id": "opencode", "models": {"big-pickle": {
        "cost": {"input": 0, "output": 0, "cache": {"read": 0, "write": 0}},
        "capabilities": {"toolcall": True}}}}]}


def response():
    return {"info": {"id": "msg_test", "role": "assistant", "providerID": "opencode",
            "modelID": "big-pickle", "cost": 0,
            "structured": {"ready": True},
            "tokens": {"input": 100, "output": 40, "reasoning": 20}}, "parts": []}


class Transport:
    def __init__(self, reply=None, error=None):
        self.catalog = catalog(); self.calls = []; self.reply = response() if reply is None else reply; self.error = error

    def ensure(self, **kwargs):return self

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/config/providers":return self.catalog
        if path == "/session":return {"id": "ses_test"}
        if path.endswith("/abort"):return True
        if path == "/permission":return []
        if method == "GET" and path == "/session/ses_test":return {"cost":0,"tokens":{"input":100,"output":40,"reasoning":20}}
        if self.error:raise self.error
        return self.reply


SCHEMA = {"type":"object","properties":{"ready":{"type":"boolean"}},"required":["ready"],"additionalProperties":False}


def run(model):
    return model.run_task("test-readiness", {"expected":True}, SCHEMA, "Check the public input", timeout_seconds=5)


class ZenTests(unittest.TestCase):
    def test_real_server_assistant_field_is_structured(self):
        runtime = Transport(); model = OpenCodeModel(runtime=runtime)
        result = run(model)
        self.assertTrue(result["result"]["ready"])
        self.assertEqual(result["usage"]["total_tokens"], 160)
        self.assertEqual(result["session_id"], "ses_test")
        self.assertEqual(runtime.calls[-2][1], "/session/ses_test/message")
        self.assertEqual(runtime.calls[-2][2]["json"]["format"]["type"], "json_schema")

    def test_paid_unknown_and_non_zen_models_are_rejected(self):
        for selected in ("opencode/gpt-6-sol", "big-pickle", "openrouter/free"):
            with self.subTest(model=selected), self.assertRaises(LLMUnavailable):free_model(catalog(), selected)

    def test_missing_price_paid_cache_and_paid_long_context_stop_before_prompt(self):
        changes = [{"input": None}, {"input": False}, {"output": 1}, {"cache": {"read": 0.1, "write": 0}},
                   {"context_over_200k": {"input": 0, "output": 2, "cache": {"read": 0}}}]
        for change in changes:
            runtime = Transport(); runtime.catalog["providers"][0]["models"]["big-pickle"]["cost"].update(change)
            with self.subTest(change=change), self.assertRaises(LLMUnavailable):OpenCodeModel(runtime=runtime)
            self.assertEqual(runtime.calls, [])

    def test_capability_is_required(self):
        data = catalog(); data["providers"][0]["models"]["big-pickle"]["capabilities"] = {}
        with self.assertRaises(LLMUnavailable):free_model(data, DEFAULT_MODEL)

    def test_metadata_is_checked_again_before_each_call(self):
        runtime = Transport(); model = OpenCodeModel(runtime=runtime)
        runtime.catalog["providers"][0]["models"]["big-pickle"]["cost"]["output"] = 1
        with self.assertRaises(LLMUnavailable):run(model)
        self.assertEqual([call[1] for call in runtime.calls], ["/config/providers"])

    def test_unknown_tool_wrong_provider_wrong_model_and_missing_cost_are_rejected(self):
        changes = [{"structured": {"tool": "bash", "arguments": {}}}, {"providerID": "other"},
                   {"modelID": "gpt-6-sol"}, {"cost": None}, {"cost": 1}, {"structured": None}]
        for change in changes:
            reply = response(); reply["info"].update(change)
            runtime = Transport(reply=reply); model = OpenCodeModel(runtime=runtime)
            expected=StructuredDeliveryError if change.get("structured")=={"tool":"bash","arguments":{}} else LLMUnavailable
            with self.subTest(change=change), self.assertRaises(expected):run(model)
            self.assertTrue(runtime.calls[-1][1].endswith("/abort"))

    def test_timeout_aborts_and_does_not_retry_a_different_model(self):
        runtime = Transport(error=LLMUnavailable("连接超时")); model = OpenCodeModel(runtime=runtime)
        with self.assertRaises(LLMUnavailable):run(model)
        self.assertEqual(sum(path.endswith("/message") for _, path, _ in runtime.calls), 1)
        self.assertTrue(runtime.calls[-1][1].endswith("/abort"))
        self.assertEqual(model.model, DEFAULT_MODEL)

    def test_provider_failure_does_not_leak_error_payload(self):
        secret = "private-value-must-never-appear"
        runtime = Transport(reply={"info": {"error": {"statusCode": 403, "message": secret}}})
        with self.assertRaises(LLMUnavailable) as caught:run(OpenCodeModel(runtime=runtime))
        self.assertIn("403", str(caught.exception)); self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(provider_error({"statusCode": 429}), "HTTP 429: OpenCode Zen 免费模型限流，任务保留等待重试")

    def test_no_credentials_or_paid_fallback_in_configuration(self):
        config = configuration()
        self.assertEqual(config["enabled_providers"], ["opencode"])
        self.assertEqual(config["small_model"], DEFAULT_MODEL)
        self.assertEqual(config["permission"]["edit"], "deny")
        self.assertEqual(config["permission"]["*"], "ask")
        self.assertNotIn("apiKey", config["provider"]["opencode"]["options"])

    def test_gate_does_not_inherit_openrouter_quota_and_blocks_all_zen_free_models(self):
        store = Store(":memory:")
        try:
            failure(store, "openrouter/free", "HTTP 429 free-models-per-day")
            self.assertEqual(gate(store, DEFAULT_MODEL)["status"], "ready")
            failure(store, DEFAULT_MODEL, "HTTP 403")
            self.assertEqual(gate(store, "opencode/ling-3.1-flash-free")["status"], "deferred")
        finally:store.close()

    def test_zen_selection_requires_cli_and_rejects_paid_models(self):
        store = Store(":memory:")
        try:
            with patch("agent_v3.opencode_zen.executable", return_value=None), self.assertRaises(ValueError):store.set_model(DEFAULT_MODEL)
            with self.assertRaises(ValueError):store.set_model("opencode/gpt-6-sol")
            with patch("agent_v3.opencode_zen.executable", return_value="test-cli"):
                self.assertEqual(store.set_model(DEFAULT_MODEL)["transport"], "opencode-agent")
            self.assertEqual(store.model_setting(), DEFAULT_MODEL)
        finally:store.close()

    def test_gated_model_routes_zen_without_calling_legacy_provider(self):
        store = Store(":memory:")
        try:
            with patch("agent_v3.opencode_zen.executable", return_value="test-cli"):store.set_model(DEFAULT_MODEL)
            with patch("agent_v3.model.OpenCodeModel") as zen, patch("agent_v3.model.LiveModel") as legacy:
                zen.return_value.model = DEFAULT_MODEL
                self.assertEqual(GatedModel(store).model, DEFAULT_MODEL)
                zen.assert_called_once_with(DEFAULT_MODEL,reasoning_effort="low"); legacy.assert_not_called()
        finally:store.close()

    def test_one_zen_model_timeout_does_not_block_another_zen_model(self):
        store=Store(":memory:")
        try:
            failure(store,DEFAULT_MODEL,"上游请求超时")
            self.assertEqual(gate(store,DEFAULT_MODEL)["status"],"deferred")
            self.assertEqual(gate(store,"opencode/ling-3.1-flash-free")["status"],"ready")
        finally:store.close()

    def test_reported_quota_hold_blocks_every_zen_model_without_starting_runtime(self):
        store=Store(":memory:")
        try:
            with patch("agent_v3.opencode_zen.executable",return_value="test-cli"):store.set_model(DEFAULT_MODEL)
            set_zen_quota(store,True)
            self.assertTrue(zen_quota(store)["hold"])
            for selected in (DEFAULT_MODEL,"opencode/ling-3.1-flash-free"):
                result=gate(store,selected)
                self.assertTrue(result["manual_resume"])
                self.assertIsNone(result["retry_at"])
            with patch("agent_v3.model.OpenCodeModel") as native, self.assertRaises(LLMUnavailable):GatedModel(store)
            native.assert_not_called()
            self.assertEqual(gate(store,"openrouter/free")["status"],"ready")
        finally:store.close()

    def test_quota_resume_wakes_retained_jobs_but_preserves_observed_provider_backoff(self):
        store=Store(":memory:")
        try:
            with patch("agent_v3.opencode_zen.executable",return_value="test-cli"):store.set_model(DEFAULT_MODEL)
            store.upsert_evidence(source());store.conn.commit();scan(store);work.enqueue(store)
            set_zen_quota(store,True);work.defer_due(store,gate(store))
            self.assertEqual(store.conn.execute("SELECT error FROM work_item").fetchone()[0],QUOTA_REASON)
            failure(store,DEFAULT_MODEL,"HTTP 429")
            set_zen_quota(store,False)
            self.assertEqual(gate(store)["status"],"deferred")
            self.assertEqual(store.conn.execute("SELECT status FROM work_item").fetchone()[0],"deferred")
            # Expire only the fixture's observed cooldown; resume does not erase it.
            store.conn.execute("UPDATE model_gate SET retry_at='2000-01-01T00:00:00+00:00'");store.conn.commit()
            set_zen_quota(store,False)
            self.assertEqual(store.conn.execute("SELECT status FROM work_item").fetchone()[0],"pending")
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM model_gate").fetchone()[0],1)
            with self.assertRaises(ValueError):set_zen_quota(store,None)
        finally:store.close()

    def test_reasoning_strength_is_stored_and_passed_to_the_official_runtime(self):
        store=Store(":memory:")
        try:
            self.assertEqual(store.reasoning_setting(),"low")
            store.set_reasoning("medium")
            with self.assertRaises(ValueError):store.set_reasoning("unbounded")
            with patch("agent_v3.opencode_zen.executable",return_value="test-cli"):store.set_model(DEFAULT_MODEL)
            with patch("agent_v3.model.OpenCodeModel") as zen:
                GatedModel(store)
                zen.assert_called_once_with(DEFAULT_MODEL,reasoning_effort="medium")
            self.assertEqual(configuration("high")["provider"]["opencode"]["models"]["ling-3.1-flash-free"]["options"]["reasoningEffort"],"high")
            self.assertNotIn("options",configuration("default")["provider"]["opencode"]["models"]["ling-3.1-flash-free"])
        finally:store.close()

    def test_empty_source_assets_are_allowed_and_evidence_ids_cannot_be_used_as_assets(self):
        topic={"topic_id":"topic_test","fingerprint":"revision_test","evidence":[source()]}
        schema,quotes=native_contract(topic,[])
        field=schema["properties"]["source_asset_ids"]
        Draft202012Validator(field).validate([])
        with self.assertRaises(ValidationError):Draft202012Validator(field).validate([topic["evidence"][0]["evidence_id"]])
        schema,_=native_contract(topic,[{"asset_id":"source_asset_test"}])
        field=schema["properties"]["source_asset_ids"]
        Draft202012Validator(field).validate(["source_asset_test"])
        with self.assertRaises(ValidationError):Draft202012Validator(field).validate([topic["evidence"][0]["evidence_id"]])
        Draft202012Validator(FINISH["function"]["parameters"]["properties"]["source_asset_ids"]).validate([])

    def test_quote_contract_preserves_source_punctuation_and_rejects_metadata_as_quotes(self):
        item=source();topic={"topic_id":"topic_test","fingerprint":"revision_test","evidence":[item]}
        schema,quotes=native_contract(topic,[])
        validator=Draft202012Validator(schema["properties"]["facts"])
        validator.validate(quotes[:1])
        with self.assertRaises(ValidationError):validator.validate([{"evidence_id":item["evidence_id"],"quote":item["title"]+"（来源只有标题）"}])

    def test_invalid_delivery_does_not_mark_a_working_provider_unavailable(self):
        store=Store(":memory:")
        try:
            with patch("agent_v3.opencode_zen.executable",return_value="test-cli"):store.set_model(DEFAULT_MODEL)
            with patch("agent_v3.model.OpenCodeModel") as zen:
                zen.return_value.model=DEFAULT_MODEL
                zen.return_value.run_task.side_effect=StructuredDeliveryError("missing facts",{})
                with self.assertRaises(StructuredDeliveryError):GatedModel(store).run_task("intelligence",{},{},"")
            self.assertEqual(gate(store,DEFAULT_MODEL)["status"],"ready")
        finally:store.close()


class NativePipelineTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:");item=source();self.eid=item["evidence_id"]
        self.store.upsert_evidence(item);self.store.conn.commit();scan(self.store)
        self.tid=queue(self.store)[0]["topic_id"];approve_test_topic(self.store,self.tid);work.enqueue(self.store)
        self.rid=self.store.create_run("原生任务契约测试")

    def tearDown(self):self.store.close()

    def model(self,decision="archive",bad_quote=False,bad_delivery_once=False):
        database=self.store;topic_id=self.tid;eid=self.eid
        class Native:
            supports_tasks=True;model=DEFAULT_MODEL;last_session="ses_test_native";calls=0
            def run_task(self,stage,packet,schema,system,**kwargs):
                self.calls+=1
                if stage=="intelligence":
                    value=analysis({"topic_id":topic_id,"fingerprint":packet["topic"]["fingerprint"]},eid,decision)
                    if bad_quote:value["facts"][0]["quote"]="这段引文不在原文中"
                else:value=output(database,topic_id)
                response={"model":self.model,"result":value,"usage":{"total_tokens":100},"seconds":0.1,
                        "transport":"opencode-agent","session_id":self.last_session,"input_file":"test-only"}
                if bad_delivery_once and self.calls==1:
                    response["result"]={"summary":"incomplete test-only reply"}
                    raise StructuredDeliveryError("missing facts",response)
                return response
        return Native()

    def test_full_native_intelligence_saves_without_forcing_creative(self):
        result=process(self.store,self.rid,model=self.model())
        self.assertEqual(result["status"],"intelligence_ready")
        self.assertEqual(self.store.overview()["counts"]["topic_intelligence"],1)
        self.assertEqual(self.store.overview()["counts"]["creative"],0)

    def test_native_opportunity_routes_to_complete_validated_creative(self):
        result=process(self.store,self.rid,model=self.model("opportunity"))
        self.assertEqual(result["status"],"completed")
        self.assertEqual(self.store.overview()["counts"]["creative"],1)
        self.assertEqual(result["usage"]["total_tokens"],200)

    def test_native_invented_quote_is_rejected_and_task_retained(self):
        result=process(self.store,self.rid,model=self.model(bad_quote=True))
        self.assertEqual(result["status"],"ai_deferred")
        self.assertEqual(self.store.overview()["counts"]["topic_intelligence"],0)
        self.assertEqual(self.store.conn.execute("SELECT status FROM work_item").fetchone()[0],"deferred")

    def test_incomplete_native_output_retries_and_only_saves_the_valid_delivery(self):
        result=process(self.store,self.rid,model=self.model(bad_delivery_once=True))
        self.assertEqual(result["status"],"intelligence_ready")
        self.assertEqual(self.store.overview()["counts"]["topic_intelligence"],1)
        self.assertEqual(result["usage"]["total_tokens"],200)
        self.assertTrue(any(step["kind"]=="intelligence_validation" for step in self.store.get_run(self.rid)["steps"]))


if __name__ == "__main__":unittest.main()
