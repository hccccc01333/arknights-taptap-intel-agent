#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""materials（T5 素材获取）测试：抽取判据 + 溯源准入闸门 + PII + Thread。

重点锁死六件事（都是设计里写死的判据，不测等于没做）：
  1. **溯源是准入条件** —— 没有 moment_id / url 不是 TapTap 链接 → 一律丢弃（不是打回）
  2. **PII 绝不入库** —— 作者名可展示，但产物里不许出现 `author_name`
  3. **判据可核对** —— 原帖：ups 降序 + 标题或摘要 ≥8 字 + 剔纯表情；金句：帖子内排序 + 10–120 字
  4. **字段坑** —— 原帖点赞在 `ups`，评论的才叫 `supports`（posts.supports 全是 0）
  5. **不假装分类** —— 金句/段子的分类标 `pending_llm`（语义判断，代码不做）
  6. **分层降级** —— 只出代码可做的两类，`meme`/`remix_angle` 显式标注缺失
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LAB = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, str(LAB / f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


mt = _load("materials")


def post(mid, title="", summary="", ups=0, comments=0, hid="", htitle=""):
    return {"moment_id": str(mid), "title": title, "summary": summary,
            "ups": str(ups), "comments": str(comments), "supports": "0",
            "hashtag_id": hid, "hashtag_title": htitle, "pv_total": "100",
            "source_type": "hashtag", "crawled_at": "2026-10-01T12:00:00+08:00",
            "author_id_hash": "h123", "author_name": "不该入库的人名"}


def comment(mid, cid, content, supports=0):
    return {"moment_id": str(mid), "comment_id": str(cid), "content": content,
            "supports": str(supports), "source_type": "moment-comment",
            "crawled_at": "2026-10-01T12:00:00+08:00", "author_name": "评论者名"}


class TestPredicates(unittest.TestCase):
    def test_text_len_ignores_markup(self):
        # 去掉 #话题#、空白、@提及、零宽字符后，剩下的汉字才是有效长度
        self.assertEqual(mt.text_len("#话题# 你好  @某人\u200b"), 6)

    def test_low_info(self):
        for s in ("+1", "顶", "哈哈哈", "2333", "😀😀", "。。。", "!!!"):
            self.assertTrue(mt.is_low_info(s), s)
        for s in ("这个改动确实影响体验", "数值膨胀了"):
            self.assertFalse(mt.is_low_info(s), s)

    def test_topic_key_reuses_tracker_format(self):
        """★ 复用 topic_tracker 的 key 格式，两个系统因此可对齐。"""
        self.assertEqual(mt.topic_key_of("12345"), "hid:12345|page_view")
        self.assertIsNone(mt.topic_key_of(""))

    def test_material_id_is_stable(self):
        a = mt.material_id("original_post", "111")
        b = mt.material_id("original_post", "111")
        c = mt.material_id("original_post", "222")
        self.assertEqual(a, b, "同素材 id 必须稳定（幂等的前提）")
        self.assertNotEqual(a, c)


class TestAdmissionGate(unittest.TestCase):
    """★ 没有溯源就不算素材（素材层设计 §2.3）。"""

    def _m(self, **over):
        m = {"material_id": "m_x", "type": "original_post",
             "provenance": {"moment_id": "111",
                            "url": "https://www.taptap.cn/moment/111"}}
        m.update(over)
        return m

    def test_ok(self):
        ok, why = mt.admit(self._m())
        self.assertTrue(ok, why)

    def test_missing_moment_id_rejected(self):
        m = self._m()
        m["provenance"]["moment_id"] = ""
        ok, why = mt.admit(m)
        self.assertFalse(ok)
        self.assertIn("无法溯源", why)

    def test_bad_url_rejected(self):
        m = self._m()
        m["provenance"]["url"] = "https://example.com/moment/111"
        ok, why = mt.admit(m)
        self.assertFalse(ok)
        self.assertIn("TapTap", why)

    def test_pii_field_rejected(self):
        """作者名可展示，但**绝不入库**。"""
        ok, why = mt.admit(self._m(author_name="张三"))
        self.assertFalse(ok)
        self.assertIn("PII", why)

    def test_assert_raises_for_direct_callers(self):
        with self.assertRaises(ValueError):
            mt.assert_traceable({"material_id": "m", "provenance": {}})
        with self.assertRaises(ValueError):
            mt.assert_no_pii(self._m(nickname="x"))


class TestExtractOriginalPosts(unittest.TestCase):
    def test_ranks_by_ups_not_supports(self):
        """★ 埋坑验证：原帖点赞在 `ups`，posts.supports 全是 0 —— 看错字段会挑错。"""
        posts = [post(1, title="低赞的那条帖子内容", ups=1, hid="9", htitle="话题A"),
                 post(2, title="高赞的那条帖子内容", ups=99, hid="9", htitle="话题A")]
        r = mt.extract_original_posts(posts, per_topic=1)
        self.assertEqual(len(r["materials"]), 1)
        self.assertEqual(r["materials"][0]["provenance"]["moment_id"], "2")
        self.assertEqual(r["materials"][0]["provenance"]["metrics"]["ups"], 99)

    def test_min_length_filter(self):
        r = mt.extract_original_posts([post(1, title="短", summary="也短")])
        self.assertEqual(r["materials"], [])
        self.assertTrue(any("过短" in d["reason"] for d in r["dropped"]))

    def test_symbol_only_dropped(self):
        r = mt.extract_original_posts([post(1, summary="😀😀😀😀😀")])
        self.assertEqual(r["materials"], [])

    def test_per_topic_quota_and_dedup(self):
        posts = [post(i, title=f"内容够长的帖子{i}", ups=i, hid="9", htitle="话题A")
                 for i in range(1, 6)]
        r = mt.extract_original_posts(posts, per_topic=2)
        self.assertEqual(len(r["materials"]), 2)
        ids = [m["material_id"] for m in r["materials"]]
        self.assertEqual(len(ids), len(set(ids)), "同 moment_id 只出一次")

    def test_multi_topic_marked(self):
        """跨话题重复出现的帖子标「多话题命中」。"""
        posts = [post(1, title="同一个帖子出现在两个话题下", ups=5, hid="1", htitle="话题A"),
                 post(1, title="同一个帖子出现在两个话题下", ups=5, hid="2", htitle="话题B")]
        r = mt.extract_original_posts(posts, per_topic=5)
        self.assertEqual(len(r["materials"]), 1, "同 moment_id 只出一次")
        self.assertTrue(r["materials"][0]["multi_topic_keys"],
                        "应标注它在另一个话题下也命中")

    def test_unattributed_counted(self):
        r = mt.extract_original_posts([post(1, title="没有话题归属的帖子内容")])
        self.assertEqual(r["n_unattributed"], 1)


class TestExtractHotComments(unittest.TestCase):
    def test_length_window(self):
        coms = [comment(1, 1, "太短"), comment(1, 2, "这条长度刚好落在窗口内用来当候选"),
                comment(1, 3, "长" * 200)]
        r = mt.extract_hot_comments(coms, {})
        self.assertEqual([m["provenance"]["comment_id"] for m in r["materials"]], ["2"])

    def test_ranking_within_thread_only(self):
        """★ 帖子内排序（不跨帖统计）。"""
        coms = [comment(1, 11, "甲帖的低赞评论内容够长了", supports=1),
                comment(1, 12, "甲帖的高赞评论内容也够长", supports=9),
                comment(2, 21, "乙帖的唯一评论内容够长", supports=3)]
        r = mt.extract_hot_comments(coms, {}, per_thread=1)
        got = [m["provenance"]["comment_id"] for m in r["materials"]]
        self.assertEqual(sorted(got), ["12", "21"], "每帖各取自己帖内最高的")

    def test_classification_not_faked(self):
        """★ 金句 vs 段子 = 语义判断 → 代码不假装分好，标 pending_llm。"""
        r = mt.extract_hot_comments([comment(1, 1, "这条评论的长度刚好适合做候选")], {})
        self.assertEqual(r["materials"][0]["classification"], "pending_llm")

    def test_thread_context_missing_flagged(self):
        r = mt.extract_hot_comments([comment(999, 1, "评论所属的帖子不在库里")], {})
        self.assertEqual(r["materials"][0]["provenance"]["thread_context"], "missing")
        self.assertEqual(r["n_thread_context_missing"], 1)

    def test_thread_context_and_topic_from_post(self):
        posts = {"1": post(1, hid="77", htitle="话题X")}
        r = mt.extract_hot_comments([comment(1, 1, "评论长度落在窗口内可以当选")], posts)
        m = r["materials"][0]
        self.assertEqual(m["topic_key"], "hid:77|page_view")
        self.assertEqual(m["topic_title"], "话题X")


class TestThreads(unittest.TestCase):
    def test_thread_unit_keeps_context(self):
        """★ 存 Thread，不存孤立评论（保住语境）。"""
        threads, orphan = mt.build_threads(
            [post(1, title="帖标题", summary="帖摘要" * 5, hid="1", htitle="话题A")],
            [comment(1, 11, "评论一"), comment(1, 12, "评论二")])
        self.assertEqual(len(threads), 1)
        t = threads[0]
        self.assertEqual(t["thread_id"], "1")
        self.assertEqual(t["n_comments"], 2)
        self.assertEqual(sorted(t["comment_ids"]), ["11", "12"])
        self.assertEqual(t["title"], "帖标题")
        self.assertEqual(orphan, 0)

    def test_orphan_comment_creates_placeholder(self):
        threads, orphan = mt.build_threads([], [comment(9, 1, "评论")])
        self.assertEqual(orphan, 1)
        self.assertTrue(threads[0].get("post_missing"))


class TestRunAndPersist(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mp = self.tmp / "materials.jsonl"
        self.tp = self.tmp / "threads.jsonl"
        self.posts = self.tmp / "posts.csv"
        self.coms = self.tmp / "comments.csv"
        self._write(self.posts, [post(1, title="一个足够长的帖子标题", ups=10,
                                      hid="5", htitle="话题A"),
                                 post(2, summary="没有标题但摘要够长的一段话", ups=3)],
                    list(post(0).keys()))
        self._write(self.coms, [comment(1, 11, "这条评论长度落在窗口内适合当选", supports=5)],
                    list(comment(0, 0, "x").keys()))

    def _write(self, path, rows, fields):
        import csv
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def run_it(self, **kw):
        return mt.run(posts_csv=self.posts, comments_csv=self.coms,
                      materials_path=self.mp, threads_path=self.tp, **kw)

    def test_run_writes_both_products(self):
        res = self.run_it()
        rows = [json.loads(l) for l in self.mp.read_text(encoding="utf-8").splitlines() if l]
        threads = [json.loads(l) for l in self.tp.read_text(encoding="utf-8").splitlines() if l]
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(len(threads), 2, "每个帖一个 Thread（含无评论的）")
        self.assertEqual(res["traceable_rate"], 1.0, "本样例全部可溯源")

    def test_no_pii_in_products(self):
        """★ 产物里绝不许出现 PII（源数据里有 author_name，产物里没有）。"""
        self.run_it()
        for line in self.mp.read_text(encoding="utf-8").splitlines():
            self.assertNotIn("author_name", line)
            self.assertNotIn("不该入库的人名", line)

    def test_missing_llm_types_are_disclosed(self):
        """★ 分层降级：只出代码可做的两类，LLM 两类**显式标注缺失**。"""
        res = self.run_it()
        self.assertEqual(sorted(res["by_type"]), ["hot_comment", "original_post"])
        self.assertIn("meme", res["missing_types"])
        self.assertIn("remix_angle", res["missing_types"])
        self.assertTrue(res["missing_reason"])

    def test_bad_material_rejected_not_crashed(self):
        """准入不通过 → **丢弃并计数**，不是打回整批、也不是崩。"""
        bad = {"material_id": "m_bad", "type": "original_post", "text": "x",
               "provenance": {"moment_id": "", "url": "https://evil.example/x"}}
        with mock.patch.object(mt, "extract_original_posts",
                               return_value={"materials": [bad], "dropped": [],
                                             "n_topics": 0, "n_unattributed": 0}):
            res = self.run_it()
        self.assertEqual(res["n_rejected_by_gate"], 1)
        self.assertLess(res["traceable_rate"], 1.0)
        self.assertTrue(res["rejected_samples"])

    def test_status_and_report_render(self):
        self.run_it()
        s = mt.status(materials_path=self.mp, threads_path=self.tp)
        self.assertIn("素材库状态", s)
        self.assertIn("需 LLM", s)


if __name__ == "__main__":
    unittest.main()
