#!/usr/bin/env bash
# 一次性分层重排：旧编号目录 -> 分层目录（L1..L6 + runtime + data）
# 用 git mv 保留历史；执行前要求工作区干净（已由 a358a06 快照保证）
set -e
cd "$(dirname "$0")/.."

say() { echo "  -> $*"; }
mv1() { git mv "$1" "$2" 2>/dev/null || mv "$1" "$2"; say "$1 => $2"; }

echo "== 1. 建新骨架 =="
mkdir -p L1_data_source/schema L1_data_source/adapters \
         L1_data_source/collectors/taptap L1_data_source/collectors/bilibili \
         L1_data_source/collectors/douyin  L1_data_source/collectors/weibo
mkdir -p L2_signal/cross_channel L2_signal/lab
mkdir -p L3_semantic/channels/bilibili L3_semantic/channels/douyin L3_semantic/channels/weibo L3_semantic/qc
mkdir -p L4_decision L5_generation L6_delivery/briefing L6_delivery/dashboard \
         L6_delivery/period_reports L6_delivery/contrast/bilibili L6_delivery/contrast/douyin L6_delivery/contrast/weibo
mkdir -p runtime/tests L4_decision/tests L5_generation/tests L2_signal/tests
mkdir -p data/raw/taptap data/raw/bilibili data/raw/douyin data/raw/weibo \
         data/events data/annotations/shards data/facts data/processed data/state data/outputs

echo "== 2. L1 信号采集层 =="
for f in crawl_taptap_community.py crawl_taptap_discovery.py crawl_taptap_reviews.py crawl_taptap_user.py config.example.env 原始提示词.md 提示词工程复盘.md 爬取提示词构建.md; do
  [ -e "01爬虫/$f" ] && mv1 "01爬虫/$f" "L1_data_source/collectors/taptap/$f"
done
[ -d "01爬虫/TAPTAP网页数据json" ] && mv1 "01爬虫/TAPTAP网页数据json" "data/raw/taptap/web_json"
[ -e "01爬虫/pii_hash.py" ] && mv1 "01爬虫/pii_hash.py" "L1_data_source/pii_hash.py"

mv1 "06对照_B站/crawl_bili_comments.py"  "L1_data_source/collectors/bilibili/crawl_bili_comments.py"
mv1 "06对照_B站/config.example.env"      "L1_data_source/collectors/bilibili/config.example.env"
mv1 "07对照_抖音/crawl_douyin_comments.py" "L1_data_source/collectors/douyin/crawl_douyin_comments.py"
mv1 "07对照_抖音/config.example.env"       "L1_data_source/collectors/douyin/config.example.env"
mv1 "08对照_微博/crawl_weibo_posts.py"     "L1_data_source/collectors/weibo/crawl_weibo_posts.py"
mv1 "08对照_微博/config.example.env"       "L1_data_source/collectors/weibo/config.example.env"

echo "== 3. L1 原始数据（按平台归湖） =="
mv1 "02数据_platform/discovery_posts.csv"    "data/raw/taptap/discovery_posts.csv"
mv1 "02数据_platform/discovery_comments.csv" "data/raw/taptap/discovery_comments.csv"
mv1 "02数据_platform/hot_hashtags.csv"       "data/raw/taptap/hot_hashtags.csv"
mv1 "02数据_platform/announcements"          "data/raw/taptap/announcements"
[ -e "02数据_platform/_ak_gid.txt" ]    && mv1 "02数据_platform/_ak_gid.txt"    "data/raw/taptap/_ak_gid.txt"
[ -e "02数据_platform/_gid_cache.json" ] && mv1 "02数据_platform/_gid_cache.json" "data/raw/taptap/_gid_cache.json"
mv1 "02数据_platform/features"               "data/facts/features"
mv1 "02数据_platform/materials"              "data/materials"
[ -e "02数据/reviews.csv" ] && mv1 "02数据/reviews.csv" "data/raw/taptap/reviews.csv"
mv1 "02数据/processed" "data/processed/reviews"
mv1 "02数据/figures"   "data/processed/figures"

mv1 "06对照_B站/videos_sample.csv"    "data/raw/bilibili/videos_sample.csv"
mv1 "06对照_B站/comments_sample.csv"  "data/raw/bilibili/comments_sample.csv"
[ -d "06对照_B站/raw" ]      && mv1 "06对照_B站/raw"      "data/raw/bilibili/json"
[ -d "06对照_B站/raw_llm" ]  && mv1 "06对照_B站/raw_llm"  "data/raw/bilibili/llm_json"
[ -d "06对照_B站/run_logs" ] && mv1 "06对照_B站/run_logs" "data/raw/bilibili/run_logs"
[ -e "06对照_B站/checkpoint_crawl.json" ] && mv1 "06对照_B站/checkpoint_crawl.json" "data/raw/bilibili/checkpoint_crawl.json"

