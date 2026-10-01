# Agent 框架落地设计 —— LangGraph / LangChain / harness 各用在哪、用多少

> 状态：**设计稿，待用户拍板后动工**。写于 2026-09-30。
> 决策人：HZZ；agent 只做实现、验证与边界汇报。
> 上游文档：[交付形态设计](./交付形态设计.md)（谁在用） · [Agent-v2-架构设计](./Agent-v2-架构设计.md)（系统怎么分层）

---

## 0. 先修正我上一轮的判断错误

上一轮我说：「本项目缺的不是零件，是交付形态；框架现在上还早，因为控制流是一条直线。」

**这个判断在「必要性」维度成立，在「作品集」维度错了。** 用户的纠正：

> 「我们这个项目是一个 agent 开发项目，不是单纯的一个数据分析。数据分析包括数据采集、收集、清洗、分析、产生、还有决策判断，都是放进这个 agent 里面的。实际上我们是在设计一个 agent 的项目，来证明我能通过 AI 工具 web coding 一个 Agent 项目出来。所以对于 agent 的项目里面用的框架就很……很重要。不能说我缺的不是零件，我当然现在我不缺零件，但是作为一个 Agent 的项目，目标是为了像 TapTap 这种游戏社区而准备的，那后面肯定还有功能迭代。」

**错在哪**：我拿「今天跑不跑得通」当唯一判据。但这个项目的验收标准不是「能跑」，
是「**能证明工程能力**」+「**能支撑下一步迭代**」。

类比：如果目标是证明我会做 Web 开发，那用 React 而不是手写 DOM 是对的——
哪怕手写 DOM 也能跑。**「能跑」不是这个项目的验收标准。**

修正后的判据是三条并行：

| 判据 | 问的问题 | 我上一轮 |
|---|---|---|
| **必要性** | 不用它，今天能不能跑通？ | ✅ 用了 |
| **展示性** | 用它之后，能力是否**可被观察**？ | ❌ 漏了 |
| **可扩展性** | 下一步迭代时，它是否省事？ | ❌ 漏了 |

> **结论修正**：LangGraph 要上，而且要**成体系地上**（4 张图，不是套个壳）。
> 但上法有讲究——见 §2 的「拆分理由」和 §4 的红线。

---

## 1. 环境事实核查（先做，因为差点基于错误前提做设计）

⚠️ 架构设计文档 §0 / §7 写「已装隔离 venv：langgraph + langchain-core + langchain-openai，图构建冒烟通过」。

**我实际核查了一遍**，结论：**装是装了，但文档没写在哪、没写版本、没有可复跑的证据** ——
我一度以为完全没装（全盘搜索 `langgraph` 目录、检查两个项目 venv 的 site-packages 都为空）。

实测结果（2026-09-30）：

| | 实际情况 |
|---|---|
| 位置 | `C:\Users\Hzz\.workbuddy\binaries\python\envs\default`（托管隔离 venv） |
| langgraph | **1.2.12** ✅ |
| langgraph-checkpoint | **4.2.0** ✅ |
| langchain-core | **1.6.5** ✅ |
| langchain-openai | **1.6.6** ✅ |
| `langgraph-checkpoint-sqlite` | ❌ **未装** —— 跨进程持久化要用它，见 §4.3 |
| `langgraph-supervisor` | ❌ 未装 —— 多图编排要用它，见 §2.5 |
| `langchain`（完整包） | ❌ 未装 —— **这是有意的**，见 §5 |

**并且我把验证写成了回归测试**：`11情报Agent/tests/test_langgraph_design.py`（9 个用例）

