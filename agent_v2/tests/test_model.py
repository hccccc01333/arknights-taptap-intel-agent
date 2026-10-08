import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock

from agent_v2.model import LiveModel,LLMUnavailable


class ModelBoundaryTests(unittest.TestCase):
    def model(self):
        model=object.__new__(LiveModel)
        model.model="nvidia/nemotron-3-ultra-550b-a55b:free"
        model.fallback="openrouter/free"
        model.router=SimpleNamespace(reasoning_effort="low")
        return model

    def test_gateway_heartbeat_cannot_keep_response_alive_indefinitely(self):
        response=Mock(status_code=200)
        response.iter_content.return_value=iter([b" ",b" "])
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        with patch("agent_v2.model.resolve_provider",return_value={"base_url":"https://openrouter.ai/test"}),patch("agent_v2.model.provider_key",return_value="test"),patch("agent_v2.model.requests.post",return_value=context),patch("agent_v2.model.time.monotonic",side_effect=[0,1,51]):
            with self.assertRaisesRegex(LLMUnavailable,"50 秒"):
                self.model()._chat([],[])

    def test_failure_switches_only_to_configured_free_fallback(self):
        model=self.model()
        with patch.object(model,"_chat",side_effect=[LLMUnavailable("503 overloaded"),{"model":"actual-free-model","tool_calls":[]}]) as chat:
            result=model.decide([],[])
        self.assertEqual(chat.call_count,2)
        self.assertEqual(model.model,"openrouter/free")
        self.assertEqual(result["model"],"actual-free-model")

    def test_repeated_failure_does_not_loop_or_manufacture_output(self):
        model=self.model()
        with patch.object(model,"_chat",side_effect=LLMUnavailable("429 rate limit")) as chat:
            with self.assertRaises(LLMUnavailable):model.decide([],[])
        self.assertEqual(chat.call_count,2)

    def test_daily_quota_failure_is_distinguished_from_temporary_rate_limit(self):
        response=Mock(status_code=429)
        response.json.return_value={"error":{"message":"Rate limit exceeded: free-models-per-day"}}
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        with patch("agent_v2.model.resolve_provider",return_value={"base_url":"https://openrouter.ai/test"}),patch("agent_v2.model.provider_key",return_value="test"),patch("agent_v2.model.requests.post",return_value=context):
            with self.assertRaisesRegex(LLMUnavailable,"当天调用额度已用尽"):
                self.model()._chat([],[])


if __name__=="__main__":unittest.main()
