# V3 Alpha 8：DeepSeek Flash 接入与响应处理

记录日期：2026-10-08。新增官方 DeepSeek Chat Completions 接入；系统持续积累情报、来源素材和增长创意的目标与来源校验保留。

## 当前模型与提供商

服务端仅从 `DEEPSEEK_API_KEY` / 已有 `LLM_API_KEY` 读取凭证，进程缺失时可读取 Windows 系统或用户环境变量；不把凭证交给浏览器，不复制到版本归档，不使用太空兔或 Zen 的凭证。请求只发往 `https://api.deepseek.com/chat/completions`，禁止重定向；不自动切换其他模型。

本次认证 GET `/models` 返回 `deepseek-flash`，显示名称 `DeepSeek-V4.1-Flash`，推理档位 low/high/max。官方文档说明旧名 `deepseek-v4-flash` 仍接受，但实际请求由 V4.1 Flash 承接。因此生产选择 `deepseek-flash`，运行记录保存请求与响应模型名；不声称实际调用了已退役的 V4 Flash。模型版本以后仍以真实接口为准。[官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)。

DeepSeek 按 token 计费。响应没有返回实际费用时记录 `reported_cost=null`、`cost_status=provider_billed_not_reported`，不把它算作零费用。Zen 额度暂停与 DeepSeek 的设置、失败退避独立。

## 最终输出与推理字段

Chat Completions 的字段关系为：

```python
message = response["choices"][0]["message"]
final_content = message["content"]        # 最终回答，JSON 交付解析这里
# message["reasoning_content"]            # 推理内容，不能作为事实、引用或成果
result = json.loads(final_content)
```

这里是 `content`，不是 `context`；项目自己的 `business_context` 是业务输入。真正解析前检查响应结构、结束原因、最终 content 类型及 JSON Schema。JSON 输出使用 `response_format={"type":"json_object"}`，提示同时明确 JSON 和业务 Schema；业务 Schema 在本地校验，不能把 API 的合法 JSON 保证等同于业务正确。[官方 JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。

默认的 V3 紧凑任务不带 tools，最终 `content` 独立解析。推理原文不写入任务输入/结果文件、SQLite、页面情报、素材、创意、报告或修正提示。仅保留推理是否存在、推理 token 数、模型、请求编号、时长、输出预算和结束状态。推理 token 是 completion token 的子集，不额外加到 total token 上。

兼容工具循环是一个独立情形：官方接口要求带 tools 的思考请求回传历史助手的 `reasoning_content`。V2 与 V3 的兼容循环通过私有 `_continuation` 和 `assistant_history` 仅在内存中单独回传，仍从 `tool_calls[].function.arguments` 读取工具参数；业务日志使用字段白名单，不保存私有继续上下文。这不是拿推理当情报。[官方思考与工具调用规则](https://api-docs.deepseek.com/guides/thinking_mode/)。

## 推理强度和输出预算

DeepSeek 设置独立持久化，支持关闭思考、低、高、最大、模型默认。关闭使用 `thinking.type=disabled`；其他使用 enabled，low/high/max 通过顶层 `reasoning_effort` 传入。模型默认不传 effort，目前供应商默认为 high。没有伪造“中”档，也不发送 OpenRouter 的 reasoning 对象、思考模式无效的 temperature 或不兼容的 tool_choice。

生产默认 low，单次输出总上限 16,384 token，可在页面调整 256–32,768。上限包含思考和最终回答。低档的基础预算：研究计划 2,048、事件关系 4,096、情报与创意计划 8,192、文案/脚本制作 12,288。高/模型默认为基础预算两倍，最大三倍，始终受用户总上限约束；不能保证最终正文剩余固定数量的 token。

每次请求保留阶段剩余总时间限制、60 秒内首响应/无数据等待、两 MB 响应大小限制。连接中断明确记录失败。`length`、空最终 content、拒绝/中断、错误 JSON 或不符合业务 Schema 均不会计为完成，不从 reasoning_content 或 context 补造结果；按原业务流程最多一次修正，任务保留。已取得响应但交付失败时，真实 usage 仍计入运行，避免把失败请求算成没有消耗。

## 真实验证与范围

小请求在 low 档通过，输出预算 512，正常 `stop`：prompt 156、completion 218、total 374，其中 reasoning 203；最终 JSON 为 ready=true。

首个完整任务 `run_a06dbf954edf4afca4ff65d563d4e10e` 因初版首响应等待 15 秒超时，保留为实际失败记录；没有获得 usage，费用未知。调整为 60 秒后，仅让该次自身连接失败退避到期并复验，未解除 Zen 暂停。

复验 `run_2ececd2b1aab44febed5cfab68317ee0` 使用真实候选 `topic_4acf9d485e97c4fdcc3b`，“成都：国庆假期解锁城市文旅新图景”。调用约 25.97 秒，low 档、阶段预算 8,192，正常 stop；prompt 5,102、completion 4,160、total 9,262，其中 reasoning 2,961。本地 Schema 与来源引文校验通过，没有修正回合；新增一份独立情报、一项来源摘录和一项推断表达模式。模型区分新闻发布与热度，因缺少真实讨论等证据判为 watch，没有生成创意。此前失败与其费用未知状态保留。

记录时独立情报 2、表达/创作素材 9、来源素材记录 957、公开评论样本 35、自动增长创意 0、旧辅助创意 1。这些来源与样本数量来自累计运行，不是本次 DeepSeek 新采集，更不代表完整全网覆盖。一次正常情报交付不等于增长创意质量或整体产品目标已验收。

17 项新增检查覆盖隔离解析、格式、结束状态、强度、预算、费用未知、失败 usage、私有工具继续上下文、实际 Provider 路由、设置权限及任务锁。合并既有检查共 260 项通过，TypeScript 和 674 模块生产构建通过。真实页面验证 DeepSeek/low/16,384 设置、五个推理选项、持续观察情报、手机宽度与运行结果无 reasoning_content；页面报错为零。源码与运行库继续独立归档，保留旧版本，不删除文件。