```
$ <venv>/python -m unittest discover -s 11情报Agent/tests -p "test_langgraph_design.py" -v
test_checkpoint_keeps_trajectory ... ok          ← 轨迹可回放
test_crawl_not_reexecuted_on_resume ... ok       ← 恢复不重跑采集
test_interrupt_pauses_graph ... ok               ← 中断暂停
test_resume_after_approval ... ok                ← 批准后续跑
test_retry_loop_fires_once ... ok                ← 条件边驱动的环
test_allows_locked_numbers_and_paths ... ok      ← 守卫放行已锁数字
test_allows_moderate_string ... ok
test_rejects_long_string ... ok                  ← 守卫拦长文本
test_rejects_raw_text_field_names ... ok         ← 守卫拦原始文本字段
Ran 9 tests in 0.056s — OK
```

**降级纪律**：langgraph 未安装时**整组跳过**（不是失败）。
主套件保持「纯标准库可跑」，框架测试作为可选层——与项目其他地方的可选维度处理一致：

| 环境 | 结果 |
|---|---|
| 无 langgraph | `Ran 158 tests — OK (skipped=5)` |
| 有 langgraph | `Ran 158 tests — OK` |

**这一步的意义**：把「环境已就绪」从**一句声明**变成**一条可复跑的命令 + 9 个测试**。
面试时被问「你真的跑过吗」，直接跑测试给人看。

**待修**：架构文档 §0 / §7 的环境描述要补全位置与版本（本次一并修）。

---

## 2. LangGraph 用了多少：4 张图 / 32 节点 / 7 条件边 / 2 环 / 3 中断点

### 2.1 总览

| 图 | 节点 | 条件边 | 环 | 中断点 | 现状 | 服务的目标 |
|---|---|---|---|---|---|---|
| **G1 巡检图** | 10 | 2 | 1 | 1 | §2 已设计未实现 | 数据运维自动化 |
| **G2 情报图** | 6 | 1 | 0 | 0 | `daily_agent` 已实现（直线） | 情报生产 |
| **G3 追踪图** | 8 | 2 | 0 | 1 | `topic_tracker` 已实现（非图） | **热点追踪** |
| **G4 素材·创意图** | 8 | 2 | 1 | 1 | 未实现 | **情报素材 + 增长创意** |
| **合计** | **32** | **7** | **2** | **3** | | |

### 2.2 为什么拆 4 张图 —— 拆分理由必须真实，不能为凑图而拆

**每张图对应一种不同的控制流形态**，这是拆分的唯一正当理由：

| 图 | 控制流形态 | 为什么不能和其他图合并 |
|---|---|---|
| G1 | **批处理 + 重试环 + 人工闸门** | 触发是「按需」；一次跑完可能跨天（等充值批准） |
| G2 | **直线流程** | 触发是「每天一次」；无环，跑完即出报告 |
| G3 | **事件驱动 + 去重网关** | 触发是「高频心跳」（30–60min）；跑得最频繁，不能和 G2 混 |
| G4 | **迭代生成 + 人工筛选** | 触发是「有选中话题时」；有自评改稿环，是唯一由 LLM 主导的图 |

> ⚠️ **反过来说**：如果哪天发现两张图的触发方式与失败处理完全一致，就该合并。
> 拆图的代价是**调试变复杂**（跨图追踪状态难），所以只在控制流形态确实不同时才拆。

### 2.3 G1 巡检图（10 节点，已有冒烟验证）

```
START → check_freshness → crawl → qc_gate ─┬─(pass)→ clean ──────────┐
                        ↑                  │                          │
                        └──(fail, ≤1次重试)─┤                          ↓
                                           └─(fail, 超限)→ mark_degraded → annotate_gate
                                                                              ↓ (interrupt)
                                                                         [人工批准]
                                                                              ↓
                                                          annotate / skip → perceive_all → refresh → 简报
```

