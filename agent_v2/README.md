# TapTap 热点情报与增长创意 Agent · V2

目标：用可复查的真实来源研究热点，交付运营可以评估和执行的 TapTap 增长提案。完整目标与验收门槛见 [产品与系统设计](../docs/V2-产品与系统设计.md)，实测与缺口见 [验收记录](../docs/V2-验收记录.md)。

## 启动

从项目根目录启动后端；另一个终端启动前端。以下端口用于和正在运行的 V1 区分。

```powershell
python -X utf8 -m uvicorn webapp.main:app --host 127.0.0.1 --port 8202
```

```powershell
cd spatial
$env:VITE_DEV_API_TARGET='http://127.0.0.1:8202'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5182 --strictPort
```

打开 `http://127.0.0.1:5182`。默认是 V2，`?version=1` 打开保留的 V1 界面。本地原有演示账号为运营A、观察E，密码 demo；这是本机预览认证，不是生产 SSO。

V2 不依赖 V1 的前端演示数据开关，始终读取真实 API。数据库在 `data/v2/agent.sqlite3`，不覆盖 V1 的分析库与创意记录。

## 模型和任务

凭证沿用现有 `L4_intelligence.intelligence.llm.provider_key` 的配置读取方式，不存入研究档案或前端。默认继承现有模型；可在「目标与自动研究」里选择已配置提供商的模型，也可在启动前设置 `V2_MODEL`。持久模型选择优先于环境变量。

凭证存在不等于模型调用可用；免费路由实际选择的模型会记入运行。默认免费模型失败可尝试免费路由，不自动切换到收费模型。DeepSeek 使用现有服务额度，当前实测返回 HTTP 402；不会由本程序购买或充值。

Agent 以工具调用查证和交付。每轮最多 8 次模型决策、16 次工具调用和 3 次在线研究补采，研究预算 360 秒；模型响应有总时长限制及最多一次重试。迁移和研究前的渠道采集另列步骤，不混作模型研究成功。进程之间用数据库租约互斥，租约到期后可将旧运行标为中断，保留已完成步骤。

原文里的提示、命令或要求仅当作证据文本，不能授权运行命令。创意必须引用实际读过的内容，直接引文须出现于真实标题或正文；这不等于来源主张已独立核实。

## 数据与采集

实时连接器覆盖 B站游戏榜与搜索、游戏媒体、百度游戏榜、微博热搜、TapTap 发现流和贴吧热点。游戏媒体当前读取机核 RSS、3DM 与游民新闻。每个连接器只声明已实现的能力；详情工具支持 B站视频简介及互动计数、部分游戏媒体正文。评论、视频转录与未实现渠道不会被当作已采到。

V1 的 CSV 按文件签名增量迁移，每个文件最多读取末尾 3000 行，保留原始时间和平台内容身份；117 个来源文件不代表 117 个平台。导入时间、内容发布时间和实际观察时间各自保留。原始 CSV 不被 V2 改写。

TapTap 旧数据中 `crawled_at` 和 `last_seen_at` 含义不同，V2 使用实际最近出现时间，并标记旧迁移的模糊观察点，排除其趋势推算。V1 对全量旧帖统一写时刻的快照未直接当作实际重新采样引入。同内容互动对比使用同指标及实际间隔；计数下降、间隔过短或历史不足时明确返回限制。

研究保存事件版本、证据正文版本与创意。新事件由独立 ID 标识；相同引用不能单独证明事件相同。模型更新事件前须读取历史；相同事件中内容完全一致的提案不会重复保存。语义去重和漏发现质量仍需真实标注基准验收。

## 日常使用

1. 在「目标与自动研究」填入实际增长目标、人群、承接位置、资源和限制。
2. 在「数据与覆盖」查看渠道状态和真实水位，可启动公开线索采集。
3. 输入研究目标启动 Agent，查看进度、工具结果和失败原因。
4. 查看事件的来源引文、推断、未知、历史变化与互动观察。
5. 核查创意中的具体人群、位置、动作、文案、步骤、素材使用条件及验证方法，记录采用、调整或拒绝的原因与实际执行结果。
6. 生成近日或近七天行动简报并下载 Markdown。历史不足会明确说明，不生成虚构曲线或增长结果。

自动研究默认关闭。启用后仅在后端运行期间按照设置间隔采集与研究；关闭后端后不会在系统层面自动唤醒。提案不直接向 TapTap 发布，记录采用也不等于实际执行。

## 命令行

```powershell
python -X utf8 -m agent_v2 ingest
python -X utf8 -m agent_v2 collect
python -X utf8 -m agent_v2 status
python -X utf8 -m agent_v2 research --live --task '研究近期玩家需求及可由 TapTap 社区承接的增长机会'
python -X utf8 -m agent_v2 brief --days 7
```

接口统一为 `/api/v2/`：overview、candidates、ingest、collection、runs、events、brief、feedback、context、model、schedule。读接口需要登录，写接口要求运营或管理员角色。单纯刷新不会更新数据观察时间。

## 验证

```powershell
python -X utf8 -m unittest agent_v2.tests.test_core agent_v2.tests.test_operations agent_v2.tests.test_model runtime.tests.test_pipeline_health L4_intelligence.tests.test_creative_delivery -q
node spatial/node_modules/typescript/bin/tsc --project spatial/tsconfig.json --noEmit
```

验收必须区分代码测试、真实采集、真实模型交付及业务效果。现有免费接口当天额度已耗尽，现有 DeepSeek 返回 HTTP 402；当前真实模型验收未通过，不能把 53 项测试或采集成功作为准确率与增长效果证明。
