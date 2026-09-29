# 明日方舟 · TapTap 玩家情报 Agent

[![CI](https://github.com/hccccc01333/arknights-taptap-intel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/hccccc01333/arknights-taptap-intel-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent_Skills-skills%2F-111827)](./skills/)

面向《明日方舟》玩家评价的**每日情报 Agent**：采集 → 清洗 → LLM 结构化标注 → 风险分层 → 决策路由 → 当日简报；叠加 B 站 / 抖音 / 微博对照。全程 **facts 锁数**——数字由代码计算，LLM 不生成任何数字。

| 项 | 内容 |
|----|------|
| 主链 | TapTap《明日方舟》评价（app_id=70253） |
| 数据面 | 四条通道分目录：评分区评论 / 单游戏社区（帖子+热评）/ 平台级发现流（话题热榜→话题下帖子）/ 用户社区足迹（跨游戏流向） |
| 隐私 | 用户标识加盐哈希（HMAC-SHA256，盐不入库）；分析层**只出聚合**，产出不含单个用户标识或轨迹 |
| 情报 Agent | 感知 → 决策（规则兜底 + LLM 可选）→ 行动 → 简报；显著异动触发深挖 |
| 风险分层 | 舆情侧流失风险信号：高投入不满 / 高投入不推荐 / 修辞伪装负向 / 可行动差评 |
| 标注 | v1.4 两段式：字面 / 意图 / 主情绪 / 修辞分通道 |
| 游戏档案 | [`games/<key>.json`](./games/README.md) 驱动 app_id / 阈值 / 等价名 / 跨渠关键词——换档案即换游戏 |
| Skills | [`skills/`](./skills/README.md)：5 个 Agent Skills |
| 架构 | 四层：数据 / 管线 / Agent 控制 / 支撑。已完成部分落在**数据层 + 管线感知侧 + 支撑层**；**控制层仍是 v1 流水线**，LangGraph v2 未动工——模块级位置对照见 [架构设计 §1.1](./docs/Agent-v2-架构设计.md) |
| 判据 | 热点是否值得社区做话题，用「**社区可发酵度**」判断（`ferment_judge`），不是「是否属于我建档的游戏」——实测旧判据命中 0/10，新判据认为 5/10 可发酵 |

> 社交三渠为限量对照；降级语料会在 facts / 看板标明，不作全网 KPI。

**快速入口：** [打开看板](./05展示页/index.html) · [每日情报简报](./11情报Agent/reports/daily_intel_latest.md) · [Agent v2 架构设计](./docs/Agent-v2-架构设计.md) · [方法论](./docs/方法论.md) · [构建说明](./docs/构建说明.md)

---

## 解决什么问题

运营侧从海量玩家评价里要看到的不只是情绪结构，而是三件事：**每天该看什么**（异动是否显著）、**该防什么**（负向风险是否集中在高投入玩家）、**该转给谁**（哪些吐槽可以转成具体运营动作）。难处在于：讽刺 / 高级黑 / 反串等伪装话术会被「单字段正负」漏掉，异常波动会被抽样噪声淹没，而 LLM 生成的报告里数字最容易失真。

本仓库的处理方式：

1. **结构化标注打底**：DeepSeek JSON 输出，v1.4 两段式标注（先挖字面之下的线索，再判整体态度），字面 / 意图 / 主情绪 / 修辞分通道——反话、阴阳、高级黑、反串单独识别。
2. **风险分层**：按「高投入不满（≥100h 玩家的负向）、高投入不推荐、修辞伪装负向、可行动差评」四层切分，并按数值、活动、商业化、性能、玩法等主题映射干预方向。
3. **每日情报 Agent**：感知（代码算 facts）→ 决策（显著性检验 + 规则引擎兜底，LLM 可选）→ 行动（显著异动触发深挖，否则常规监测）→ 简报。LLM 不生成任何数字，依赖异常时自动降级并写明原因。
4. **可复现交付**：日 / 周报（四问法 + 执行摘要；薄样本禁写环比）、跨渠道 facts 锁数综合、离线 HTML 看板；TapTap 采集带字段契约与断点续跑。

---

## 技术栈

| 类别 | 选用 |
|------|------|
| 语言 / 数据 | Python、Pandas（清洗 / 异动分解） |
| 情报 Agent | **纯标准库实现**（两比例 z 检验 / Wilson CI / facts 锁数），零第三方依赖 |
| LLM | DeepSeek API（可选增强：决策与定性；无 Key 时规则引擎 + 模板全链路可跑） |
| 交付 | Markdown 报告、离线 HTML 看板 |
| 质量 | 95 个单元测试（stdlib unittest）+ CI（测试 / 报告重建 / 堆栈与 PII 卫生检查） |
| Agent | [Agent Skills](https://agentskills.io)（`skills/*/SKILL.md`） |

---

## 效果预览

![看板首屏](./docs/preview_dashboard.png)

*离线看板首屏：本周风险评级、洞察链路、高风险主题与决策队列。*

| 产出 | 入口 |
|------|------|
| 每日情报简报（异动决策 + 深挖） | [`11情报Agent/reports/daily_intel_latest.md`](./11情报Agent/reports/daily_intel_latest.md) |
| 流失风险分层 | [`11情报Agent/reports/risk_insight_latest.md`](./11情报Agent/reports/risk_insight_latest.md) |
| 跨游戏同口径对比 | [`11情报Agent/reports/cross_game_compare_latest.md`](./11情报Agent/reports/cross_game_compare_latest.md) |
| 单游戏社区话题流 | [`11情报Agent/reports/community_insight_wuthering-waves_latest.md`](./11情报Agent/reports/community_insight_wuthering-waves_latest.md) |
| 平台级发现流（事件信号） | [`11情报Agent/reports/platform_insight_latest.md`](./11情报Agent/reports/platform_insight_latest.md) |
| 用户流动（跨游戏流向 / 投入度×流动） | [`11情报Agent/reports/user_flow_wuthering-waves_latest.md`](./11情报Agent/reports/user_flow_wuthering-waves_latest.md) |
| 日 / 周报终稿 | [`04日报周报/reports/`](./04日报周报/reports/) |
| 跨渠道综合简报 | [`09跨渠道AI/reports/`](./09跨渠道AI/reports/) |
| 离线看板（双击即开） | [`05展示页/index.html`](./05展示页/index.html) |

---

## 快速开始

- Python 3.10+；**情报 Agent 模块零依赖**，clone 后无需安装任何包即可运行
- （可选）`DEEPSEEK_API_KEY`：标注 / 跨渠 LLM 综合 / Agent 的 LLM 决策与定性；无 Key 走规则模式

```bash
git clone https://github.com/hccccc01333/arknights-taptap-intel-agent.git
cd arknights-taptap-intel-agent

# 情报 Agent（零依赖，30 秒出简报）
python 11情报Agent/daily_agent.py

# 完整链路（清洗 / 标注 / 看板等需要第三方依赖时）
pip install -r requirements.txt
python scripts/refresh_demo.py
```

看板双击 `05展示页/index.html` 即可打开。

---

## 使用说明

```bash
# 每日情报 Agent（感知 → 决策 → 行动 → 简报；无 Key 走规则模式）
python 11情报Agent/risk_insight.py          # 流失风险分层
python 11情报Agent/anomaly_lite.py          # 零依赖异动感知（近 4 窗检验）
python 11情报Agent/daily_agent.py           # 当日情报简报

# 清洗与标注（标注需 API Key）
python 02数据/preprocess_reviews.py
python 02数据/annotate_reviews.py --limit 50

# 日 / 周报与跨渠综合
python 04日报周报/build_skill_reports.py
python 09跨渠道AI/build_channel_facts.py
python 09跨渠道AI/synthesize_cross_channel.py

# 社区通道（面向社区公司视角）：单游戏社区帖子流 → 平台级发现流
python 01爬虫/crawl_taptap_community.py --game wuthering-waves --comment-limit 20
python 11情报Agent/community_insight.py --game wuthering-waves
python 01爬虫/crawl_taptap_discovery.py --source all --from-hot 3 --comment-limit 12
python 11情报Agent/platform_insight.py

# 用户流动（跨游戏足迹；分层取样保证高/低投入有对照）
python 01爬虫/crawl_taptap_user.py --game wuthering-waves --from-reviews --limit-users 120 --sample stratified
python 11情报Agent/user_flow.py --game wuthering-waves

# 分析实验室 → 重建看板
python 10分析实验室/anomaly_diagnosis.py
python 10分析实验室/model_eval.py
python 10分析实验室/event_impact.py
python 05展示页/build_dashboard.py

# 跨游戏同口径对比（结构层不依赖标注）
python 11情报Agent/cross_game_compare.py \
  --clean "明日方舟=02数据/processed/reviews_clean.csv" \
  --clean "鸣潮=02数据_wuthering_waves/processed/reviews_clean.csv"

# 单元测试（纯标准库，55+ 个用例）
python -m unittest discover -s 11情报Agent/tests -v
```

---

## 目录结构

```text
01爬虫/          TapTap 采集
02数据/          清洗与标注脚本
03标注结果/      annotations_v1_4.csv · QC · 修辞口径
04日报周报/      日/周报生成与示例
05展示页/        离线看板
06–08对照_*/     B站 / 抖音 / 微博
09跨渠道AI/      facts 锁数 + 综合简报
10分析实验室/    异动 / 评估 / 事件
11情报Agent/     每日情报 Agent（风险分层 + 决策路由 + 简报）
games/           游戏档案（参数化入口：换档案即换游戏）
skills/          Agent Skills（唯一 Skill 源目录）
docs/            方法论与构建说明
scripts/         刷新演示、同步 Skills
```

---

## Agent Skills

| Skill | 作用 |
|-------|------|
| [`arknights-taptap-crawl`](./skills/arknights-taptap-crawl/SKILL.md) | 爬虫字段契约 |
| [`arknights-llm-annotate-v14`](./skills/arknights-llm-annotate-v14/SKILL.md) | 标注两段式 v1.4 |
| [`arknights-taptap-yuqing-report`](./skills/arknights-taptap-yuqing-report/SKILL.md) | 日/周报写法 |
| [`arknights-cross-channel-facts`](./skills/arknights-cross-channel-facts/SKILL.md) | 跨渠 facts 锁数 |
| [`arknights-daily-intel-agent`](./skills/arknights-daily-intel-agent/SKILL.md) | 每日情报 Agent |

```bash
python scripts/sync_agent_skills.py
```

详见 [`skills/README.md`](./skills/README.md)。

---

## 边界说明

- 主 KPI 仅 TapTap 评价切片，不是全网舆情中台  
- 抖音 / 微博可能含降级样本，叙事中会标明  
- 模型评估在人工金标回收前可能使用 proxy 口径，见分析实验室报告  
- 风险分层是**舆情侧风险信号**（发声用户口径），不是用户流失预测；无留存 / 回流数据前不做因果与转化归因  
- 高投入阈值为展示口径（默认 ≥100h，`--high-hours` 可配置），分位点校准参考见风险分层报告  

---

## 路线图

- [x] TapTap 主链采集 / 标注 / 日周报 / 看板  
- [x] 四渠对照 + facts 锁数综合  
- [x] 分析实验室（异动 / 评估 / 事件）  
- [x] Agent Skills 打包（`skills/`，5 个）  
- [x] 每日情报 Agent（感知 / 决策路由 / 简报）+ 舆情侧风险分层  
- [x] 零依赖感知层（`intel_stats` 统计）+ 48 个单元测试 + CI  
- [ ] LLM 决策与定性路径实跑验证（当前规则模式全链路可用）  
- [ ] 人工金标回收后输出正式一致率  
- [ ] 传播维度：修复 support_count 采集后激活「高传播差评」层  
- [ ] 对照渠在可用 Cookie 下提升真采样占比  

---

## 许可证

本项目采用 [MIT License](./LICENSE)。