| # | 节点 | 干什么 | 失败处理 |
|---|---|---|---|
| 1 | `check_freshness` | 距上次采集 >24h？ | — |
| 2 | `crawl` | subprocess 爬虫（参数白名单） | `retry_policy` |
| 3 | `qc_gate` | 条数 / app_id 归属 / 空文本率 | → 条件边 |
| 4 | `clean` | 清洗 tool | 标降级 |
| 5 | `mark_degraded` | 重爬超限，标记降级继续 | — |
| 6 | `annotate_gate` | **interrupt**：算增量+额度账，等人批准 | 余额不足跳过 |
| 7 | `annotate` | 标注 tool | 标降级 |
| 8 | `perceive_all` | 批量跑感知模块（risk / anomaly / platform / user_flow） | 逐项降级 |
| 9 | `refresh` | 看板重建 + 跨游戏对比 | 标降级 |
| 10 | `report_summary` | 产巡检摘要 | — |

**LangGraph 特性映射**：
- `add_conditional_edges` ← 质检分流（现有 if/else 手写）
- **环**：`qc_gate → crawl` + `crawl_rounds` 计数（**这是真实的环，不是装饰**）
- `interrupt()` ← 标注闸门（决策 #4 已定要 interrupt）
- `SqliteSaver` ← 中断可能跨天存活（等充值）

### 2.4 G2 情报图（6 节点，现状迁移）

```
START → perceive → decide ─┬─(deep_dive)→ deep_dive ─┐
                           └─(routine)──→ routine ────┴→ brief → archive → END
```

| # | 节点 | 现状对应 |
|---|---|---|
| 1 | `perceive` | `daily_agent.perceive()` |
| 2 | `decide` | `llm_decision()` + `rule_decision()` 合为一个节点（**内部选择用哪条路**，而不是两个分支做同一件事） |
| 3 | `deep_dive` | `act()` 的 deep_dive 分支 |
| 4 | `routine` | `act()` 的 routine 分支 |
| 5 | `brief` | `render_brief()` |
| 6 | `archive` | **新增**：写跨日去重表（同话题不重复推） |

**这张图的价值最低**（本来就是直线），但它是**骨架迁移的第一步**：
先把「一个能跑的东西」搬进图，验证环境、checkpointer、测试怎么改。
**副作用收益**：`decide` 节点顺手解决了架构设计里指出的「LLM 分支与规则分支做同一件事、是重复而非叠加」的问题。

### 2.5 G3 追踪图（8 节点，服务「热点追踪」）

```
START → sample → classify → gate ─┬─(放行)→ ferment_judge → dedupe ─┬─(未推过)→ push
                                  └─(不放行)→ END                    └─(推过)→ END
                                                                        ↓ (interrupt)
                                                                   [员工动作]
                                                                        ↓
                                                                  write_action_log → END
```

| # | 节点 | 干什么 |
|---|---|---|
| 1 | `sample` | 从 facts 采样（`topic_tracker.collect_samples`） |
| 2 | `classify` | 状态迁移判定（`classify_transition`） |
| 3 | `gate` | **推送门槛**：只放行升温/爆发（见交付形态设计 §3.2） |
| 4 | `ferment_judge` | LLM 可发酵度判断（可降级为规则） |
| 5 | `dedupe` | 查已推送表：同话题同状态只推一次 |
| 6 | `push` | 企微/飞书机器人 |
| 7 | `await_action` | **interrupt**：等「我要跟进 / 忽略」 |
| 8 | `write_action_log` | 写 `action_log` 表（闭环校准的原料） |

**这张图是「事件驱动」的载体** —— 它可以被 30–60min 的心跳触发，
而 G2 是每天一次。**两种触发频率共用同一套 checkpointer 状态**，这正是 §5 论证的框架价值。

### 2.6 G4 素材·创意图（8 节点，补项目目标缺口②③）

```
START → select_topic → extract_material → retrieve_similar → generate
                                                              ↓
                        publish ← human_select ← ──── self_critique
                                                          ↓ (不达标, ≤N 轮)
                                                       revise ──┐
                                                          ↑     │
                                                          └─────┘
```

