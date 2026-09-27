# 明日方舟 · TapTap 玩家情报 Agent

[![CI](https://github.com/hccccc01333/arknights-taptap-intel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/hccccc01333/arknights-taptap-intel-agent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Agent Skills](https://img.shields.io/badge/Agent_Skills-skills%2F-111827)](./skills/)

以 **TapTap 评价为主链**：采集 → 清洗 → LLM 结构化标注 → **风险分层 → 每日情报 Agent（决策路由）** → 日/周报与离线看板；叠加 B 站 / 抖音 / 微博对照，全程 **facts 锁数**——数字由代码计算，LLM 不生成任何数字。

| 项 | 内容 |
|----|------|
| 主链 | TapTap《明日方舟》评价（app_id=70253） |
| 标注 | v1.4：字面 / 意图 / 主情绪 / 修辞分通道 |
| 情报 Agent | 感知 → 决策（规则兜底 + LLM 可选）→ 行动 → 简报；显著异动触发深挖 |
| 风险分层 | 舆情侧流失风险信号：高投入不满 / 高投入不推荐 / 修辞伪装负向 / 可行动差评 |
| 跨渠 | 数字由代码锁定，LLM 只写定性 |
| Skills | [`skills/`](./skills/README.md)：5 个 Agent Skills |

> 社交三渠为限量对照；降级语料会在 facts / 看板标明，不作全网 KPI。

**快速入口：** [打开看板](./05展示页/index.html) · [构建说明](./docs/构建说明.md) · [方法论](./docs/方法论.md) · [日/周报示例](./04日报周报/reports/)

---

## 目录

- [功能特性](#功能特性)
- [解决什么问题](#解决什么问题)
- [技术栈](#技术栈)
- [效果预览](#效果预览)
- [快速开始](#快速开始)
- [使用说明](#使用说明)
- [目录结构](#目录结构)
- [Agent Skills](#agent-skills)
- [边界说明](#边界说明)
- [路线图](#路线图)
- [许可证](#许可证)

---

## 功能特性

- **评价采集**：TapTap 列表接口翻页、字段契约、断点续跑  
- **结构化标注**：DeepSeek JSON 输出；v1.4 两段式（裂隙线索 → 整体态度）  
- **日 / 周报**：四问法 + 执行摘要四句；薄样本禁止瞎写环比  
- **多渠对照**：B站 / 抖音 / 微博限量切片 + 四渠矩阵  
- **跨渠道 AI**：`facts` 锁数 → 校验失败重试 / 模板降级  
- **分析实验室**：异动显著性、模型评估、事件前后窗  
- **每日情报 Agent**：感知（facts 锁数）→ 决策（规则兜底 + LLM 可选）→ 行动（深挖/常规路由）→ 简报；流失风险分层 + 修辞伪装负向识别  
- **可复用 Skills**：爬虫 / 标注 / 报告 / 跨渠规格放在 `skills/`

---

## 解决什么问题

运营侧需要从海量玩家评价里快速看到的不只是情绪结构，而是**每天该看什么、该防什么、该转给谁**：负向风险是否集中在高投入玩家、哪些吐槽可以转成运营动作、以及讽刺 / 高级黑 / 反串等不易被「单字段正负」抓住的伪装话术。

本仓库把非结构化评论变成可聚合指标，并在此基础上跑一个**每日情报 Agent**：代码计算并锁定事实指标（facts）→ 规则/LLM 决策路由（深挖 vs 常规）→ 输出当日简报；LLM 不生成任何数字，依赖异常时自动降级并写明原因。配套**舆情侧流失风险分层**（高投入不满、高投入不推荐、修辞伪装负向、可行动差评）与分主题干预方向映射，产出可复现的日/周报与离线看板。

---

## 技术栈

| 类别 | 选用 |
|------|------|
| 语言 / 数据 | Python、Pandas（清洗 / 异动分解） |
| 情报 Agent | **纯标准库实现**（两比例 z 检验 / Wilson CI / facts 锁数），零第三方依赖 |
| LLM | DeepSeek API（可选增强：决策与定性；无 Key 时规则引擎 + 模板全链路可跑） |
| 交付 | Markdown 报告、离线 HTML 看板 |
| 质量 | 48 个单元测试（stdlib unittest）+ CI（测试 / 报告重建 / 堆栈与 PII 卫生检查） |
| Agent | [Agent Skills](https://agentskills.io)（`skills/*/SKILL.md`） |

---

## 效果预览

![看板首屏](./docs/preview_dashboard.png)

1. 双击打开 [`05展示页/index.html`](./05展示页/index.html)（离线，无需起服务）  
2. 查看 [`11情报Agent/reports/daily_intel_latest.md`](./11情报Agent/reports/daily_intel_latest.md) 每日情报简报（含异动决策与深挖）  
3. 查看 [`11情报Agent/reports/risk_insight_latest.md`](./11情报Agent/reports/risk_insight_latest.md) 流失风险分层  
4. 查看 [`04日报周报/reports/`](./04日报周报/reports/) 日/周报终稿与 [`09跨渠道AI/reports/`](./09跨渠道AI/reports/) 跨渠道简报  

---

## 快速开始

### 环境要求

- Python 3.10+  
- **情报 Agent 模块零依赖**：clone 后无需安装任何包即可运行（感知层统计与风险分层均为纯标准库）  
- （可选）`DEEPSEEK_API_KEY`：标注 / 跨渠 LLM 综合 / Agent 的 LLM 决策与定性；无 Key 走规则模式  

### 安装与演示刷新

```bash
git clone https://github.com/hccccc01333/arknights-taptap-intel-agent.git
cd arknights-taptap-intel-agent

# 情报 Agent（零依赖，30 秒出简报）
python 11情报Agent/daily_agent.py

# 完整链路（清洗 / 标注 / 看板需要第三方依赖时）
pip install -r requirements.txt
python scripts/refresh_demo.py
```

然后用浏览器打开 `05展示页/index.html`。

---

## 使用说明

```bash
# 清洗
python 02数据/preprocess_reviews.py

# 小样本标注（需 API Key）
python 02数据/annotate_reviews.py --limit 50

# 日/周报
python 04日报周报/build_skill_reports.py

# 跨渠 facts + 综合
python 09跨渠道AI/build_channel_facts.py
python 09跨渠道AI/synthesize_cross_channel.py

# 分析实验室 → 再重建看板
python 10分析实验室/anomaly_diagnosis.py
python 10分析实验室/model_eval.py
python 10分析实验室/event_impact.py
python 05展示页/build_dashboard.py

# 每日情报 Agent（感知→决策→行动→简报；无 Key 走规则模式）
python 11情报Agent/risk_insight.py          # 流失风险分层
python 11情报Agent/anomaly_lite.py          # 零依赖异动感知（近 4 窗检验）
python 11情报Agent/daily_agent.py           # 当日情报简报

# 单元测试（纯标准库，48 个用例）
python -m unittest discover -s 11情报Agent/tests -v
```

一键演示刷新（已有标注与样本时）：

```bash
python scripts/refresh_demo.py
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

---

构建：HCCCC
