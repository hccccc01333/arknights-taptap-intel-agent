# V3 Alpha 14 · 自动化情报、素材与增长创意

[打开网站](https://hccccc01333.github.io/taptap-hotspot-intel/) · [GitHub部署与成果同步](docs/V3-GitHub部署与成果同步.md) · [版本记录](docs/版本记录.md)

源码推送自动部署页面；本地后台自动同步真实成果，打开网站即可浏览五类内容。后台仍在本地运行，电脑关闭后保留最后一次成果。公开页不包含原始采集库、账号信息、密钥或模型推理过程。

## 当前开发版本：V3 Alpha 13

当前版本：`3.0.0-alpha.13`。本轮实现[自动交付、分类浏览与子Agent补查闭环](docs/V3-自动交付与补查闭环.md)，正常界面只保留五个浏览入口，后台独立推进初筛和深度任务；核查TapTap产品边界，素材须有可编辑使用稿，执行效果反馈暂不建设。上轮规则见[正负面、评论补采与游戏入口](docs/V3-正负面与评论补采.md)；下方旧版本段落保留对应时点记录，以最新契约为准。

Alpha 9 实现[业务主 Agent 与所属研究子 Agent](docs/V3-主Agent与研究子Agent.md)：主 Agent 筛选候选并委派研究，子 Agent 返回有来源的热点解读，主 Agent 整理游戏情报、可用素材并决定增长创意。热点默认展示解读，原文可核查；两条真实后台周期交付情报与素材，均判断观察，完整自动增长创意仍为零。通用检索、持续补查和业务质量继续完善。

Alpha 8 接入[官方 DeepSeek Flash](docs/V3-DeepSeek.md)：推理强度与输出预算独立配置，最终 JSON 与推理字段分开处理。真实文旅情报任务已通过，未确认增长机会时持续观察；自动增长创意仍为零，整体交付质量继续验收。

围绕“全网热点追踪 → AI 情报与素材积累 → TapTap 实际增长创意”进行架构升级。默认前端入口为 V3，V1 / V2 保留；实际能力、验证状态与边界见 [V3 架构与验收](docs/V3-架构与验收.md)、[版本记录](docs/版本记录.md) 和 [启动说明](agent_v3/README.md)。

新增[跨领域来源与正文积累](docs/V3-跨领域来源.md)：中新社社会、文娱、生活订阅和贴吧公开话题简介，详情按来源轮换并标明实际范围。已实现十三个渠道实采、独立来源素材整理、持久情报与创意任务、供应商退避和每小时自动周期；情报与素材可独立积累，再为明确机会制作创意。[OpenCode Zen 免费原生任务与推理强度设置](docs/V3-OpenCode-Zen.md) 已保存第一条后台独立 AI 情报及表达素材。新增 [太空兔官网 API 与五档推理设置](docs/V3-Space-Bunny.md)，两次真实调用均返回 502，未计为新成果。有一条旧的真实来源交互辅助样例。自动增长创意、发现质量与素材制作仍待验收。版本为 `3.0.0-alpha.7`，不表示业务目标已经全部完成。

Alpha 7 新增[证据研究与事件跟踪](docs/V3-证据研究与事件跟踪.md)：按缺口安排正文、背景检索和讨论抽样；稳定来源身份、离线跨领域语义召回、原文关系核对及可撤回归并接入持续流程。已取得真实 TapTap / B 站热门评论，最新排序和完整语境仍有失败，AI 额度暂停保留。

# taptap-hotspot-intel

> 面向游戏社区的 Agent 情报系统：**全网热点追踪 → 情报与素材 → 可落地的增长创意**。
> 以 TapTap 为第一个接入平台。

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![V2](https://img.shields.io/badge/V2_checks-53%20local-34d399)](docs/V2-验收记录.md)
[旧 CI 配置](.github/workflows/ci.yml)

## V2 接手重构（2026-10-07）

V2 阶段默认前端曾切换到独立 V2 工作台，旧界面从 `?version=1` 进入。V2 使用真实 API 和 `data/v2/agent.sqlite3`，保留 V1 源码快照与原始数据。

- [完整产品与系统设计](docs/V2-产品与系统设计.md)：从增长结果倒推采集、Agent 研究、事件、创意、素材、反馈与日报周报。
- [V2 启动和运行说明](agent_v2/README.md)：后台、前端、凭证配置、模型选择、命令行和接口。
- [实测与验收记录](docs/V2-验收记录.md)：53 项测试、真实六类渠道采集、浏览器验证，以及模型调用与质量基线缺口。

当前完成完整设计与可运行首版，**整体验收未完成**。模型服务的真实研究交付尚未通过，不能将采集成功或界面运行当作分析准确与业务增长的证明。

以下为 V1 的历史产品说明与实现，不代表 V2 已实现和验收了其中所有能力。

---

## 为什么要它

热点不是等出来的，是**追出来的**——它先在某个社区发酵，然后跨平台扩散，最后才进入大众视野。等到它上了热搜，通常已经过了窗口期。

多数团队的做法是人工刷榜：运营每天早中晚各看一次热榜，用肉眼判断"这个能不能借"。问题是：

- **看的是存量榜** —— 词上榜时讨论量通常已经过了峰值，看到时窗口已经关了一半
- **噪音远大于信号** —— 全网热搜里 95% 和游戏无关，无法直接行动
- **判断不可复用** —— 换个话题就要重新想一遍，很容易得出"正确但没法执行"的结论

这个系统把这三件事交给 Agent：

```
全网采集  →  形成中判定  →  相关性匹配  →  创意生成
（多平台）    （只看正在出现）  （和 TapTap 有关）  （给出可执行动作）
```

---

## 它和"热榜聚合"的区别

| | 热榜聚合 | 本系统 |
|---|---|---|
| **输入** | 一个热榜列表 | 全网多渠道 + 站内社区 + 跨轮时间序列 |
| **输出** | 「话题 X 排名第 3」 | 「X 值得做：位置在哪、几步做完、文案怎么写、素材要什么、多久内要做、有什么风险」 |
| **判断者** | 人 | Agent（规则召回 + LLM 判定 + LLM 生成） |
| **时效** | 榜单快照 | 只报 `forming`（正在形成），`peaking`/`fading` 的不报 |
| **可执行性** | 需要人再想一遍 | 直接是执行清单 |

---

## 产出长什么样

一次运行会产出**结构化创意**，不是一段文字：

```jsonc
{
  "name": "鸣潮丹瑾支线悬天全配音补全-热点专题页",
  "creative_type": "content",
  "audience": "找攻略的核心玩家、剧情党玩家",

  "execution": {
    "where": "TapTap 鸣潮游戏页顶部 Banner 位 + 发现页专题池",
    "steps": ["下载 B 站原视频并截取关键剧情时间轴", "做 1 张长图：官方原声 vs 补全版对照表", "…"],
    "owner": "运营主导（文案/发布/置顶），设计配合",
    "cost": "low",
    "lead_time_hours": 4,
    "window_missed": false          // 热点年龄 vs 提前期，赶不上会标 true
  },

  "copy": {
    "headline": "丹瑾支线『悬天』原来藏着这么多没听过的台词？UP 主补全版配音上线",
    "push_title": "…"               // Push 类型自动填
  },

  "assets": [                        // 素材清单
    {"type": "image", "desc": "剧情时间轴长图", "source": "B 站原视频截帧"},
    {"type": "video", "desc": "原视频授权片段", "source": "UP 主授权"}
  ],

  "primary_metric": "engagement_rate",
  "kpi_target": "6.91%",
  "kpi_basis": "互动率的社区 P75（n=323 篇社区帖）",
  "risks": [                         // 风险不是"出事了"，是"这样做可能出事"
    {"level": "medium", "warning": "UP 主授权风险：未获二创授权或商用可能引发版权投诉"}
  ],
  "evidence": ["热点:【鸣潮】丹瑾支线悬天全配音补全版本", "站内:鸣潮游戏专区-动态广场"]
}
```

关键点：**`window_missed`、`kpi_basis`、`risks`、`evidence` 都是硬约束**，不是装饰。
赶不上窗口会明说"KPI 是参照值不是承诺"，风险没想过就直说，而不是编一个。

---

## 核心设计取舍

### 1. 只报"正在形成"的热点

热榜是**存量榜**。系统以采集时刻为锚点算发帖速率，把榜上词分四档：

| 阶段 | 判据 | 报不报 |
|---|---|---|
| `forming` | 刚进榜 / 热度在涨 / 排名在升 | ✅ **只报这档** |
| `rising` | 在榜 > 90 分钟且仍在涨 | 供参考 |
| `peaking` | 在榜 > 60 分钟且热度横盘 | ❌ 爆发已过 |
| `fading` | 热度下滑 | ❌ |

### 2. 相关性判定用三层，不是单点阈值

```
① 规则词表（117 词）→ 召回优先，宁多不漏
② bge 语义召回 → 救回词表漏网的（崩铁/蛋仔/lol/绝区零…）
③ LLM 三档判定 → related 进看板 / adjacent 背景参考 / irrelevant 丢弃
```

实测：253 条外部热点 → 4 个跨平台事件 → 7 条 related。

### 3. 创意生成按类型逐条来

一次问模型"给 5 条"会把 token 全花在第 1 条上。改成**一次只做一种类型**（专题 / 社区 / Push / 社媒…），每条都有完整预算，且类型不同天然动作不同。

### 4. 时效闸门前置

赶不上的创意**压根不生成** —— Push 只有 2 小时窗口，厂商合作要 72 小时。对一个已经火了三天的热点建议"厂商合作"是废话。

### 5. Agent 自主调度（V2）

Agent 每轮读完数据自己决定下一轮怎么采，规则可解释：

```
信号：15 个帖评论增长≥10  →  评论预算 150→225
信号：覆盖率 11% < 30%     →  评论页数 10→15
```

每轮从**基准值**出发而不是上轮结果叠加 —— 否则会 120→270→405 复合膨胀。

---

## 系统边界

**本系统只负责产出**：情报报告、增长创意、素材清单。

运营跟进、投放执行、效果验证**在外部系统完成** —— 这个仓库不做审批流、不做实验引擎、不做报表。

---

## 快速开始

```bash
git clone https://github.com/hccccc01333/taptap-hotspot-intel.git
cd taptap-hotspot-intel
pip install -r requirements.txt
```

### 采集

```bash
# 单轮
python L1_data_source/collectors/baidu/crawl_baidu_hot.py --tabs realtime,game
python L1_data_source/collectors/weibo/crawl_weibo_hot.py          # 需 WEIBO_COOKIE
python L1_data_source/collectors/taptap/crawl_taptap_community.py --game arknights

# 常驻（推荐，每 10~15 分钟一轮）
python -u L1_data_source/collectors/baidu/crawl_baidu_hot.py --watch --interval 600 &
python -u L1_data_source/collectors/weibo/crawl_weibo_hot.py  --watch --interval 600 &
python -u L1_data_source/collectors/bilibili/crawl_bili_game_hot.py --watch --interval 900 &
```

### 入库 → 分析 → 产出

```bash
python L1_data_source/pipeline.py --once --all          # 入库
python L2_signal/processing/pipeline.py --run           # 清洗

python L3_trend/forming_report.py                       # 正在形成的热点
python L3_trend/hotspot_filter.py                       # 相关性三档
python L3_trend/event_resolver.py                       # 跨平台事件合并
python L4_intelligence/intelligence/hotspot_to_creative.py   # 创意生成

python L3_trend/strategy.py                             # Agent 决定下轮参数
python L3_trend/feedback.py                             # 评估上轮效果
```

### Web 界面

```bash
python -m uvicorn webapp.main:app --port 8200     # 演示密码 demo
```

三个页面：**社区报告**（热点情报）· **增长创意**（可执行动作）· **数据源**（采集状态 + 参数面板）

---

## 渠道接入状态

| 渠道 | 拿什么 | 状态 |
|---|---|---|
| 百度热搜 · 综合 | 搜索意图 | ✅ 51 条/轮 |
| **百度热搜 · 游戏** | **游戏专属榜（纯度最高）** | ✅ 30 条/轮 |
| 微博热搜 | 破圈话题 + 在榜「新」标记 | ✅ 49 条/轮 |
| B站游戏区 + 热门 | 创作者生产 + 播放增速 | ✅ 126 条/轮 |
| TapTap S1 | 2357 个游戏社区索引 | ✅ |
| TapTap S2/S3 | 帖子流 + 评论（thread 监测） | ✅ |
| 抖音话题 / 贴吧 / 知乎 | — | ❌ 登录态 / 风控 |

站内数据（TapTap）用于**验证**（热点传导到社区了吗）和**语境补充**，
不作为创意生成的前置条件 —— 一个鸣潮的热点即使站内还没讨论，也照样能出创意。

---

## 已知边界

- `topic_count`（社区累计帖数）有缓存延迟 → 只能测**小时级**增长，不是分钟级
- 推荐流有马太效应（热帖反复推、冷帖被推走）→ 采集样本有偏
- 百度热搜是**搜索意图**，不等于讨论热度
- 微博无公开趋势指数 → 用跨轮变化率代替
- 免费模型结构化输出有条数上限（8 条/次），已做自适应拆分

---

## 项目结构

```
L1_data_source/     采集：各平台爬虫 + 源注册表 + 事件总线
L2_signal/          加工：清洗、去重、质量闸门
L3_trend/           趋势
  ├─ forming.py         形成中判定
  ├─ hotspot_filter.py  三层相关性过滤
  ├─ event_resolver.py  跨平台事件合并
  ├─ event_graph.py     事件图
  ├─ game_tags.py       游戏标签库（2357 社区寻址）
  ├─ thread_feed.py     评论响应式服务
  ├─ kpi_baseline.py    KPI 基线
  ├─ strategy.py        Agent 采集策略（V2）
  └─ feedback.py        策略效果回溯
L4_intelligence/    情报：LLM 报告 + 增长创意
webapp/             Web：社区报告 / 增长创意 / 数据源
docs/               架构设计
```

设计文档：[AI 情报系统架构](docs/AI情报系统架构.md) · [热点检测架构](docs/热点检测架构设计.md)

---

## 开发

### 接手重构与数据时效检查（2026-10-07）

当前重构目标与验收顺序见 [Agent 重构实施方案](docs/Agent重构实施方案.md)。文档中的已实现与待实施范围分别列明；下方既有测试徽章不作为业务效果证明。

只读检查现存采集入库、清洗、趋势、情报分析、外部快照和社区报告的数据时间：

```bash
python -X utf8 runtime/pipeline_health.py
python -X utf8 runtime/pipeline_health.py --json --max-age-minutes 60
python -X utf8 -m unittest runtime.tests.test_pipeline_health -v
```

Web 的 `/api/pipeline-health` 需要登录；社区报告及 Universe 接口也返回时效状态。时效阈值尚未按各渠道校准，数据水位不等于任务最近执行时间。重新生成报告或刷新页面不代表上游数据更新。

```bash
python -m unittest discover -s L3_trend/tests -p "test_*.py"
python -m unittest discover -s L4_intelligence/tests -p "test_*.py"
python -m unittest discover -s runtime/tests -p "test_*.py"
# 共 7 层 323 个测试，CI 全绿
```

---

## License

[MIT](./LICENSE)