| # | 节点 | 干什么 | 角色 |
|---|---|---|---|
| 1 | `select_topic` | 从升温/爆发话题里挑选题 | 决策 |
| 2 | `extract_material` | **素材抽取**（原帖引用 / 热评金句 / 梗 / 二创角度） | LLM |
| 3 | `retrieve_similar` | **RAG**：检索历史素材库（相似素材、已用过的梗、反讽例句） | 检索 |
| 4 | `generate` | 增长创意生成（Flash + **thinking 开** + `reasoning_effort=high`） | LLM |
| 5 | `self_critique` | 自评：创意是否贴合社区、是否可执行、有无事实错误 | LLM |
| 6 | `revise` | 按自评意见改稿 | LLM |
| 7 | `human_select` | **interrupt**：员工挑选题 | 人 |
| 8 | `publish` | 落盘 / 推送 | 行动 |

**这张图是项目目标的核心**（「情报与素材系统」+「产出增长创意」），
也是 LangGraph 用得最实的一张：**自评改稿环是真实的迭代**，不是 if/else 能表达的。

### 2.7 多图怎么串起来：supervisor

4 张图不是各跑各的，需要一个编排层：

```
                    ┌───────────────┐
                    │  supervisor   │  ← 判断"现在该跑哪张图"
                    └───────┬───────┘
        ┌───────────┬───────┴───────┬───────────┐
        ↓           ↓               ↓           ↓
    G1 巡检      G2 情报         G3 追踪      G4 创意
   （按需）     （每天一次）    （心跳 30min）  （有选题时）
```

**实现方式**（二选一，待定）：
- **A**：`langgraph-supervisor` 库（未装，需装）
- **B**：自己写一个 supervisor 图，用 `add_node` 挂子图 + 条件边路由

**我的建议：先 B**。理由：supervisor 的决策逻辑很简单（看时间 + 看有没有待处理事项），
自己写 20 行比引一个库更透明，也不会在面试时被问「这个库内部怎么调度」答不上来。

---

## 3. 「怎么写」：最小可跑的骨架

以 G3 追踪图的片段为例（**这不是伪代码，是本项目实际会写的形态**）：

```python
from typing import Annotated, TypedDict
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
import operator

class TrackState(TypedDict):
    # ✅ 已锁数字（来自 facts，代码算好的）
    samples: list[dict]          # 每个元素是 {topic_key, title, metric, metric_kind}
    transitions: list[dict]      # 状态迁移记录
    # ✅ 路径引用
    db_path: str
    # ✅ 控制流
    pushed_keys: list[str]
    action: str | None
    # ✅ 轨迹（归并式）
    log: Annotated[list[str], operator.add]

def node_sample(state: TrackState) -> dict:
    # 调 topic_tracker 的既有函数，不重写逻辑
    import topic_tracker as tt
    return {"samples": tt.collect_samples(), "log": ["[sample] 采到 N 条"]}

def node_gate(state: TrackState) -> dict:
    """推送门槛：只放行升温/爆发。不合格的在这里就断了，不进 LLM。"""
    hot = [t for t in state["transitions"] if t["state"] in ("升温", "爆发")]
    # 用 Command 直接声明下一步，比在 State 里塞一个字符串再读出来更清楚
    return Command(
        update={"log": [f"[gate] 放行 {len(hot)} 条"]},
        goto="ferment_judge" if hot else END,
    )

def node_await_action(state: TrackState) -> dict:
    """等员工动作。interrupt 会暂停图并存进 checkpointer。"""
    decision = interrupt({"ask": "这条要不要跟进？", "topics": state["transitions"]})
    return {"action": decision, "log": [f"[action] 员工={decision}"]}

g = StateGraph(TrackState)
g.add_node("sample", node_sample)
g.add_node("gate", node_gate)
g.add_node("ferment_judge", node_ferment_judge)
g.add_node("dedupe", node_dedupe)
g.add_node("push", node_push)
g.add_node("await_action", node_await_action)
g.add_node("write_action_log", node_write_action_log)

g.add_edge(START, "sample")
g.add_edge("sample", "classify")
g.add_edge("classify", "gate")
# gate 用 Command(goto=...) 动态路由，无需 add_conditional_edges
g.add_edge("ferment_judge", "dedupe")
g.add_edge("dedupe", "push")
g.add_edge("push", "await_action")
g.add_edge("await_action", "write_action_log")
g.add_edge("write_action_log", END)

app = g.compile(checkpointer=SqliteSaver.from_conn_string("state/agent_checkpoints.sqlite3"))
```

