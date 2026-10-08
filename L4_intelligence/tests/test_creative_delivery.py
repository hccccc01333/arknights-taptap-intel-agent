import json
import unittest
from unittest.mock import mock_open, patch

from L4_intelligence.intelligence.hotspot_to_creative import normalize_creative, persist_creatives


def draft(title="热点 A"):
    return normalize_creative({"name": "游戏交流提案", "where": "相关游戏社区", "steps": ["核查来源", "发布提案"]},
                              {"title": title, "platform": "bilibili"}, "游戏", "content", 4, False, ["原文链接"])


class CreativeDeliveryTests(unittest.TestCase):
    def test_topic_and_type_ids_are_stable_and_distinct(self):
        self.assertEqual(draft()["idea_id"], draft()["idea_id"])
        self.assertNotEqual(draft("A")["idea_id"], draft("B")["idea_id"])

    def test_empty_generation_does_not_overwrite_last_successful_file(self):
        with patch("builtins.open") as opened:
            with self.assertRaises(ValueError):
                persist_creatives([], "unused.json")
            opened.assert_not_called()

    def test_rule_proposals_cannot_be_published_as_model_output(self):
        item = {**draft(), "generated_by": "rule"}
        with patch("builtins.open") as opened:
            with self.assertRaises(ValueError):
                persist_creatives([item], "unused.json")
            opened.assert_not_called()

    def test_generated_draft_has_watermark_and_is_delivered_to_web_artifact(self):
        opened = mock_open()
        with patch("builtins.open", opened), patch("os.makedirs"):
            result = persist_creatives([draft()], "unused.json")
        written = json.loads("".join(c.args[0] for c in opened().write.call_args_list))
        self.assertEqual(written["creatives"][0]["review_status"], "unreviewed")
        self.assertTrue(written["generated_at"])
        self.assertEqual(written["meta"]["generated_count"], 1)
        self.assertEqual(result, written)


if __name__ == "__main__":
    unittest.main()
