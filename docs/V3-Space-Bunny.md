# V3 太空兔 API 接入

版本：3.0.0-alpha.5，2026-10-07。

用户提供的 API 文档指定 `POST https://spacebunny.app/api/v1/chat/completions`，实际模型名为 `stealth/space-bunny-alpha`。本机系统环境变量 `space_bunney_free_api_key` 已存在，适配器直接在服务端读取；也支持 `SPACE_BUNNY_API_KEY`。先读进程环境，Windows 进程未继承时只读系统/用户注册表，不修改环境、不写密钥文件、不把密钥传给浏览器或模型提示词。

## 当前实测

2026-10-07 两次受控真实请求均使用 low，输出预算分别为 256 和 512 token；均收到 HTTP 502。首个请求约 2.11 秒结束，未获得模型回复、用量或费用。公开模型列表两个候选路径均返回 404，不能通过目录验证价格。这些结果证明本轮接口不可用，不能说明密钥已通过验证，也不能直接证明永久停服或推理强度有问题。

当前模型列表已加入“太空兔 · Space Bunny Alpha”，配置状态为“已配置凭证”，不是服务可用认证。本轮没有新增 AI 情报、规划、素材草案或自动增长创意；自动增长创意仍为 0。生产选择仍是原来的 Big Pickle，Zen 免费额度暂停继续保留，没有自动启用未验收的接口或切换其他模型。

## 使用和交付

在“业务与自动运行”保存太空兔模型后，可设置 low、medium、high、xhigh、max 或模型默认。推理设置单独保存在 `space_bunny_reasoning_effort`，与 Ling 设置互不影响；默认 low，模型默认不传 reasoning。尚未保存的新模型不能保存推理配置，避免修改到另一提供商。

本机模型配置标识为 `spacebunny/space-bunny-alpha`，请求始终固定使用用户指定官网及 `stealth/space-bunny-alpha`，禁止请求重定向，不自动更换模型。客户端使用文档明确支持的 `response_format: json_object`，把当前任务与 JSON 契约一起提交；响应由本地 JSON Schema 和业务规则继续校验，没有声称服务端支持强制 JSON Schema。

沿用 Alpha 4 的紧凑来源包、逐字引用映射、情报机会路由，以及“创意规划 → 文案/脚本制作”的阶段和总预算。接口暂只发送文本证据，未接入多模态输入。模型不能直接执行工具、读取本机文件或修改数据，业务校验后才保存交付。

记录实际 API 模型、请求编号、提供商、耗时、来源包、用量及费用状态。太空兔记录标为 `space-bunny-api`，没有 OpenCode 会话；不能用 OpenCode 原生任务的标签标记官网调用。内部推理内容不展示，推理 token 可包含在 completion 用量内，不重复相加。结构缺失进入有限业务修正，身份/参数错误、限流和上游失败独立退避，任务与规划保留。

## 计费与限制

提供的文档将当前预览描述为零价格，但 [官网计费页](https://spacebunny.app/pricing) 同时出售积分包，不能从模型宣传或变量名判断本账号 API 调用全部免费。[官方 API 文档](https://spacebunny.app/docs) 说明响应费用信息可选。

响应不提供费用时，记录 `reported_cost: null / cost_status: unknown`；明确报告零费用才记录 `reported_zero`，不把未知写成零。报告非零或无效费用时停止后续模型调用，不购买积分或使用别的模型。暂无通过官方账号接口自动核对余额/积分的实现，生产使用前仍需确认账户用量条件；本轮两次失败没有取得可核对的费用字段。

## 本地验证

```powershell
python -X utf8 -m agent_v3.space_bunny status
python -X utf8 -m agent_v3.space_bunny probe
```

`probe` 是真实请求，会占用服务用量。本轮两次 502 已保存到 `.toolchain/v3-alpha5-space-bunny-live.json`，不继续重复探测。

138 项本地检查、TypeScript、生产构建与浏览器验证通过。新增检查验证凭证隔离、固定路由、不跟随重定向、JSON 交付修正、模型和截断拦截、费用未知/非零、独立推理与退避，以及情报到创意的提供商记录。流程使用内存测试库与脚本响应，只证明代码机制，不能代替真实模型和创意质量验收。
