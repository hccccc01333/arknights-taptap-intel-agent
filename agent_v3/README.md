# TapTap V3 Alpha

当前版本：`3.0.0-alpha.14`。最新运行方式见[项目首页](../README.md)，公开网站及自动成果同步见[部署说明](../docs/V3-GitHub部署与成果同步.md)。下方保留早期阶段的详细记录。

最新实现与非线性业务边界见[研究工具、时效与自动队列](../docs/V3-研究工具与自动队列.md)；下方旧版本段落保留对应时点记录，以最新契约为准。

Alpha 9 新增 `main_agent.py`、`research_child.py` 与 `web_research.py`：业务主 Agent 控制情报、素材和增长创意，按需委派所属研究子 Agent。默认展示有依据的热点解读、游戏情报与可用素材，原文位于核查入口。两条真实周期通过，286 项检查；完整目标及长期无人干预质量仍待验收。[架构、实测与边界](../docs/V3-主Agent与研究子Agent.md)。

Alpha 8 新增 `deepseek.py`：官方 Flash 紧凑 JSON 任务，最终回答/推理隔离、阶段预算、独立推理设置和输出上限。认证清单实际为 V4.1 Flash；生产默认 low、单次总上限 16,384 token。真实非游戏情报任务通过，判断持续观察并保存两项素材，未生成自动创意。[响应契约与实测](../docs/V3-DeepSeek.md)。

全网热点情报与素材系统，主要交付可执行的增长创意。当前版本 `3.0.0-alpha.10`，完成状态见 [架构与验收](../docs/V3-架构与验收.md) 和 [版本记录](../docs/版本记录.md)。

```powershell
python -X utf8 -m uvicorn webapp.main:app --host 127.0.0.1 --port 8202
```

在 `spatial` 目录：

```powershell
$env:VITE_DEV_API_TARGET='http://127.0.0.1:8202'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5182 --strictPort
```

默认 V3；`?version=2` 和 `?version=1` 保留旧入口。本机既有演示账号运营A/观察E，密码 demo。

```powershell
python -X utf8 -m agent_v3 collect
python -X utf8 -m agent_v3 research
python -X utf8 -m agent_v3 research --live
python -X utf8 -m agent_v3 status
python -X utf8 scripts/version_snapshot.py v3
```

`collect` 实采十三个渠道、整理来源素材并安排独立情报任务；`research` 先同步已有采集文件，再处理情报与创意队列；`--live` 在研究前补充实时采集。运行库独立在 `data/v3/`，V2 原文/观察只读迁移，V2 研究结果不冒充 V3 新交付。自动周期在业务设置中配置。新启用 V3 后端时初始化为每 60 分钟运行，已有明确设置会保留；后端关闭时不执行。每周期最多处理两条情报、一个创意任务；AI 退避不影响采集和来源素材整理。

支持本机原有服务与 OpenCode Zen 免费原生任务，安装与验证见 [Zen 接入](../docs/V3-OpenCode-Zen.md)。不把凭证或 CLI 存在当作调用可用。每条独立情报最多 3 轮模型决策、8 次工具、1 次网络补采和 180 秒轮间预算；创意研究最多 8 轮模型决策、16 次工具调用、3 次网络补采和 360 秒轮间预算；单请求另有时限。Zen 在官方 OpenCode 内执行完整任务，最多 10 次原生迭代、两次业务校验尝试，并沿用情报/创意总预算；后台不自动批准终端与文件修改。失败保留原因和此前的发现/素材成果。

Alpha 5 新增 `space_bunny.py`，通过用户提供的官网 API 接入太空兔，系统环境变量只在服务端读取；推理强度与 Ling 独立，记录实际提供商与费用未知状态。两次真实小请求均返回 502，生产模型和 Zen 额度暂停保留，未产生新情报或自动创意。[接入与实测](../docs/V3-Space-Bunny.md)。当前 138 项本地检查通过。

新增 Alpha 4 模块：`enrichment.py` 有限正文补读与重试记录，`task_packets.py` 紧凑来源引用和增长假设契约，`native_growth.py` 可续作的创意规划和文案/脚本制作。补读独立于 AI 退避执行；当前仅支持已有 B 站与游戏媒体详情，其他渠道仍有标题/摘要限制。

Zen 免费额度已确认耗尽时，在业务设置中暂停整组 AI 任务；采集、正文补读与素材整理继续，重启及切换 Zen 模型后仍暂停。确认额度恢复后点击继续，队列与创意规划保留。正文有效期内，同一文章的渠道摘要不会覆盖已读详情。130 项本地检查通过；本轮自动增长创意仍为 0，质量验收需真实模型与运营评审。

原有独立阶段模块：`materials.py` 独立来源素材索引，`work.py` 持久任务与版本/重试，`intelligence.py` 独立情报提炼与机会路由，`pipeline.py` 衔接情报与创意，`model.py` 共用供应商退避。

原有模块：`connectors.py` 分渠道采集，`discovery.py` 增量候选，`tools.py` 情报/素材/创意契约，`store.py` 版本与复用，`engine.py` 研究控制，`service.py` 独立阶段与定时周期，`api.py` 认证接口，`reports.py` 创意和制作交付文档。

来源素材当前包括实采文本/摘要、视频页面引用、文章引用和实采封面图片引用。媒体引用尚未下载，视频尚未转录。AI 与制作素材包括来源摘录、表达模式、文案、脚本、视觉方案与制作清单；图像/视频自动制作尚未实现。严格标题归组不等于语义事件识别。已有一条真实来源交互辅助样例；Zen 新增真实后台独立情报，自动增长创意按实际运行结果单独计数，完整业务质量仍待验收。

验证：

```powershell
python -X utf8 -m unittest agent_v3.tests.test_research_tracking agent_v3.tests.test_public_sources agent_v3.tests.test_space_bunny agent_v3.tests.test_delivery agent_v3.tests.test_zen agent_v3.tests.test_continuity agent_v3.tests.test_v3 agent_v2.tests.test_core agent_v2.tests.test_operations agent_v2.tests.test_model runtime.tests.test_pipeline_health L4_intelligence.tests.test_creative_delivery -q
```

Alpha 6 新增 `public_sources.py`：中新社社会/文娱/生活官方订阅、明确文章区域与贴吧平台话题简介。每轮最多六次详情读取，跨来源持久轮换；真实评论尚未读取。新闻发布不等于热度升温，旧闻/日期不明的文章不进入近期候选。UI 可按栏目领域筛选，正文范围、失败和重试可见。紧凑情报契约升级 intelligence-v3.4，读取元数据进入任务包。[能力与边界](../docs/V3-跨领域来源.md)。

Alpha 7 新增 `research.py` 按缺口规划与工具执行，`discussion.py` 真实公开评论抽样及质量/日期标记，`tracking.py` 稳定身份与可撤回关系，`semantic.py` 本机离线语义召回。当前每轮六次来源工具调用；支持的模型可多执行一条研究计划、最多两对身份判断，分别保留模型与用量记录，暂停时只做规则工具和离线召回。评论一小时后刷新，失败按能力和方法退避；确认关联后共用来源并减少重复研究，撤回会恢复历史队列。紧凑情报契约 `intelligence-v3.5`。当前 189 项检查通过；真实热门评论已读，最新排序仍失败，业务质量尚未通过完整验收。[实际能力与验证](../docs/V3-证据研究与事件跟踪.md)。
