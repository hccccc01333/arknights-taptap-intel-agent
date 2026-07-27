# Agent Skills

本目录按 [Agent Skills](https://agentskills.io) 组织：每个子目录含 `SKILL.md`，及可选的 `references/` / `assets/`。

这是本仓库 **唯一** 的 Skill 源目录。

## 列表

| Skill | 作用 |
|-------|------|
| [`arknights-taptap-crawl`](./arknights-taptap-crawl/SKILL.md) | TapTap 爬虫字段契约 |
| [`arknights-llm-annotate-v14`](./arknights-llm-annotate-v14/SKILL.md) | 标注两段式 v1.4 |
| [`arknights-taptap-yuqing-report`](./arknights-taptap-yuqing-report/SKILL.md) | 日/周报四问法 |
| [`arknights-cross-channel-facts`](./arknights-cross-channel-facts/SKILL.md) | 跨渠 facts 锁数 |

## 使用

将对应 skill 目录加入 Agent 上下文，或执行：

```bash
python scripts/sync_agent_skills.py
```

构建说明：[`docs/构建说明.md`](../docs/构建说明.md)。