mv1 "07对照_抖音/videos_sample.csv"   "data/raw/douyin/videos_sample.csv"
mv1 "07对照_抖音/comments_sample.csv" "data/raw/douyin/comments_sample.csv"
[ -d "07对照_抖音/raw" ]      && mv1 "07对照_抖音/raw"      "data/raw/douyin/json"
[ -d "07对照_抖音/raw_llm" ]  && mv1 "07对照_抖音/raw_llm"  "data/raw/douyin/llm_json"
[ -d "07对照_抖音/run_logs" ] && mv1 "07对照_抖音/run_logs" "data/raw/douyin/run_logs"
[ -e "07对照_抖音/checkpoint_crawl.json" ] && mv1 "07对照_抖音/checkpoint_crawl.json" "data/raw/douyin/checkpoint_crawl.json"

mv1 "08对照_微博/posts_sample.csv"    "data/raw/weibo/posts_sample.csv"
mv1 "08对照_微博/comments_sample.csv" "data/raw/weibo/comments_sample.csv"
[ -d "08对照_微博/raw" ]      && mv1 "08对照_微博/raw"      "data/raw/weibo/json"
[ -d "08对照_微博/raw_llm" ]  && mv1 "08对照_微博/raw_llm"  "data/raw/weibo/llm_json"
[ -d "08对照_微博/run_logs" ] && mv1 "08对照_微博/run_logs" "data/raw/weibo/run_logs"

echo "== 4. L2 信号计算层 =="
[ -e "02数据/preprocess_reviews.py" ] && mv1 "02数据/preprocess_reviews.py" "L2_signal/preprocess_reviews.py"
[ -e "02数据/数据处理提示词.md" ]     && mv1 "02数据/数据处理提示词.md"     "L2_signal/数据处理提示词.md"
for f in build_channel_facts.py synthesize_cross_channel.py facts_cross_channel.json latest_summary.json README.md; do
  [ -e "09跨渠道AI/$f" ] && mv1 "09跨渠道AI/$f" "L2_signal/cross_channel/$f"
done
[ -d "09跨渠道AI/raw_llm" ] && mv1 "09跨渠道AI/raw_llm" "L2_signal/cross_channel/raw_llm"
[ -d "09跨渠道AI/reports" ] && mv1 "09跨渠道AI/reports" "L2_signal/cross_channel/reports"
for f in README.md anomaly_diagnosis.py event_impact.py events.csv model_eval.py _probe_events.py _probe_events2.py; do
  [ -e "10分析实验室/$f" ] && mv1 "10分析实验室/$f" "L2_signal/lab/$f"
done
[ -d "10分析实验室/outputs" ] && mv1 "10分析实验室/outputs" "L2_signal/lab/outputs"
[ -d "10分析实验室/reports" ] && mv1 "10分析实验室/reports" "L2_signal/lab/reports"
mv1 "11情报Agent/features.py" "L2_signal/features.py"
for t in test_features.py; do [ -e "11情报Agent/tests/$t" ] && mv1 "11情报Agent/tests/$t" "L2_signal/tests/$t"; done

echo "== 5. L3 语义理解层 =="
[ -e "02数据/annotate_reviews.py" ]    && mv1 "02数据/annotate_reviews.py"    "L3_semantic/annotate_reviews.py"
[ -e "02数据/run_annotate_shards.py" ] && mv1 "02数据/run_annotate_shards.py" "L3_semantic/run_annotate_shards.py"
mv1 "11情报Agent/announcements.py" "L3_semantic/announcements.py"
for t in test_announcements.py test_comment_parse.py; do
  [ -e "11情报Agent/tests/$t" ] && mv1 "11情报Agent/tests/$t" "L3_semantic/tests_$t"
done
mkdir -p L3_semantic/tests
[ -e "L3_semantic/tests_test_announcements.py" ] && mv "L3_semantic/tests_test_announcements.py" "L3_semantic/tests/test_announcements.py"
[ -e "L3_semantic/tests_test_comment_parse.py" ] && mv "L3_semantic/tests_test_comment_parse.py"  "L3_semantic/tests/test_comment_parse.py"

mv1 "06对照_B站/annotate_bili_sample.py"     "L3_semantic/channels/bilibili/annotate_bili_sample.py"
mv1 "06对照_B站/annotations_bili_sample.csv" "L3_semantic/channels/bilibili/annotations_bili_sample.csv"
[ -e "06对照_B站/checkpoint_annotate.json" ] && mv1 "06对照_B站/checkpoint_annotate.json" "L3_semantic/channels/bilibili/checkpoint_annotate.json"
mv1 "07对照_抖音/annotate_douyin_sample.py"     "L3_semantic/channels/douyin/annotate_douyin_sample.py"
mv1 "07对照_抖音/annotations_douyin_sample.csv" "L3_semantic/channels/douyin/annotations_douyin_sample.csv"
[ -e "07对照_抖音/checkpoint_annotate.json" ] && mv1 "07对照_抖音/checkpoint_annotate.json" "L3_semantic/channels/douyin/checkpoint_annotate.json"
mv1 "08对照_微博/annotate_weibo_sample.py"     "L3_semantic/channels/weibo/annotate_weibo_sample.py"
mv1 "08对照_微博/annotations_weibo_sample.csv" "L3_semantic/channels/weibo/annotations_weibo_sample.csv"
[ -e "08对照_微博/checkpoint_annotate.json" ] && mv1 "08对照_微博/checkpoint_annotate.json" "L3_semantic/channels/weibo/checkpoint_annotate.json"