**两个写法要点**（`_smoke_langgraph.py` 已验证）：

1. **`Command(goto=...)` vs `add_conditional_edges`**：
   前者把路由写在节点里（节点自己知道下一步去哪），后者把路由集中在图上。
   分支简单时用条件边更清楚；分支依赖节点内部算出来的复杂结果时用 `Command`。
   **本项目两种都会出现**，这不是风格不统一，是各取所长。

2. **`Annotated[list, operator.add]`**：轨迹字段用归并 reducer，
   这样 checkpointer 恢复时能看到完整历史（冒烟第 4 步验证过：历史节点数 = 9）。

---

## 4. 三条必须在框架层守住的红线

### 4.1 State 不能放原始文本（否则 facts 锁数在框架层失效）

LangGraph 的 State 是「流经所有节点的共享字典」。它**天然诱使人**把原始评论塞进去，
因为那样任一节点取用最方便。**一旦这么做，facts 锁数纪律就崩了**——
LLM 节点能从 State 直接读到原始文本，它就有可能编数字，而架构上再也拦不住。

**硬规则**：

> State 里只放两类东西：① **已锁的汇总数字**（facts）② **产物路径**。
> **原始文本一律不进 State**，需要读的节点自己去读文件。

**并加运行时守卫**（冒烟已验证可拦截）：

```python
def _assert_state_clean(state: dict) -> None:
    for k, v in state.items():
        if isinstance(v, str) and len(v) > 200:
            raise ValueError(f"State 禁止存放长文本（{k}）——违反 facts 锁数纪律")
        if k.startswith("raw_") or k.endswith("_text") or k.endswith("_content"):
            raise ValueError(f"State 禁止出现原始文本字段：{k}")
```

> **这条要写进 code review 清单**。框架不会帮你守，只有自己守。

### 4.2 降级必须是显式的，不能交给框架的默认行为

框架的默认失败行为是**抛异常**或**静默重试**，不是「降级并标注」。
本项目每个可选维度都带 `available` / `reason`（`load_platform` / `load_topic_state` 都是），
这套纪律**要原样搬进节点返回值**，不能因为套了框架就退化成抛异常。

**具体做法**：节点内部 try/except 自己的业务异常 → 返回 `{"xxx": {"available": False, "reason": ...}}`；
只把**基础设施异常**（网络、磁盘）留给框架的 `retry_policy`。

### 4.3 interrupt 的持久化必须落到 SQLite，不能只放内存

`InMemorySaver` 在进程结束时丢状态。而本项目的两个中断点都有**跨天**特性：

- G1 标注闸门：等充值，可能等几天
- G3/G4 员工动作：员工可能第二天才点

**所以必须装 `langgraph-checkpoint-sqlite`**（当前 ❌ 未装），用 `SqliteSaver`：
文件放 `data/state/`（已在 `.gitignore`，运行时状态不入库）。

⚠️ **这条不做，「人工确认点」就是假的人确认点** —— 进程一重启就丢，员工点了没反应。

---

## 5. 零件箱（LangChain）用不用：部分用，且有明确的「不用清单」

用户问：「应用零件箱是不是也要用呢？」——**要，但是部分用，而且拒绝的部分要有理由。**

### 5.1 用（4 件，都是替掉手写样板）

