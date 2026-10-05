# taptap-hotspot-intel

> 面向游戏社区的 Agent 情报系统：**全网热点追踪 → 情报与素材 → 可落地的增长创意**。
> 以 TapTap 为第一个接入平台。

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-323%20passing-34d399)](#开发)
[![CI](https://img.shields.io/badge/CI-passing-4c1)](.github/workflows/ci.yml)

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

```bash
python -m unittest discover -s L3_trend/tests -p "test_*.py"
python -m unittest discover -s L4_intelligence/tests -p "test_*.py"
python -m unittest discover -s runtime/tests -p "test_*.py"
# 共 7 层 323 个测试，CI 全绿
```

---

## License

[MIT](./LICENSE)