echo "== 6. L3 标注资产 =="
for f in 03标注结果/*; do
  b=$(basename "$f")
  case "$b" in
    README.md|*.csv|*.json) mv1 "$f" "data/annotations/$b" ;;
    qc)                     mv1 "$f" "L3_semantic/qc_orig" ;;
    *)                      mv1 "$f" "data/annotations/shards/$b" ;;
  esac
done

echo "== 7. L4 决策层 =="
for f in topic_tracker.py ferment_judge.py events.py freshness.py anomaly_lite.py intel_stats.py; do
  [ -e "11情报Agent/$f" ] && mv1 "11情报Agent/$f" "L4_decision/$f"
done
for t in test_topic_tracker.py test_events.py test_freshness.py; do
  [ -e "11情报Agent/tests/$t" ] && mv1 "11情报Agent/tests/$t" "L4_decision/tests/$t"
done

echo "== 8. L5 生成层 =="
for f in materials.py community_insight.py community_ops.py platform_insight.py risk_insight.py user_flow.py cross_game_compare.py; do
  [ -e "11情报Agent/$f" ] && mv1 "11情报Agent/$f" "L5_generation/$f"
done
for t in test_materials.py test_community_ops.py test_platform_insight.py; do
  [ -e "11情报Agent/tests/$t" ] && mv1 "11情报Agent/tests/$t" "L5_generation/tests/$t"
done

echo "== 9. L6 交付层 =="
mv1 "11情报Agent/daily_agent.py" "L6_delivery/briefing/daily_agent.py"
[ -e "11情报Agent/tests/test_daily_agent.py" ] && mv1 "11情报Agent/tests/test_daily_agent.py" "L6_delivery/briefing/test_daily_agent.py"
mv1 "11情报Agent/reports"  "L6_delivery/briefing/reports"
mv1 "11情报Agent/outputs"  "data/outputs/agent"
for f in 04日报周报/*; do [ -e "$f" ] && mv1 "$f" "L6_delivery/period_reports/$(basename "$f")"; done
for f in 05展示页/*; do   [ -e "$f" ] && mv1 "$f" "L6_delivery/dashboard/$(basename "$f")"; done
mv1 "06对照_B站/build_contrast_report.py" "L6_delivery/contrast/bilibili/build_contrast_report.py"
[ -d "06对照_B站/reports" ] && mv1 "06对照_B站/reports" "L6_delivery/contrast/bilibili/reports"
[ -e "06对照_B站/README.md" ] && mv1 "06对照_B站/README.md" "L6_delivery/contrast/bilibili/README.md"
[ -d "07对照_抖音/reports" ] && mv1 "07对照_抖音/reports" "L6_delivery/contrast/douyin/reports"
[ -e "07对照_抖音/README.md" ] && mv1 "07对照_抖音/README.md" "L6_delivery/contrast/douyin/README.md"
[ -d "08对照_微博/reports" ] && mv1 "08对照_微博/reports" "L6_delivery/contrast/weibo/reports"
[ -e "08对照_微博/README.md" ] && mv1 "08对照_微博/README.md" "L6_delivery/contrast/weibo/README.md"

echo "== 10. runtime 控制面 =="
for f in harness.py task_contracts.py agent_graph.py scheduler.py; do
  [ -e "11情报Agent/$f" ] && mv1 "11情报Agent/$f" "runtime/$f"
done
for t in test_harness.py test_scheduler.py test_langgraph_design.py; do
  [ -e "11情报Agent/tests/$t" ] && mv1 "11情报Agent/tests/$t" "runtime/tests/$t"
done
[ -d "11情报Agent/state" ] && mv1 "11情报Agent/state" "data/state"

echo "== 11. 提示词工程归档 =="
[ -d "11提示词工程" ] && mv1 "11提示词工程" "docs/prompt_engineering"

echo "== 12. 清残留空目录 =="
rm -rf 02数据/__pycache__ 10分析实验室/__pycache__ 01爬虫/__pycache__ games/__pycache__ 11情报Agent/__pycache__ 2>/dev/null || true
for d in 01爬虫 02数据 02数据_platform 03标注结果 04日报周报 05展示页 06对照_B站 07对照_抖音 08对照_微博 09跨渠道AI 10分析实验室 11情报Agent 11提示词工程; do
  [ -d "$d" ] && rmdir --ignore-fail-on-non-empty "$d" 2>/dev/null && say "removed empty $d" || [ -d "$d" ] && say "kept non-empty $d"
done

echo "== DONE =="
