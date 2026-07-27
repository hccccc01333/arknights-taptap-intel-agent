# 明日方舟 · TapTap 舆情日周报分析平台

以 TapTap 评价为主链，完成 **采集 → 清洗 → LLM 结构化标注 → 日/周报 → 离线看板**；并叠加 B 站 / 抖音 / 微博对照与跨渠道综合。

| 项 | 内容 |
|----|------|
| 主链 | TapTap《明日方舟》评价（app_id=70253） |
| 标注 | v1.4：字面 / 意图 / 主情绪 / 修辞分通道 |
| 跨渠 | facts 锁数后由 LLM 写定性综合 |
| Skills | [`skills/`](./skills/README.md)（[Agent Skills](https://agentskills.io) 格式） |
| 构建 | HCCCC |

> 社交三渠为限量对照样本；降级语料会在 facts / 看板标明，不作全网 KPI。

## 解决什么问题

游戏运营需要从海量玩家评价里快速看到：情绪结构、主题负向主簇、可行动吐槽，以及讽刺 / 高级黑等不易被单字段情绪抓住的话术。本仓库把非结构化评论变成可聚合指标，并输出可复现的日/周报。

## 技术栈

Python · Pandas · DeepSeek API（结构化 JSON）· 离线 HTML 看板 · Markdown 报告 · Agent Skills

## 主要能力

1. TapTap 评价采集与清洗（字段契约、断点续跑）  
2. LLM 标注 v1.4（修辞两段式 + 主题 / 可行动）  
3. 日/周报四问法终稿与薄样本纪律  
4. 四渠对照矩阵 + facts 锁数跨渠道简报  
5. 分析实验室：异动检验、模型评估、事件前后窗  
6. 可复用 Skills：爬虫 / 标注 / 报告 / 跨渠  

## 架构

```text
01 爬虫 → 02 清洗 → 03 标注 v1.4 → 04 日/周报
                              ↘ 05 看板
                              ↘ 09 跨渠道 AI ← 06/07/08 对照
                              ↘ 10 分析实验室
skills/  提供可加载的 Agent Skill 规格
```

## 本地运行

```bash
pip install -r requirements.txt

# 刷新报告、跨渠 facts、看板（演示数据可无密钥）
python scripts/refresh_demo.py

# 打开看板
# 05展示页/index.html
```

可选（需自行配置环境变量，勿提交密钥）：

```bash
# TapTap 采集：见 01爬虫/config.example.env
# 标注：DEEPSEEK_API_KEY
python 02数据/annotate_reviews.py --limit 50
python 09跨渠道AI/synthesize_cross_channel.py
```

## 目录

| 路径 | 说明 |
|------|------|
| `01爬虫/` | TapTap 采集 |
| `02数据/` | 清洗与标注脚本 |
| `03标注结果/` | `annotations_v1_4.csv`、QC、修辞口径 |
| `04日报周报/` | 日/周报生成 |
| `05展示页/` | 离线看板 |
| `06`–`08` | B站 / 抖音 / 微博对照 |
| `09跨渠道AI/` | facts + 综合简报 |
| `10分析实验室/` | 异动 / 评估 / 事件 |
| `skills/` | Agent Skills（唯一 Skill 目录） |
| `docs/` | 方法与构建说明 |

## Skills

| Skill | 作用 |
|-------|------|
| `arknights-taptap-crawl` | 爬虫字段契约 |
| `arknights-llm-annotate-v14` | 标注两段式 v1.4 |
| `arknights-taptap-yuqing-report` | 日/周报写法 |
| `arknights-cross-channel-facts` | 跨渠 facts 锁数 |

```bash
python scripts/sync_agent_skills.py   # 可选：同步到本地 Agent 目录
```

## 入口

- 看板：[`05展示页/index.html`](./05展示页/index.html)  
- 构建说明：[`docs/构建说明.md`](./docs/构建说明.md)  
- 方法论：[`docs/方法论.md`](./docs/方法论.md)  
- 报告示例：[`04日报周报/reports/`](./04日报周报/reports/)  

---

构建：HCCCC