| 组件 | 用在哪 | 替掉了什么 |
|---|---|---|
| `langchain-openai`（ChatOpenAI） | 所有 LLM 调用 | **`daily_agent.llm_decision()` 里 40 行手写 urllib**：组 body、设 header、超时、解析 choices、异常静默返回 None |
| `with_structured_output(schema)` | 决策 / 可发酵度 / 素材抽取 | **手写 `json.loads` + 字段校验**（现在失败就静默返回 None，看不出错在哪） |
| 检索器（向量/BM25） | G4 `retrieve_similar` | 素材层需要「找相似历史素材」，这部分从零写不划算 |
| `RecursiveCharacterTextSplitter` | G4 素材抽取 | 原帖+评论可能超长，需要切分 |

**第 1、2 件是立刻就能换的**——`langchain-openai` 已在环境里，
DeepSeek 兼容 OpenAI 协议，改造成本低，且**换掉的是「静默失败」这个真问题**。

### 5.2 不用（4 件，每件都有具体冲突）

| 组件 | 为什么不用 |
|---|---|
| **LCEL 全链路 `\|` 串联** | 会把控制流写死在表达式里，与 LangGraph 的图**直接冲突**。本项目需要条件分支与中断，串不出来 |
| **`langchain.agents`（老 Agent 类）** | 它自己管 ReAct 循环，与 LangGraph 的图**重复**。两套循环会打架，debug 时不知道是谁在决定下一步 |
| **LangChain Memory 模块** | ❌ **与 facts 锁数纪律冲突**。它的记忆是自由文本对话历史；本项目的跨天记忆是**结构化状态机**（`topic_state` 表 + SQLite）。用它会退化回「把昨天报告丢给 LLM 让它对比」——那正是我们明确否掉的方案 |
| **各类 Document Loader** | 数据源是自己的爬虫（已产出结构化 CSV/JSON），没有需要 loader 的场景 |

### 5.3 面试口径（会被问「为什么用 / 不用 LangChain」）

> 「我把它当**零件箱**用，不当骨架用。
> **用**的是三件替我掉样板代码的：LLM 客户端（DeepSeek 兼容 OpenAI 协议，直接换掉了 40 行手写 urllib）、
> 结构化输出（用 schema 约束，比手写 json 校验可靠）、检索器（素材层找相似素材）。
> **不用**的是 LCEL 全链路和内置 Memory：前者会把控制流写死在表达式里，
> 而我的控制流要用图管条件分支和人工中断；后者会和我的 facts 锁数纪律冲突——
> 我的跨天记忆是结构化状态机，不是自由文本对话历史。」

**这个回答的结构**：不是「我用了/没用」，而是「**我按判据决定用哪部分**」。

---

## 6. harness：哪些必须自己写，哪些可以交给框架

用户已经理解 harness = agent 外面那层控制代码。要点是**这层不能整体外包**，
因为其中一部分就是这个项目的技术主张。

| harness 组件 | 交给谁 | 理由 |
|---|---|---|
| **facts 锁数** | 🙋 **自己写** | 框架没有这个概念；它只会让你更方便地把原始数据喂给 LLM |
| **显式降级** | 🙋 **自己写** | 框架默认抛异常或静默重试，不是「降级并标注原因」 |
| **PII 三域 + 加盐哈希 + 聚合守卫** | 🙋 **自己写** | 框架不管合规 |
| **话题状态机语义** | 🙋 **自己写** | 框架提供 checkpointer（存什么），但**状态语义**（什么算升温）是自己的 |
| **事实源分层** | 🙋 **自己写** | 同一份 facts 渲染三种视角，这是业务判断 |
| 重试 / 超时 / 退避 | 🤖 交给框架 | `retry_policy` 是机械活 |
| 工具注册 / 参数 schema 校验 | 🤖 交给框架 | ToolNode + Pydantic，别自己造 |
| 图状态持久化 | 🤖 交给框架 | checkpointer |
| 轨迹落盘可回放 | 🤖 交给框架 | checkpointer 的历史 + `get_state()` |

> **一句话分工**：**框架管机械，你管纪律。**
> 把纪律也交给框架的那天，这个项目就没有可讲的东西了。

**现状盘点**（诚实版）：

| | 状态 |
|---|---|
| facts 锁数 | ✅ 已实现（`daily_agent` 的 facts 组装 + 简报只引用 facts） |
| 显式降级 | ✅ 已实现（`load_platform` / `load_topic_state` 都带 `available`/`reason`） |
| PII 三域 | ✅ 已实现（加盐哈希 + `assert_aggregate_only` 写前拦截 + CI guard） |
| 话题状态机 | ✅ 已实现（`topic_tracker.py`，30 测试） |
| 重试/超时 | 🟡 部分（`timeout=300` 硬编码，无退避） |
| 工具注册 | 🟡 部分（subprocess 白名单，无 schema 校验） |
| 轨迹回放 | 🟡 部分（`agent_run.log` 只记失败，不记全轨迹） |

**后三行 🟡 就是交给框架的部分。**

---

## 7. 迁移路径（三阶段，每阶段可独立交付）

| 阶段 | 做什么 | 用上哪些框架特性 | 前置 |
|---|---|---|---|
| **P1** | **G2 情报图**：把 `daily_agent` 迁进图；LLM 调用换 `ChatOpenAI` + 结构化输出；接 `SqliteSaver` | StateGraph · checkpointer · 结构化输出 | 装 `langgraph-checkpoint-sqlite` |
| **P2** | **G1 巡检图**：第一次用上**环 + interrupt**。这是「Agent 工程」立得住的关键一步 | 条件边 · 环 · interrupt · retry_policy | P1（骨架复用） |
| **P3** | **G3 追踪图 + G4 创意图**：服务项目目标的两个缺口 | supervisor 编排 · RAG 检索 · 自评环 | **「素材」定义（交付形态设计 §7 未决 #3）** |

**为什么 P1 先做**：它是**风险最低的迁移**（现有逻辑不变，只是换个壳），
但能一次性验证环境 + checkpointer + 测试怎么改。**1 天量级。**

**为什么 P2 紧接着**：环与 interrupt 是「框架真的在干活」的证据。
做完 P1+P2，「这是个 LangGraph 项目」就不再是一句话，而是 16 个节点、2 个条件边、1 个环、1 个中断点的实际结构。

**P3 为什么最后**：它依赖用户回答「素材具体指什么」（唯一卡住的问题），
而且它是工程量最大的一步。**但它是项目目标的正面命中**——缺口 ②③ 都在这里。

---

## 8. 未决（需要用户拍板）

| # | 问题 | 我的建议 |
|---|---|---|
| 1 | 是否按本设计**成体系上 LangGraph**（4 图 32 节点 + supervisor） | ✅ 是，但按 P1→P2→P3 分期，不一次性铺 |
| 2 | supervisor 自己写还是用 `langgraph-supervisor` | 自己写（20 行更透明） |
| 3 | P1 是否现在就动工（1 天量级，不改行为） | 建议是 |
| 4 | **素材层的「素材」具体指什么** | ⚠️ **仍需用户给样例**——这是 P3 的硬前置 |
| 5 | 话题语义归一（同义不同形会分裂成两条线） | 原计划等 DeepSeek 充值；也可先做规则版（同义词典） |

---

## 附：本文与既有文档的关系

| 问题 | 去哪 |
|---|---|
| 谁在用、怎么到手、动作往哪回写 | [交付形态设计](./交付形态设计.md) |
| 系统怎么分层、感知/决策/行动怎么切 | [Agent-v2-架构设计](./Agent-v2-架构设计.md) §1–§2 |
| **框架用哪些、用多少、怎么写** | **本文** |
| 话题追踪机制（状态机） | [Agent-v2-架构设计](./Agent-v2-架构设计.md) §1.3 |
